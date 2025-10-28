#!/usr/bin/env python

"""Utility to validate charm repositories and discover missing charms."""

import asyncio
import csv
import dataclasses
import json
import logging
import typing
import urllib.parse

import click
import httpx
import rich.logging
import rich.console
import rich.table

logger = logging.getLogger(__name__)


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
class CharmhubCharm:
    """Information about a charm from Charmhub."""

    name: str
    source_url: str | None
    canonical_repo_name: str | None


async def check_repository_status(
    client: httpx.AsyncClient, repository: str
) -> tuple[int | None, bool]:
    """Check if a repository exists and if it's archived.

    Returns (status_code, is_archived).
    """
    if repository.startswith("git@github.com:"):
        http_url = repository.replace("git@github.com:", "https://github.com/")
    else:
        http_url = repository
    http_url = http_url.rstrip("/").removesuffix(".git")

    try:
        response = await client.head(http_url, follow_redirects=True)
        if response.status_code == 404:
            return 404, False
        elif response.status_code != 200:
            logger.warning(
                "Unexpected status %d for %s", response.status_code, http_url
            )
            return response.status_code, False

        if "github.com" in http_url:
            parsed = urllib.parse.urlparse(http_url)
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 2:
                owner, repo = path_parts[0], path_parts[1]
                api_url = f"https://api.github.com/repos/{owner}/{repo}"

                try:
                    api_response = await client.get(api_url)
                    if api_response.status_code == 200:
                        repo_data = api_response.json()
                        return response.status_code, repo_data.get("archived", False)
                    else:
                        logger.debug(
                            "GitHub API request failed for %s: %d",
                            api_url,
                            api_response.status_code,
                        )
                except Exception as e:
                    logger.debug(
                        "Failed to check archive status for %s: %s", http_url, e
                    )

        return response.status_code, False

    except httpx.RequestError as e:
        logger.warning("Request failed for %s: %s", http_url, e)
        return None, False


async def validate_charm(
    client: httpx.AsyncClient, charm: CharmInfo
) -> ValidationResult:
    """Validate a single charm repository."""
    if not charm.repository:
        return ValidationResult(
            charm=charm, is_valid=False, error_message="No repository URL provided"
        )

    status_code, is_archived = await check_repository_status(client, charm.repository)

    if status_code == 404:
        return ValidationResult(
            charm=charm,
            is_valid=False,
            error_message="Repository not found (404)",
            status_code=404,
        )
    elif is_archived:
        return ValidationResult(
            charm=charm,
            is_valid=False,
            error_message="Repository is archived",
            is_archived=True,
            status_code=status_code,
        )
    elif status_code and status_code >= 400:
        return ValidationResult(
            charm=charm,
            is_valid=False,
            error_message=f"HTTP error: {status_code}",
            status_code=status_code,
        )
    else:
        return ValidationResult(
            charm=charm, is_valid=True, error_message=None, status_code=status_code
        )


async def get_charmhub_packages() -> list[dict]:
    """Get the list of published charms from Charmhub."""
    logger.info("Fetching the list of published charms from charmhub")
    url = "https://charmhub.io/packages.json"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        return data["packages"]


async def get_charm_source_url(
    client: httpx.AsyncClient, charm_name: str
) -> str | None:
    """Get the source URL for a charm from charmhub."""
    logger.debug("Looking for source URL for %s", charm_name)

    try:
        url = f"https://api.charmhub.io/v2/charms/info/{charm_name}?fields=result.links"
        response = await client.get(url, timeout=30)
        if response.status_code == 200:
            data = response.json()
            links = data.get("result", {}).get("links", {})
            source_links = links.get("source", [])
            if source_links:
                return source_links[0]
    except Exception as e:
        logger.debug("Failed to get source URL for %s: %s", charm_name, e)

    try:
        url = f"https://api.charmhub.io/v2/charms/info/{charm_name}?fields=result.bugs-url"
        response = await client.get(url, timeout=30)
        if response.status_code == 200:
            data = response.json()
            bugs_url = data.get("result", {}).get("bugs-url")
            if bugs_url:
                return bugs_url
    except Exception as e:
        logger.debug("Failed to get bugs-url for %s: %s", charm_name, e)

    logger.debug("Could not find source URL for %s", charm_name)
    return None


def extract_canonical_repo_name(url: str | None) -> str | None:
    """Extract the repository name from a GitHub URL if it's a Canonical repository."""
    if not url:
        return None

    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc != "github.com":
            return None

        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) < 2:
            return None

        owner, repo = path_parts[0], path_parts[1]

        if owner != "canonical":
            return None

        return repo
    except Exception:
        return None


async def get_charmhub_charms() -> list[CharmhubCharm]:
    """Get all charms from charmhub with their source URLs."""
    packages = await get_charmhub_packages()

    charms = []

    async with httpx.AsyncClient() as client:
        semaphore = asyncio.Semaphore(10)  # Limit concurrent requests

        async def fetch_charm_info(package):
            async with semaphore:
                source_url = await get_charm_source_url(client, package["name"])
                canonical_repo_name = extract_canonical_repo_name(source_url)

                return CharmhubCharm(
                    name=package["name"],
                    source_url=source_url,
                    canonical_repo_name=canonical_repo_name,
                )

        tasks = [fetch_charm_info(package) for package in packages]
        charms = await asyncio.gather(*tasks)

    canonical_charms = [charm for charm in charms if charm.canonical_repo_name]

    logger.info(
        "Found %d charms on charmhub, %d are Canonical charms with source repositories",
        len(charms),
        len(canonical_charms),
    )

    return canonical_charms


def load_csv_charms(csv_file_path: str) -> list[CharmInfo]:
    """Load charms from CSV file."""
    charms = []

    with open(csv_file_path, "r") as csv_file:
        reader = csv.DictReader(csv_file)

        for row in reader:
            if not row or not row.get("Repository"):
                continue

            charm = CharmInfo(
                team=row.get("Team", "").strip(),
                name=row.get("Charm Name", "").strip(),
                repository=row.get("Repository", "").strip(),
                key_charm=row.get("Key Charm for this Team", "").strip().upper()
                == "TRUE",
                branch=row.get("Branch (if not the default)", "").strip() or None,
                notes=row.get("Notes", "").strip(),
            )

            if charm.name and charm.repository:
                charms.append(charm)

    return charms


def extract_repo_name_from_url(url: str) -> str | None:
    """Extract repository name from a URL."""
    if not url:
        return None

    if url.startswith("git@github.com:"):
        url = url.replace("git@github.com:", "https://github.com/")

    try:
        parsed = urllib.parse.urlparse(url)
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) >= 2:
            return path_parts[1].removesuffix(".git")
    except Exception:
        pass

    return None


def find_missing_charms(
    csv_charms: list[CharmInfo], charmhub_charms: list[CharmhubCharm]
) -> list[CharmhubCharm]:
    """Find charms publicly listed on Charmhub that are not in the CSV."""
    csv_repo_names = set()
    for charm in csv_charms:
        repo_name = extract_repo_name_from_url(charm.repository)
        if repo_name:
            csv_repo_names.add(repo_name)

    missing_charms = []
    for charmhub_charm in charmhub_charms:
        if charmhub_charm.canonical_repo_name not in csv_repo_names:
            missing_charms.append(charmhub_charm)

    return missing_charms


def add_missing_charms_to_csv(
    csv_file_path: str,
    existing_charms: list[CharmInfo],
    missing_charms: list[CharmhubCharm],
) -> int:
    """Add missing charms to the CSV file.

    Returns count of added charms.
    """
    if not missing_charms:
        return 0

    with open(csv_file_path, "r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames
        existing_rows = list(reader)

    new_rows = []
    for charm in missing_charms:
        new_row = {}
        if "Team" in fieldnames:
            new_row["Team"] = ""  # We can't easily figure this out automatically.
        if "Charm Name" in fieldnames:
            new_row["Charm Name"] = charm.name
        if "Repository" in fieldnames:
            new_row["Repository"] = charm.source_url or ""
        if "Key Charm for this Team" in fieldnames:
            new_row["Key Charm for this Team"] = "FALSE"
        if "Branch" in fieldnames:
            new_row["Branch"] = ""
        elif "Branch (if not the default)" in fieldnames:
            new_row["Branch (if not the default)"] = ""
        if "Notes" in fieldnames:
            new_row["Notes"] = "Added automatically from Charmhub"
        new_rows.append(new_row)

    with open(csv_file_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)
        writer.writerows(new_rows)

    return len(new_rows)


def print_validation_results(
    results: list[ValidationResult], console: rich.console.Console
):
    """Print validation results in a formatted table."""
    invalid_results = [r for r in results if not r.is_valid]

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
):
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
            charm.canonical_repo_name or "Unknown",
            charm.source_url or "Not found",
        )

    console.print(table)


async def validate_all_charms(charms: list[CharmInfo]) -> list[ValidationResult]:
    """Validate all charms concurrently."""
    logger.info("Validating %d charms...", len(charms))

    async with httpx.AsyncClient(timeout=30) as client:
        semaphore = asyncio.Semaphore(10)

        async def validate_with_limit(charm):
            async with semaphore:
                return await validate_charm(client, charm)

        tasks = [validate_with_limit(charm) for charm in charms]
        results = await asyncio.gather(*tasks)

    return results


@click.command()
@click.argument("charm-list", type=click.Path(exists=True))
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
def main(
    charm_list: str,
    validate_only: bool,
    missing_only: bool,
    add_missing: bool,
    output_format: str,
):
    """
    Validate charm repositories and find missing charms from Charmhub.

    This tool checks that all repositories in the CSV are accessible and not archived,
    and optionally finds charms published on Charmhub that are not in the CSV file.

    With --add-missing, missing charms can be automatically added to the CSV file.
    """
    FORMAT = "%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[rich.logging.RichHandler()],
    )

    console = rich.console.Console()

    charms = load_csv_charms(charm_list)
    console.print(f"Loaded {len(charms)} charms from CSV")

    async def run_validation():
        results = []
        missing_charms = []

        if not missing_only:
            results = await validate_all_charms(charms)

        if not validate_only:
            console.print("Fetching charms from charmhub...")
            charmhub_charms = await get_charmhub_charms()
            missing_charms = find_missing_charms(charms, charmhub_charms)

        return results, missing_charms

    validation_results, missing_charms = asyncio.run(run_validation())

    if add_missing and missing_charms:
        try:
            count = add_missing_charms_to_csv(charm_list, charms, missing_charms)
            console.print(
                f"\nAdded {count} missing charms to {charm_list}", style="green"
            )
        except Exception as e:
            console.print(f"\nFailed to update CSV: {e}", style="red")
            exit(1)

    if output_format == "json":
        output = {}

        if validation_results:
            output["validation_results"] = [
                {
                    "charm_name": r.charm.name,
                    "repository": r.charm.repository,
                    "team": r.charm.team,
                    "is_valid": r.is_valid,
                    "error_message": r.error_message,
                    "is_archived": r.is_archived,
                    "status_code": r.status_code,
                }
                for r in validation_results
            ]

        if missing_charms:
            output["missing_charms"] = [
                {
                    "charm_name": c.name,
                    "repository_name": c.canonical_repo_name,
                    "source_url": c.source_url,
                }
                for c in missing_charms
            ]

        console.print(json.dumps(output, indent=2))

    else:
        if validation_results:
            print_validation_results(validation_results, console)
            console.print()

        if missing_charms:
            print_missing_charms(missing_charms, console)

    if validation_results and any(not r.is_valid for r in validation_results):
        exit(1)


if __name__ == "__main__":
    main()
