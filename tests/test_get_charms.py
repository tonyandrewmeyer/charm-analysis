"""Tests for tools/get_charms.py."""

import csv
import io
import pathlib
import sys
from unittest import mock

import pytest

# Add tools/ to sys.path so we can import get_charms directly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import get_charms

# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestClone:
    """Tests for the clone() coroutine."""

    @pytest.mark.asyncio
    async def test_clone_builds_correct_args(self, tmp_path):
        dest = tmp_path / "my-charm"
        proc = mock.AsyncMock()
        proc.wait = mock.AsyncMock()
        proc.returncode = 0
        with mock.patch("asyncio.create_subprocess_exec", return_value=proc) as m:
            await get_charms.clone(dest, "my-charm", "git@github.com:org/repo", "")
        args = m.call_args[0]
        assert args[0] == "git"
        assert "--depth=1" in args
        assert "--single-branch" in args
        assert "--no-tags" in args
        assert "git@github.com:org/repo" in args
        assert str(dest) in args
        # --branch should NOT be present when branch is empty.
        assert "--branch" not in args

    @pytest.mark.asyncio
    async def test_clone_with_branch(self, tmp_path):
        dest = tmp_path / "my-charm"
        proc = mock.AsyncMock()
        proc.wait = mock.AsyncMock()
        proc.returncode = 0
        with mock.patch("asyncio.create_subprocess_exec", return_value=proc) as m:
            await get_charms.clone(dest, "my-charm", "git@github.com:org/repo", "main")
        args = m.call_args[0]
        idx = args.index("--branch")
        assert args[idx + 1] == "main"

    @pytest.mark.asyncio
    async def test_clone_logs_error_on_failure(self, tmp_path):
        dest = tmp_path / "my-charm"
        proc = mock.AsyncMock()
        proc.wait = mock.AsyncMock()
        proc.returncode = 1
        with (
            mock.patch("asyncio.create_subprocess_exec", return_value=proc),
            mock.patch.object(get_charms.logger, "error") as log_err,
        ):
            await get_charms.clone(dest, "my-charm", "git@github.com:org/repo", "")
        log_err.assert_called_once()


class TestPull:
    """Tests for the pull() coroutine."""

    @pytest.mark.asyncio
    async def test_pull_builds_correct_args(self, tmp_path):
        dest = tmp_path / "my-charm"
        proc = mock.AsyncMock()
        proc.wait = mock.AsyncMock()
        proc.returncode = 0
        with mock.patch("asyncio.create_subprocess_exec", return_value=proc) as m:
            await get_charms.pull(dest, "my-charm")
        args = m.call_args[0]
        assert args == ("git", "pull", "--quiet")
        assert m.call_args[1]["cwd"] == dest.resolve()

    @pytest.mark.asyncio
    async def test_pull_logs_warning_on_failure(self, tmp_path):
        dest = tmp_path / "my-charm"
        proc = mock.AsyncMock()
        proc.wait = mock.AsyncMock()
        proc.returncode = 1
        with (
            mock.patch("asyncio.create_subprocess_exec", return_value=proc),
            mock.patch.object(get_charms.logger, "warning") as log_warn,
        ):
            await get_charms.pull(dest, "my-charm")
        log_warn.assert_called_once()


class TestProcessInput:
    """Tests for process_input()."""

    def _make_csv(self, rows):
        """Build a csv.DictReader from a list of dicts."""
        buf = io.StringIO()
        fieldnames = ["Charm Name", "Repository", "Branch (if not the default)"]
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        buf.seek(0)
        return csv.DictReader(buf)

    @pytest.mark.asyncio
    async def test_clones_when_repo_folder_missing(self, tmp_path):
        reader = self._make_csv([
            {
                "Charm Name": "test-charm",
                "Repository": "https://github.com/org/my-operator",
                "Branch (if not the default)": "",
            },
        ])
        with (
            mock.patch.object(
                get_charms, "clone", new_callable=mock.AsyncMock
            ) as clone_mock,
            mock.patch.object(get_charms, "pull", new_callable=mock.AsyncMock),
        ):
            await get_charms.process_input(reader, tmp_path)
        clone_mock.assert_called_once()
        call_args = clone_mock.call_args[0]
        assert call_args[0] == tmp_path / "my-operator"
        assert call_args[1] == "test-charm"
        # URL should be converted to git@ form.
        assert call_args[2] == "git@github.com:org/my-operator"

    @pytest.mark.asyncio
    async def test_pulls_when_repo_folder_exists(self, tmp_path):
        (tmp_path / "my-operator").mkdir()
        reader = self._make_csv([
            {
                "Charm Name": "test-charm",
                "Repository": "https://github.com/org/my-operator",
                "Branch (if not the default)": "",
            },
        ])
        with (
            mock.patch.object(
                get_charms, "clone", new_callable=mock.AsyncMock
            ) as clone_mock,
            mock.patch.object(
                get_charms, "pull", new_callable=mock.AsyncMock
            ) as pull_mock,
        ):
            await get_charms.process_input(reader, tmp_path)
        clone_mock.assert_not_called()
        pull_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_branch_in_folder_name(self, tmp_path):
        reader = self._make_csv([
            {
                "Charm Name": "test-charm",
                "Repository": "https://github.com/org/my-operator",
                "Branch (if not the default)": "feat/new",
            },
        ])
        with (
            mock.patch.object(
                get_charms, "clone", new_callable=mock.AsyncMock
            ) as clone_mock,
            mock.patch.object(get_charms, "pull", new_callable=mock.AsyncMock),
        ):
            await get_charms.process_input(reader, tmp_path)
        dest = clone_mock.call_args[0][0]
        assert dest == tmp_path / "my-operator-feat/new"

    @pytest.mark.asyncio
    async def test_skips_empty_rows(self, tmp_path):
        reader = self._make_csv([
            {
                "Charm Name": "",
                "Repository": "",
                "Branch (if not the default)": "",
            },
        ])
        with (
            mock.patch.object(
                get_charms, "clone", new_callable=mock.AsyncMock
            ) as clone_mock,
            mock.patch.object(
                get_charms, "pull", new_callable=mock.AsyncMock
            ) as pull_mock,
        ):
            await get_charms.process_input(reader, tmp_path)
        clone_mock.assert_not_called()
        pull_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_url_conversion(self, tmp_path):
        reader = self._make_csv([
            {
                "Charm Name": "c",
                "Repository": "https://github.com/canonical/ops-lib",
                "Branch (if not the default)": "",
            },
        ])
        with (
            mock.patch.object(
                get_charms, "clone", new_callable=mock.AsyncMock
            ) as clone_mock,
            mock.patch.object(get_charms, "pull", new_callable=mock.AsyncMock),
        ):
            await get_charms.process_input(reader, tmp_path)
        repo_arg = clone_mock.call_args[0][2]
        assert repo_arg == "git@github.com:canonical/ops-lib"

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped_from_repo_name(self, tmp_path):
        reader = self._make_csv([
            {
                "Charm Name": "c",
                "Repository": "https://github.com/canonical/ops-lib/",
                "Branch (if not the default)": "",
            },
        ])
        with (
            mock.patch.object(
                get_charms, "clone", new_callable=mock.AsyncMock
            ) as clone_mock,
            mock.patch.object(get_charms, "pull", new_callable=mock.AsyncMock),
        ):
            await get_charms.process_input(reader, tmp_path)
        dest = clone_mock.call_args[0][0]
        assert dest.name == "ops-lib"
