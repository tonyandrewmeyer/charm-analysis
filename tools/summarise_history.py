#! /usr/bin/env python3

"""Summarise the git history of a set of charms."""

import collections
import dataclasses
import datetime
import functools
import logging
import pathlib
import subprocess
from collections.abc import Iterable, Iterator

import click
import rich.console
import rich.table
from helpers import configure_logging, count_and_percentage_table, iter_repositories

logger = logging.getLogger(__name__)

# The separator between the fields of a "git log" line. A tab cannot appear in
# either a date or an email address, so it is safe to split on.
FIELD_SEPARATOR = "\t"

# Recent activity is reported over these windows, in days.
RECENT_WINDOWS = (90, 365)

# Buckets for "how long ago was that?": an upper bound in days, and the label
# to report it as. Anything larger than the last bucket goes in OLDER.
AGE_BUCKETS = (
    (7, "up to a week"),
    (30, "up to a month"),
    (90, "up to 3 months"),
    (180, "up to 6 months"),
    (365, "up to a year"),
    (730, "up to 2 years"),
)
OLDER = "more than 2 years"

# Buckets for "how many commits (or authors) was that?", in the same form.
COUNT_BUCKETS = ((0, "0"), (1, "1"), (5, "2-5"), (20, "6-20"), (50, "21-50"))
MORE = "more than 50"

# How long to wait for any single git command.
GIT_TIMEOUT = 60


@dataclasses.dataclass
class CharmHistory:
    """The history of a single charm."""

    #: The charm, named relative to the folder the search started from.
    name: str
    #: The most recent commit, and the first one, touching the charm.
    last_commit: datetime.datetime
    first_commit: datetime.datetime
    #: Number of commits in each of RECENT_WINDOWS, keyed by the window.
    recent: dict[int, int]
    #: Number of distinct commit authors, by email address.
    authors: int
    #: The checked-out branch of the repository holding the charm.
    branch: str
    #: The repository holding the charm, which is the charm itself unless the
    #: charm is one of several in a monorepo.
    root: pathlib.Path
    #: Whether the clone is shallow, and so has an incomplete history.
    shallow: bool

    @property
    def age(self) -> datetime.timedelta:
        """How long ago the charm was first committed."""
        return self.last_commit - self.first_commit


def git(root: pathlib.Path, *args: str) -> str | None:
    """Run a git command in the repository, returning None if it fails.

    As elsewhere in these tools, a repository that cannot be read is logged and
    skipped rather than aborting the run over 150+ repositories.
    """
    command = ("git", "-C", str(root), *args)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except OSError as e:
        logger.warning("Unable to run %s: %s", " ".join(command), e)
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Timed out running %s", " ".join(command))
        return None
    if result.returncode:
        logger.warning(
            "%s failed (%s): %s",
            " ".join(command),
            result.returncode,
            result.stderr.strip(),
        )
        return None
    return result.stdout


def find_repository_root(charm: pathlib.Path, base: pathlib.Path) -> pathlib.Path | None:
    """Return the git repository that the charm is part of, or None.

    The charm is normally the repository itself, but a charm in a monorepo is a
    folder somewhere below it. The search stops at the folder the clones live
    in, because that is often itself inside a git repository - the default
    ".cache" is inside this one - and the history of the tools says nothing
    about the history of a charm.
    """
    for folder in (charm, *charm.parents):
        if (folder / ".git").exists():
            return folder
        if folder == base:
            break
    logger.info("Not a git repository: %s", charm)
    return None


@functools.cache
def branch(root: pathlib.Path) -> str:
    """Return the name of the checked-out branch of the repository.

    The clones are fresh, so this is the default branch of the remote. A
    detached HEAD (a clone of a tag, for example) is reported as "detached".
    Cached because a monorepo asks the same question once per charm.
    """
    name = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if name is None:
        return "unknown"
    name = name.strip()
    if not name or name == "HEAD":
        return "detached"
    return name


@functools.cache
def is_shallow(root: pathlib.Path) -> bool:
    """Report whether the repository is a shallow clone.

    A shallow clone has no early history, so the first commit and the number of
    authors are lower bounds rather than the real values.
    """
    result = git(root, "rev-parse", "--is-shallow-repository")
    return result is not None and result.strip() == "true"


def parse_log(output: str, path: pathlib.Path) -> Iterator[tuple[datetime.datetime, str]]:
    """Parse the (date, author) pairs out of the output of git log."""
    for line in output.splitlines():
        date, separator, author = line.partition(FIELD_SEPARATOR)
        if not separator:
            logger.warning("%s: unexpected git log output: %r", path, line)
            continue
        try:
            # Git's strict ISO 8601 always has an offset, so the result is
            # always aware, and can be compared with the other timestamps.
            when = datetime.datetime.fromisoformat(date)
        except ValueError:
            logger.warning("%s: unexpected commit date: %r", path, date)
            continue
        yield when, author


def summarise_history(
    charm: pathlib.Path, base: pathlib.Path, now: datetime.datetime
) -> CharmHistory | None:
    """Summarise the git history of a single charm.

    Returns None if the charm is not in a git repository, or has no commits.
    """
    root = find_repository_root(charm, base)
    if root is None:
        return None

    # In a monorepo, only the commits that touch this charm are of interest.
    # The charm is the whole repository in every other case, and limiting the
    # log to "." would (harmlessly, but pointlessly) exclude nothing.
    scope = ["--", str(charm.relative_to(root))] if charm != root else []
    output = git(root, "log", f"--format=%cI{FIELD_SEPARATOR}%aE", *scope)
    if output is None:
        return None

    commits = list(parse_log(output, charm))
    if not commits:
        logger.info("No commits found for %s", charm)
        return None

    # git log is in reverse chronological order, but a rebase or an amended
    # commit date can put it slightly out of order, so don't rely on that.
    dates = [when for when, _ in commits]
    recent = {
        days: sum(1 for when in dates if (now - when).days < days) for days in RECENT_WINDOWS
    }

    # A charm in a monorepo is named for its place in it, but the base folder
    # being the charm itself gives a relative path of ".", so fall back to the
    # folder name.
    name = str(charm.relative_to(base)) if charm != base else charm.name

    return CharmHistory(
        name=name,
        last_commit=max(dates),
        first_commit=min(dates),
        recent=recent,
        authors=len({author for _, author in commits}),
        branch=branch(root),
        root=root,
        shallow=is_shallow(root),
    )


def bucketed(
    values: Iterable[int], buckets: tuple[tuple[int, str], ...], overflow: str
) -> list[tuple[str, int]]:
    """Return the counts of the values in each bucket, in increasing order.

    Buckets that no charm falls into are still listed, because "no charm has
    been touched in the last week" is as interesting as the alternative.
    """
    counts = dict.fromkeys([label for _, label in buckets] + [overflow], 0)
    for value in values:
        for limit, label in buckets:
            if value <= limit:
                counts[label] += 1
                break
        else:
            counts[overflow] += 1
    return list(counts.items())


@click.command()
@click.option("--cache-folder", default=".cache")
@click.option(
    "--details/--no-details",
    default=True,
    help="Include a row per charm as well as the aggregate tables.",
)
def main(cache_folder: str, details: bool):
    """Output simple statistics about the git history of the charms."""
    configure_logging()

    base = pathlib.Path(cache_folder)
    now = datetime.datetime.now(datetime.UTC)

    charms = 0
    histories: list[CharmHistory] = []
    for charm in iter_repositories(base):
        charms += 1
        history = summarise_history(charm, base, now)
        if history is not None:
            histories.append(history)

    shallow = sorted({str(history.root) for history in histories if history.shallow})
    if shallow:
        logger.warning(
            "Shallow clones, where the first commit and the number of authors "
            "are lower bounds: %s",
            ", ".join(shallow),
        )

    report(charms, histories, now, details)


def report(
    charms: int,
    histories: list[CharmHistory],
    now: datetime.datetime,
    details: bool,
) -> None:
    """Output a report of the results to the console."""
    console = rich.console.Console()
    console.print()  # Separate out from any logging.

    console.print(f"{len(histories)} out of {charms} charms have a git history.")
    console.print()

    if not histories:
        return

    if details:
        console.print(details_table(histories))
        console.print()

    table = count_and_percentage_table(
        "Last Commit",
        "Age",
        len(histories),
        bucketed(
            ((now - history.last_commit).days for history in histories),
            AGE_BUCKETS,
            OLDER,
        ),
    )
    console.print(table)
    console.print()

    for days in RECENT_WINDOWS:
        table = count_and_percentage_table(
            f"Commits in the Last {days} Days",
            "Commits",
            len(histories),
            bucketed(
                (history.recent[days] for history in histories),
                COUNT_BUCKETS,
                MORE,
            ),
        )
        console.print(table)
        console.print()

    table = count_and_percentage_table(
        "Distinct Authors",
        "Authors",
        len(histories),
        bucketed((history.authors for history in histories), COUNT_BUCKETS, MORE),
    )
    console.print(table)
    console.print()

    table = count_and_percentage_table(
        "Repository Age",
        "Age",
        len(histories),
        bucketed((history.age.days for history in histories), AGE_BUCKETS, OLDER),
    )
    console.print(table)
    console.print()

    # A monorepo has one branch but several charms, so counting the charms
    # would say more about the size of the monorepo than about the fleet.
    branches: dict[pathlib.Path, str] = {history.root: history.branch for history in histories}
    counts = collections.Counter(branches.values())
    table = count_and_percentage_table(
        "Checked-out Branch",
        "Branch",
        len(branches),
        sorted(counts.items()),
    )
    console.print(table)
    console.print()


def details_table(histories: list[CharmHistory]) -> rich.table.Table:
    """Return a table with a row for each charm."""
    table = rich.table.Table(title="Charm History")
    table.add_column("Charm")
    table.add_column("Last Commit")
    table.add_column("First Commit")
    for days in RECENT_WINDOWS:
        table.add_column(f"Commits ({days}d)", justify="right")
    table.add_column("Authors", justify="right")
    table.add_column("Branch")
    for history in sorted(histories, key=lambda history: history.name):
        table.add_row(
            history.name,
            history.last_commit.date().isoformat(),
            history.first_commit.date().isoformat(),
            *(str(history.recent[days]) for days in RECENT_WINDOWS),
            str(history.authors),
            history.branch,
        )
    return table


if __name__ == "__main__":
    main()
