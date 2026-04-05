"""Tests for tools/super-tox.py."""

import asyncio
import importlib.util
import pathlib
import textwrap
from unittest import mock

import pytest

# The module filename has a hyphen, so we need importlib.
_spec = importlib.util.spec_from_file_location(
    "super_tox",
    str(pathlib.Path(__file__).resolve().parent.parent / "tools" / "super-tox.py"),
)
super_tox = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(super_tox)


def _make_settings(**overrides):
    """Create a Settings instance with sensible defaults."""
    defaults = {
        "executable": "tox",
        "mode": "local",
        "lxd_name": "test",
        "lxd_image_alias": "ubuntu-22.04",
        "keep_lxd_instance": False,
        "cache_folder": pathlib.Path("/tmp/test-cache"),
        "ops_source": "https://github.com/canonical/operator",
        "ops_source_branch": None,
        "remove_local_changes": False,
        "git_pull": False,
        "repo_re": ".*",
        "fresh_tox": False,
        "workers": 1,
        "verbose": False,
        "sample": 0,
    }
    defaults.update(overrides)
    return super_tox.Settings(**defaults)


# ---------------------------------------------------------------------------
# _patch_requirements_file
# ---------------------------------------------------------------------------


class TestPatchRequirementsFile:
    """Tests for _patch_requirements_file()."""

    def test_removes_ops_and_appends_git_source(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("ops>=2.0\nfoo\nbar\n")
        super_tox.settings = _make_settings()
        original = super_tox._patch_requirements_file(req)
        assert original == "ops>=2.0\nfoo\nbar\n"
        content = req.read_text()
        assert "ops" not in content.split("\n")[0]  # ops line removed
        assert "foo" in content
        assert "bar" in content
        assert "git+https://github.com/canonical/operator" in content

    def test_removes_ops_with_extras(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("ops[testing]>=2.0\nfoo\n")
        super_tox.settings = _make_settings()
        super_tox._patch_requirements_file(req)
        content = req.read_text()
        assert "ops[testing]" not in content
        assert "foo" in content

    def test_preserves_non_ops_git_dependencies(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("git+https://github.com/other/lib\nops\n")
        super_tox.settings = _make_settings()
        super_tox._patch_requirements_file(req)
        content = req.read_text()
        assert "git+https://github.com/other/lib" in content

    def test_removes_canonical_operator_git_line(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("git+https://github.com/canonical/operator@main\nfoo\n")
        super_tox.settings = _make_settings()
        super_tox._patch_requirements_file(req)
        content = req.read_text()
        assert "canonical/operator@main" not in content

    def test_with_branch(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("ops\n")
        super_tox.settings = _make_settings(ops_source_branch="my-branch")
        super_tox._patch_requirements_file(req)
        content = req.read_text()
        assert "git+https://github.com/canonical/operator@my-branch" in content

    def test_skips_hash_lines(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("ops\n--hash=sha256:abc\nfoo\n")
        super_tox.settings = _make_settings()
        super_tox._patch_requirements_file(req)
        content = req.read_text()
        assert "--hash" not in content
        assert "foo" in content

    def test_skips_comments(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("# a comment\nops  # inline comment\nfoo\n")
        super_tox.settings = _make_settings()
        super_tox._patch_requirements_file(req)
        content = req.read_text()
        assert "foo" in content
        # ops should be removed even with inline comment.
        lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
        ops_lines = [
            line for line in lines if line.startswith("ops") and "git+" not in line
        ]
        assert not ops_lines

    def test_handles_r_directive(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("-r base.txt\nops\n")
        super_tox.settings = _make_settings()
        super_tox._patch_requirements_file(req)
        content = req.read_text()
        assert "-r base.txt" in content

    def test_returns_original_content(self, tmp_path):
        req = tmp_path / "requirements.txt"
        original_text = "ops>=2.5\nfoo==1.0\n"
        req.write_text(original_text)
        super_tox.settings = _make_settings()
        result = super_tox._patch_requirements_file(req)
        assert result == original_text


# ---------------------------------------------------------------------------
# patch_ops context manager
# ---------------------------------------------------------------------------


class TestPatchOps:
    """Tests for the patch_ops() context manager."""

    def test_requirements_patched_and_restored(self, tmp_path):
        req = tmp_path / "requirements.txt"
        original = "ops>=2.0\nfoo\n"
        req.write_text(original)
        super_tox.settings = _make_settings()
        with super_tox.patch_ops(tmp_path) as patched:
            assert patched == req
            content = req.read_text()
            assert "ops>=2.0" not in content
            assert "git+https://github.com/canonical/operator" in content
        # After exiting, the file should be restored.
        assert req.read_text() == original

    def test_extra_requirements_files_patched_and_restored(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("ops\n")
        extra = tmp_path / "requirements-unit.txt"
        extra_original = "ops>=2.0\npytest\n"
        extra.write_text(extra_original)
        super_tox.settings = _make_settings()
        with super_tox.patch_ops(tmp_path):
            content = extra.read_text()
            assert "ops>=2.0" not in content
        assert extra.read_text() == extra_original

    def test_raises_when_no_requirements_or_pyproject(self, tmp_path):
        super_tox.settings = _make_settings()
        with pytest.raises(NotImplementedError), super_tox.patch_ops(tmp_path):
            pass

    def test_pyproject_pep621_patched_and_restored(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        original = textwrap.dedent("""\
            [project]
            name = "test"
            dependencies = [
                "ops>=2.0",
                "foo",
            ]
        """)
        pyproject.write_text(original)
        super_tox.settings = _make_settings()
        with super_tox.patch_ops(tmp_path):
            content = pyproject.read_text()
            # The ops dependency line should be removed.
            assert '"ops>=2.0"' not in content
            assert '"foo"' in content
        assert pyproject.read_text() == original

    def test_pyproject_poetry_patched_and_restored(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        original = textwrap.dedent("""\
            [tool.poetry]
            name = "test"

            [tool.poetry.dependencies]
            python = "^3.10"
            ops = "^2.5"
            foo = "^1.0"
        """)
        pyproject.write_text(original)
        super_tox.settings = _make_settings()
        # Mock poetry command so we don't need it installed.
        with (
            mock.patch("subprocess.run"),
            super_tox.patch_ops(tmp_path),
        ):
            content = pyproject.read_text()
            assert 'ops = "^2.5"' not in content
            assert "[tool.poetry.dependencies]" in content
            assert "ops = {git" in content
        assert pyproject.read_text() == original


# ---------------------------------------------------------------------------
# run_tox
# ---------------------------------------------------------------------------


class TestRunTox:
    """Tests for run_tox()."""

    @pytest.mark.asyncio
    async def test_run_tox_local_success(self, tmp_path):
        super_tox.settings = _make_settings(mode="local")
        proc = mock.AsyncMock()
        proc.communicate = mock.AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        results = []
        with mock.patch("asyncio.create_subprocess_exec", return_value=proc) as m:
            await super_tox.run_tox("tox", tmp_path, "unit", results)
        args = m.call_args[0]
        assert "tox" in args
        assert "-e" in args
        assert "unit" in args
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert results[0]["location"] == tmp_path

    @pytest.mark.asyncio
    async def test_run_tox_local_failure(self, tmp_path):
        super_tox.settings = _make_settings(mode="local")
        proc = mock.AsyncMock()
        proc.communicate = mock.AsyncMock(return_value=(b"output", b"error"))
        proc.returncode = 1
        results = []
        with mock.patch("asyncio.create_subprocess_exec", return_value=proc):
            await super_tox.run_tox("tox", tmp_path, "unit", results)
        assert len(results) == 1
        assert results[0]["passed"] is False

    @pytest.mark.asyncio
    async def test_run_tox_no_environment(self, tmp_path):
        super_tox.settings = _make_settings(mode="local")
        proc = mock.AsyncMock()
        proc.communicate = mock.AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        results = []
        with mock.patch("asyncio.create_subprocess_exec", return_value=proc) as m:
            await super_tox.run_tox("tox", tmp_path, None, results)
        args = m.call_args[0]
        assert "-e" not in args

    @pytest.mark.asyncio
    async def test_run_tox_exit_254(self, tmp_path):
        """Exit code 254 means no matching environment."""
        super_tox.settings = _make_settings(mode="local")
        proc = mock.AsyncMock()
        proc.communicate = mock.AsyncMock(return_value=(b"", b""))
        proc.returncode = 254
        results = []
        with mock.patch("asyncio.create_subprocess_exec", return_value=proc):
            await super_tox.run_tox("tox", tmp_path, "unit", results)
        assert results[0]["passed"] is False


# ---------------------------------------------------------------------------
# worker - ignore list logic
# ---------------------------------------------------------------------------


class TestWorker:
    """Tests for the worker() function's ignore-list filtering."""

    @pytest.mark.asyncio
    async def test_worker_skips_expensive(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        repo = cache / "big-repo"
        repo.mkdir()
        super_tox.settings = _make_settings(cache_folder=cache, ops_source="")
        conf = {"ignore": {"expensive": ["big-repo"]}}
        queue = asyncio.Queue()
        results = []
        queue.put_nowait((repo, "unit", results))

        with mock.patch.object(
            super_tox, "run_tox", new_callable=mock.AsyncMock
        ) as run_mock:
            task = asyncio.create_task(super_tox.worker("w0", queue, conf))
            # Wait for the item to be processed, then cancel.
            await queue.join()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        run_mock.assert_not_called()
        assert results == []

    @pytest.mark.asyncio
    async def test_worker_skips_manual(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        repo = cache / "manual-repo"
        repo.mkdir()
        super_tox.settings = _make_settings(cache_folder=cache, ops_source="")
        conf = {"ignore": {"manual": ["manual-repo"]}}
        queue = asyncio.Queue()
        results = []
        queue.put_nowait((repo, "unit", results))

        with mock.patch.object(
            super_tox, "run_tox", new_callable=mock.AsyncMock
        ) as run_mock:
            task = asyncio.create_task(super_tox.worker("w0", queue, conf))
            await queue.join()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        run_mock.assert_not_called()
        assert results == []

    @pytest.mark.asyncio
    async def test_worker_runs_tox_for_non_ignored(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        repo = cache / "good-repo"
        repo.mkdir()
        super_tox.settings = _make_settings(cache_folder=cache, ops_source="")
        conf = {"ignore": {}}
        queue = asyncio.Queue()
        results = []
        queue.put_nowait((repo, "unit", results))

        with mock.patch.object(
            super_tox, "run_tox", new_callable=mock.AsyncMock
        ) as run_mock:
            task = asyncio.create_task(super_tox.worker("w0", queue, conf))
            await queue.join()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        run_mock.assert_called_once()


# ---------------------------------------------------------------------------
# super_tox - integration-style test
# ---------------------------------------------------------------------------


class TestSuperTox:
    """Higher-level tests for the super_tox() orchestrator."""

    @pytest.mark.asyncio
    async def test_filters_by_repo_re(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "matching-charm").mkdir()
        (cache / "matching-charm" / "tox.ini").touch()
        (cache / "other-charm").mkdir()
        (cache / "other-charm" / "tox.ini").touch()

        super_tox.settings = _make_settings(
            cache_folder=cache, repo_re="matching.*", ops_source=""
        )
        conf = {"ignore": {}}

        with mock.patch.object(
            super_tox, "run_tox", new_callable=mock.AsyncMock
        ) as run_mock:
            await super_tox.super_tox(conf, "unit")
        # Only matching-charm should have been queued.
        assert run_mock.call_count == 1
        call_location = run_mock.call_args[0][1]
        assert call_location.name == "matching-charm"

    @pytest.mark.asyncio
    async def test_skips_non_directories(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a-file.txt").touch()
        (cache / "a-repo").mkdir()
        (cache / "a-repo" / "tox.ini").touch()

        super_tox.settings = _make_settings(cache_folder=cache, ops_source="")
        conf = {"ignore": {}}

        with mock.patch.object(
            super_tox, "run_tox", new_callable=mock.AsyncMock
        ) as run_mock:
            await super_tox.super_tox(conf, "unit")
        assert run_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_skips_repo_without_tox_ini_or_pyproject(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "no-tox").mkdir()  # No tox.ini or pyproject.toml.

        super_tox.settings = _make_settings(cache_folder=cache, ops_source="")
        conf = {"ignore": {}}

        with mock.patch.object(
            super_tox, "run_tox", new_callable=mock.AsyncMock
        ) as run_mock:
            await super_tox.super_tox(conf, "unit")
        run_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_tox_removes_tox_cache(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        repo = cache / "my-repo"
        repo.mkdir()
        (repo / "tox.ini").touch()
        tox_cache = repo / ".tox"
        tox_cache.mkdir()
        (tox_cache / "somefile").touch()

        super_tox.settings = _make_settings(
            cache_folder=cache, fresh_tox=True, ops_source=""
        )
        conf = {"ignore": {}}

        with mock.patch.object(super_tox, "run_tox", new_callable=mock.AsyncMock):
            await super_tox.super_tox(conf, "unit")
        assert not tox_cache.exists()

    @pytest.mark.asyncio
    async def test_sample_limits_repos(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        for i in range(5):
            repo = cache / f"repo-{i}"
            repo.mkdir()
            (repo / "tox.ini").touch()

        super_tox.settings = _make_settings(cache_folder=cache, sample=2, ops_source="")
        conf = {"ignore": {}}

        with mock.patch.object(
            super_tox, "run_tox", new_callable=mock.AsyncMock
        ) as run_mock:
            await super_tox.super_tox(conf, "unit")
        # At most 2 repos should be processed.
        assert run_mock.call_count <= 2

    @pytest.mark.asyncio
    async def test_bundle_yaml_iterates_charms_subdir(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        repo = cache / "bundle-repo"
        repo.mkdir()
        (repo / "bundle.yaml").touch()
        (repo / "tox.ini").touch()
        charms = repo / "charms"
        charms.mkdir()
        charm1 = charms / "charm-a"
        charm1.mkdir()
        charm2 = charms / "charm-b"
        charm2.mkdir()

        super_tox.settings = _make_settings(cache_folder=cache, ops_source="")
        conf = {"ignore": {}}

        with mock.patch.object(
            super_tox, "run_tox", new_callable=mock.AsyncMock
        ) as run_mock:
            await super_tox.super_tox(conf, "unit")
        # Both sub-charms should be processed.
        assert run_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_results_summary(self, tmp_path, capsys):
        cache = tmp_path / "cache"
        cache.mkdir()
        repo = cache / "repo"
        repo.mkdir()
        (repo / "tox.ini").touch()

        super_tox.settings = _make_settings(cache_folder=cache, ops_source="")
        conf = {"ignore": {}}

        async def fake_run_tox(exe, loc, env, results):
            results.append({"passed": True, "location": loc})

        with mock.patch.object(super_tox, "run_tox", side_effect=fake_run_tox):
            await super_tox.super_tox(conf, "unit")
        captured = capsys.readouterr()
        assert "1 out of 1" in captured.out
        assert "100%" in captured.out
