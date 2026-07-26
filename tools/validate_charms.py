#!/usr/bin/env python3

"""Utility to validate charm repositories and discover missing charms."""

import asyncio
import csv
import dataclasses
import json
import logging
import os
import pathlib
import sys
import urllib.parse

import click
import httpx
import rich.console
import rich.table
from helpers import configure_logging

logger = logging.getLogger(__name__)

CHARMHUB_PACKAGES_URL = "https://charmhub.io/packages.json"
CHARMHUB_INFO_URL = "https://api.charmhub.io/v2/charms/info"
GITHUB_API_URL = "https://api.github.com"

#: Number of requests that are allowed to be in flight at the same time.
DEFAULT_CONCURRENCY = 10
#: Timeout (seconds) for a single HTTP request.
REQUEST_TIMEOUT = 30
#: Timeout (seconds) for fetching the (large) list of Charmhub packages.
PACKAGE_LIST_TIMEOUT = 120


@dataclasses.dataclass
class CharmInfo:
    """Information about a charm from the CSV input file."""

    team: str
    name: str
    repository: str
    key_charm: bool
    branch: str | None
    notes: str


@dataclasses.dataclass
class ValidationResult:
    """Result of validating a charm repository."""

    charm: CharmInfo
    is_valid: bool
    error_message: str | None
    is_archived: bool = False
    status_code: int | None = None


@dataclasses.dataclass
class RepositoryStatus:
    """The state of a repository, as far as we are able to determine it."""

    #: The HTTP status code of the check, or None if no response was received.
    status_code: int | None
    is_archived: bool = False
    #: Set when the repository exists but the requested branch does not.
    missing_branch: str | None = None
    #: Set when the repository has been renamed/moved (informational only).
    renamed_to: str | None = None
    #: Set when the check could not be made at all, e.g. an unusable URL.
    error_message: str | None = None


@dataclasses.dataclass
class CharmhubCharm:
    """Information about a charm from Charmhub."""

    name: str
    source_url: str | None
    repo_name: str | None


def normalise_repository_url(url: str | None) -> str | None:
    """Return a canonical `https://host/path` form of a repository URL.

    Handles the `git@host:owner/name.git` (scp-like) syntax, `git+` prefixes,
    non-https schemes, `www.` prefixes, trailing slashes, and the `.git`
    suffix, so that URLs that refer to the same repository compare equal.

    Returns None if the URL is empty or cannot be understood.
    """
    if not url:
        return None
    url = url.strip().removeprefix("git+")
    if not url:
        return None
    if "://" not in url and "@" in url and ":" in url:
        # scp-like syntax, e.g. git@github.com:canonical/operator.git
        user_host, _, path = url.partition(":")
        host = user_host.rpartition("@")[2]
        url = f"https://{host}/{path}"
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        logger.debug("Failed to parse repository URL %r: %s", url, exc)
        return None
    # Drop any credentials and the default port from the host.
    host = parsed.netloc.rpartition("@")[2].lower()
    host = host.removesuffix(":443").removesuffix(":22").removeprefix("www.")
    path = parsed.path.rstrip("/").removesuffix(".git")
    if not host or not path:
        return None
    return f"https://{host}{path}"


def repository_identity(url: str | None) -> str | None:
    """Return a comparable identity (`host/owner/name`) for a repository URL.

    For the well-known forges only the first two path components are
    significant, so URLs that point at a branch, a subfolder, or the issue
    tracker of a repository all map to the same identity.
    """
    normalised = normalise_repository_url(url)
    if not normalised:
        return None
    parsed = urllib.parse.urlparse(normalised)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.netloc in ("github.com", "gitlab.com"):
        if len(parts) < 2:
            return None
        parts = parts[:2]
    if not parts:
        return None
    return f"{parsed.netloc}/{'/'.join(parts)}".lower()


def extract_repo_name(url: str | None) -> str | None:
    """Extract the repository name from a repository (or issue tracker) URL."""
    identity = repository_identity(url)
    if not identity:
        return None
    return identity.rpartition("/")[2]


def github_owner_repo(url: str | None) -> tuple[str, str] | None:
    """Return the (owner, repository) pair for a GitHub URL, if it is one."""
    identity = repository_identity(url)
    if not identity:
        return None
    host, _, path = identity.partition("/")
    if host != "github.com":
        return None
    owner, _, repo = path.partition("/")
    if not owner or not repo:
        return None
    return owner, repo


def _github_headers() -> dict[str, str]:
    """Return the headers to use for GitHub API requests.

    Unauthenticated requests are limited to 60/hour, which is not enough to
    check a list of charms, so use a token if one is available.
    """
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _is_rate_limited(response: httpx.Response) -> bool:
    """Report whether a GitHub API response indicates that we are rate limited."""
    if response.status_code not in (403, 429):
        return False
    return response.headers.get("x-ratelimit-remaining") == "0" or (
        "rate limit" in response.text.lower()
    )


async def _check_url_exists(
    client: httpx.AsyncClient, url: str
) -> RepositoryStatus | None:
    """Check that a URL can be fetched, without using any forge-specific API.

    Returns None if the request could not be made at all.
    """
    try:
        response = await client.head(url, follow_redirects=True)
        if response.status_code in (405, 501):
            # Some servers do not implement HEAD.
            response = await client.get(url, follow_redirects=True)
    except httpx.RequestError as exc:
        logger.warning("Request failed for %s: %s", url, exc)
        return None
    if response.status_code >= 400 and response.status_code != 404:
        logger.warning("Unexpected status %d for %s", response.status_code, url)
    return RepositoryStatus(status_code=response.status_code)


async def _check_github_repository(
    client: httpx.AsyncClient, owner: str, repo: str, branch: str | None
) -> RepositoryStatus | None:
    """Check a GitHub repository (and optionally a branch) via the GitHub API.

    Returns None if the API could not be used, in which case the caller should
    fall back to a plain HTTP check.
    """
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}"
    try:
        response = await client.get(url, headers=_github_headers())
    except httpx.RequestError as exc:
        logger.warning("Request failed for %s: %s", url, exc)
        return None
    if _is_rate_limited(response):
        logger.warning(
            "GitHub API rate limit reached; falling back to a plain HTTP check "
            "(archived repositories will not be detected). Set GITHUB_TOKEN to "
            "raise the limit."
        )
        return None
    if response.status_code == 404:
        return RepositoryStatus(status_code=404)
    if response.status_code != 200:
        logger.debug("GitHub API request failed for %s: %d", url, response.status_code)
        return None
    try:
        data = response.json()
    except ValueError as exc:
        logger.debug("Invalid JSON from %s: %s", url, exc)
        return None

    full_name = data.get("full_name")
    renamed_to = None
    if full_name and full_name.lower() != f"{owner}/{repo}".lower():
        renamed_to = full_name
        logger.info("Repository %s/%s has been renamed to %s", owner, repo, full_name)

    status = RepositoryStatus(
        status_code=200,
        is_archived=bool(data.get("archived", False)),
        renamed_to=renamed_to,
    )
    if branch and not status.is_archived:
        status.missing_branch = await _check_github_branch(client, owner, repo, branch)
    return status


async def _check_github_branch(
    client: httpx.AsyncClient, owner: str, repo: str, branch: str
) -> str | None:
    """Return the branch name if it does not exist on GitHub, otherwise None."""
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/branches/{urllib.parse.quote(branch)}"
    try:
        response = await client.get(url, headers=_github_headers())
    except httpx.RequestError as exc:
        logger.debug("Failed to check branch %s of %s/%s: %s", branch, owner, repo, exc)
        return None
    if response.status_code == 404 and not _is_rate_limited(response):
        return branch
    return None


async def check_repository_status(
    client: httpx.AsyncClient, repository: str, branch: str | None = None
) -> RepositoryStatus | None:
    """Check whether a repository exists, is archived, and has the given branch.

    Returns None if the repository could not be reached at all.
    """
    url = normalise_repository_url(repository)
    if not url:
        logger.warning("Unable to understand repository URL: %r", repository)
        return RepositoryStatus(
            status_code=None, error_message="Not a usable repository URL"
        )

    owner_repo = github_owner_repo(url)
    if owner_repo:
        status = await _check_github_repository(client, *owner_repo, branch)
        if status is not None:
            return status
    return await _check_url_exists(client, url)


async def validate_charm(
    client: httpx.AsyncClient, charm: CharmInfo
) -> ValidationResult:
    """Validate a single charm repository."""
    if not charm.repository:
        return ValidationResult(
            charm=charm, is_valid=False, error_message="No repository URL provided"
        )

    status = await check_repository_status(client, charm.repository, charm.branch)
    if status is None or status.status_code is None:
        message = "Request failed (no response)"
        if status is not None and status.error_message:
            message = status.error_message
        return ValidationResult(charm=charm, is_valid=False, error_message=message)
    if status.status_code == 404:
        return ValidationResult(
            charm=charm,
            is_valid=False,
            error_message="Repository not found (404)",
            status_code=404,
        )
    if status.is_archived:
        return ValidationResult(
            charm=charm,
            is_valid=False,
            error_message="Repository is archived",
            is_archived=True,
            status_code=status.status_code,
        )
    if status.status_code >= 400:
        return ValidationResult(
            charm=charm,
            is_valid=False,
            error_message=f"HTTP error: {status.status_code}",
            status_code=status.status_code,
        )
    if status.missing_branch:
        return ValidationResult(
            charm=charm,
            is_valid=False,
            error_message=f"Branch not found: {status.missing_branch}",
            status_code=status.status_code,
        )
    return ValidationResult(
        charm=charm, is_valid=True, error_message=None, status_code=status.status_code
    )


async def validate_all_charms(
    charms: list[CharmInfo], concurrency: int = DEFAULT_CONCURRENCY
) -> list[ValidationResult]:
    """Validate all charms concurrently."""
    logger.info("Validating %d charms...", len(charms))

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        semaphore = asyncio.Semaphore(concurrency)

        async def validate_with_limit(charm: CharmInfo) -> ValidationResult:
            async with semaphore:
                return await validate_charm(client, charm)

        return list(await asyncio.gather(*(validate_with_limit(c) for c in charms)))


async def get_charmhub_packages() -> list[dict]:
    """Get the list of published charms from Charmhub."""
    logger.info("Fetching the list of published charms from charmhub")
    async with httpx.AsyncClient(timeout=PACKAGE_LIST_TIMEOUT) as client:
        response = await client.get(CHARMHUB_PACKAGES_URL)
        response.raise_for_status()
        data = response.json()
    packages = data.get("packages")
    if not isinstance(packages, list):
        raise ValueError(f"Unexpected response from {CHARMHUB_PACKAGES_URL}")
    # packages.json also contains bundles, which have no charm repository.
    return [
        package
        for package in packages
        if package.get("name") and package.get("type", "charm") != "bundle"
    ]


async def get_charm_source_url(
    client: httpx.AsyncClient, charm_name: str
) -> str | None:
    """Get the source URL for a charm from charmhub.

    Falls back to the bug tracker URL, which is generally in the same
    repository, when no source URL is published.
    """
    logger.debug("Looking for source URL for %s", charm_name)
    url = f"{CHARMHUB_INFO_URL}/{urllib.parse.quote(charm_name)}"
    try:
        response = await client.get(
            url,
            params={"fields": "result.links,result.bugs-url"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            logger.debug(
                "Charmhub info for %s: HTTP %d", charm_name, response.status_code
            )
            return None
        result = response.json().get("result") or {}
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.debug("Failed to get source URL for %s: %s", charm_name, exc)
        return None

    source_links = (result.get("links") or {}).get("source") or []
    if source_links:
        return source_links[0]
    bugs_url = result.get("bugs-url")
    if bugs_url:
        return bugs_url

    logger.debug("Could not find source URL for %s", charm_name)
    return None


async def get_charmhub_charms(
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[CharmhubCharm]:
    """Get all charms from charmhub that have a known source repository."""
    packages = await get_charmhub_packages()

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_charm_info(package: dict) -> CharmhubCharm:
            async with semaphore:
                source_url = await get_charm_source_url(client, package["name"])
                return CharmhubCharm(
                    name=package["name"],
                    source_url=source_url,
                    repo_name=extract_repo_name(source_url),
                )

        charms = await asyncio.gather(*(fetch_charm_info(p) for p in packages))

    charms_with_source = [charm for charm in charms if charm.source_url]
    logger.info(
        "Found %d charms on charmhub, %d have source repositories",
        len(charms),
        len(charms_with_source),
    )
    return charms_with_source


def _field(row: dict[str, str | None], *names: str) -> str:
    """Return the first non-empty value from the named CSV columns.

    `csv.DictReader` fills in missing columns with None, so this also protects
    against short rows.
    """
    for name in names:
        value = row.get(name)
        if value:
            return value.strip()
    return ""


def load_csv_charms(csv_file_path: pathlib.Path) -> list[CharmInfo]:
    """Load charms from the CSV file, skipping blank rows."""
    charms: list[CharmInfo] = []
    # Charms often share a repository (monorepos), but a repeated name is a
    # mistake in the CSV.
    seen_names: set[str] = set()

    with pathlib.Path(csv_file_path).open(
        newline="", encoding="utf-8-sig", errors="replace"
    ) as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            logger.warning("%s does not have a header row", csv_file_path)
            return charms

        for row in reader:
            name = _field(row, "Charm Name", "Name")
            repository = _field(row, "Repository")
            if not name and not repository:
                continue
            if not name:
                logger.warning("The row for %s has no charm name", repository)
            elif name.lower() in seen_names:
                logger.warning("%s appears more than once in the CSV", name)
            seen_names.add(name.lower())
            charm = CharmInfo(
                team=_field(row, "Team"),
                name=name,
                repository=repository,
                key_charm=_field(row, "Key Charm for this Team").upper() == "TRUE",
                branch=_field(row, "Branch (if not the default)", "Branch") or None,
                notes=_field(row, "Notes"),
            )
            charms.append(charm)

    return charms


def find_missing_charms(
    csv_charms: list[CharmInfo], charmhub_charms: list[CharmhubCharm]
) -> list[CharmhubCharm]:
    """Find charms publicly listed on Charmhub that are not in the CSV."""
    csv_identities = {
        identity
        for charm in csv_charms
        if (identity := repository_identity(charm.repository))
    }
    return [
        charm
        for charm in charmhub_charms
        if repository_identity(charm.source_url) not in csv_identities
    ]


def _new_csv_row(header: list[str], charm: CharmhubCharm) -> list[str]:
    """Build a CSV row for a charm, matching the columns of the existing file."""
    values = {
        # We can't easily figure out the team automatically.
        "Team": "",
        "Charm Name": charm.name,
        "Name": charm.name,
        "Repository": charm.source_url or "",
        "Key Charm for this Team": "FALSE",
        "Branch": "",
        "Branch (if not the default)": "",
        "Notes": "Added automatically from Charmhub",
    }
    return [values.get(column, "") for column in header]


def add_missing_charms_to_csv(
    csv_file_path: pathlib.Path, missing_charms: list[CharmhubCharm]
) -> int:
    """Append the missing charms to the CSV file, returning the number added."""
    if not missing_charms:
        return 0

    path = pathlib.Path(csv_file_path)
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.reader(file))
    if not rows:
        raise ValueError(f"{path} is empty, so new rows cannot be aligned with it")

    new_rows = [_new_csv_row(rows[0], charm) for charm in missing_charms]
    # Write to a temporary file first, so that a failure part-way through does
    # not destroy the existing content.
    temp_path = path.with_name(path.name + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerows(rows)
        writer.writerows(new_rows)
    temp_path.replace(path)
    return len(new_rows)


def print_validation_results(
    results: list[ValidationResult], console: rich.console.Console
) -> None:
    """Print validation results in a formatted table."""
    invalid_results = [result for result in results if not result.is_valid]

    if not invalid_results:
        console.print("All charms validated successfully!")
        return

    console.print(f"Found {len(invalid_results)} invalid charms:")

    table = rich.table.Table()
    table.add_column("Charm Name")
    table.add_column("Repository")
    table.add_column("Issue")
    table.add_column("Team")

    for result in invalid_results:
        table.add_row(
            result.charm.name,
            result.charm.repository,
            result.error_message or "Unknown error",
            result.charm.team,
        )

    console.print(table)


def print_missing_charms(
    missing_charms: list[CharmhubCharm], console: rich.console.Console
) -> None:
    """Print missing charms in a formatted table."""
    if not missing_charms:
        console.print("No missing charms found!")
        return

    console.print(f"Found {len(missing_charms)} charms on Charmhub not in CSV:")

    table = rich.table.Table()
    table.add_column("Charm Name")
    table.add_column("Repository Name")
    table.add_column("Source URL")

    for charm in missing_charms:
        table.add_row(
            charm.name,
            charm.repo_name or "Unknown",
            charm.source_url or "Not found",
        )

    console.print(table)


def print_json_report(
    results: list[ValidationResult],
    missing_charms: list[CharmhubCharm],
    validated: bool,
    searched_for_missing: bool,
) -> None:
    """Print the report as JSON on stdout."""
    output: dict[str, list[dict]] = {}
    if validated:
        output["validation_results"] = [
            {
                "charm_name": result.charm.name,
                "repository": result.charm.repository,
                "team": result.charm.team,
                "is_valid": result.is_valid,
                "error_message": result.error_message,
                "is_archived": result.is_archived,
                "status_code": result.status_code,
            }
            for result in results
        ]
    if searched_for_missing:
        output["missing_charms"] = [
            {
                "charm_name": charm.name,
                "repository_name": charm.repo_name,
                "source_url": charm.source_url,
            }
            for charm in missing_charms
        ]
    # Not via rich, which would wrap (and thereby corrupt) long lines.
    click.echo(json.dumps(output, indent=2))


async def collect_report(
    charms: list[CharmInfo], validate: bool, find_missing: bool, concurrency: int
) -> tuple[list[ValidationResult], list[CharmhubCharm]]:
    """Run the requested checks, returning the results and the missing charms."""
    results: list[ValidationResult] = []
    missing_charms: list[CharmhubCharm] = []
    if validate:
        results = await validate_all_charms(charms, concurrency)
    if find_missing:
        charmhub_charms = await get_charmhub_charms(concurrency)
        missing_charms = find_missing_charms(charms, charmhub_charms)
    return results, missing_charms


@click.command()
@click.argument(
    "charm-list", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path)
)
@click.option(
    "--validate-only",
    is_flag=True,
    help="Only validate existing charms, don't check for missing ones",
)
@click.option(
    "--missing-only",
    is_flag=True,
    help="Only check for missing charms, don't validate existing ones",
)
@click.option(
    "--add-missing",
    is_flag=True,
    help="Add missing charms to the CSV file (requires write access)",
)
@click.option(
    "--output-format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
@click.option(
    "--concurrency",
    type=click.IntRange(min=1),
    default=DEFAULT_CONCURRENCY,
    show_default=True,
    help="Maximum number of simultaneous HTTP requests",
)
def main(
    charm_list: pathlib.Path,
    validate_only: bool,
    missing_only: bool,
    add_missing: bool,
    output_format: str,
    concurrency: int,
) -> None:
    """
    Validate charm repositories and find missing charms from Charmhub.

    This tool checks that all repositories in the CSV are accessible and not archived,
    and optionally finds charms published on Charmhub that are not in the CSV file.

    With --add-missing, missing charms can be automatically added to the CSV file.

    Set GITHUB_TOKEN (or GH_TOKEN) to avoid the very low unauthenticated GitHub
    API rate limit, which is needed to detect archived repositories.

    Exits with a non-zero status if any charm fails validation.
    """
    configure_logging()

    if validate_only and missing_only:
        raise click.UsageError("--validate-only and --missing-only are incompatible.")

    # Progress and diagnostics are logged (and so go to stderr), so that
    # --output-format=json produces parsable output on stdout.
    console = rich.console.Console()

    charms = load_csv_charms(charm_list)
    logger.info("Loaded %d charms from %s", len(charms), charm_list)
    if not charms:
        raise click.ClickException(f"No charms found in {charm_list}.")

    validation_results, missing_charms = asyncio.run(
        collect_report(
            charms,
            validate=not missing_only,
            find_missing=not validate_only,
            concurrency=concurrency,
        )
    )

    if add_missing and missing_charms:
        try:
            count = add_missing_charms_to_csv(charm_list, missing_charms)
        except (OSError, ValueError) as exc:
            raise click.ClickException(f"Failed to update CSV: {exc}") from exc
        logger.info("Added %d missing charms to %s", count, charm_list)

    if output_format == "json":
        print_json_report(
            validation_results,
            missing_charms,
            validated=not missing_only,
            searched_for_missing=not validate_only,
        )
    else:
        if not missing_only:
            print_validation_results(validation_results, console)
            console.print()
        if not validate_only:
            print_missing_charms(missing_charms, console)

    if any(not result.is_valid for result in validation_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
