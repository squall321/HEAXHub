"""Tests for ``integration_fetcher`` — git clone/update/skip/fail paths.

Uses a real local bare repo as the "upstream" so the tests exercise
``git`` end-to-end without hitting the network. The managed workspace
roots are redirected to ``tmp_path`` to keep ``var/`` untouched.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.services import integration_fetcher, managed_workspaces
from app.services.integrations_scanner import SourceSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect managed workspace roots so tests don't pollute ``var/``."""
    managed = tmp_path / "var" / "integration_workspaces"
    sifs = tmp_path / "var" / "sifs"
    logs = tmp_path / "var" / "logs"
    monkeypatch.setattr(managed_workspaces, "MANAGED_ROOT", managed)
    monkeypatch.setattr(managed_workspaces, "SIF_OUT_DIR", sifs)
    monkeypatch.setattr(managed_workspaces, "LOG_DIR", logs)
    return tmp_path


def _git(*args: str, cwd: Path) -> str:
    """Run git with a fixed identity so commits work in sandboxed envs."""
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "HOME": str(cwd),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout


def make_local_bare_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Build a bare repo at ``tmp_path/upstream.git`` with the given files.

    Workflow: create a working repo with ``files``, commit them on ``main``,
    then ``git clone --bare`` so ``fetch_for_integration`` can clone from
    it via a ``file://`` URL.
    """
    work = tmp_path / "_upstream_work"
    work.mkdir()
    _git("init", "-b", "main", cwd=work)
    for name, content in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-m", "init", cwd=work)

    bare = tmp_path / "upstream.git"
    _git("clone", "--bare", str(work), str(bare), cwd=tmp_path)
    return bare


def _add_commit(bare: Path, tmp_path: Path, name: str, content: str) -> str:
    """Append a commit to ``bare`` by cloning, committing, then pushing."""
    work2 = tmp_path / "_upstream_work2"
    if work2.exists():
        # Re-clone to keep the helper simple/idempotent.
        for child in sorted(work2.rglob("*"), reverse=True):
            if child.is_dir():
                child.rmdir()
            else:
                child.unlink()
        work2.rmdir()
    _git("clone", str(bare), str(work2), cwd=tmp_path)
    (work2 / name).write_text(content, encoding="utf-8")
    _git("add", "-A", cwd=work2)
    _git("commit", "-m", f"add {name}", cwd=work2)
    _git("push", "origin", "main", cwd=work2)
    return _git("rev-parse", "HEAD", cwd=work2).strip()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fetch_clones_when_upstream_absent(
    isolated_paths: Path, tmp_path: Path
) -> None:
    """First fetch into an empty workspace clones and reports a commit sha."""
    bare = make_local_bare_repo(tmp_path, {"README.md": "hello\n"})
    spec = SourceSpec(type="git", url=f"file://{bare}", ref="main")

    result = integration_fetcher.fetch_for_integration("demo-clone", spec)

    assert result.action == "cloned"
    assert result.error is None
    assert result.commit and len(result.commit) == 40
    ws = managed_workspaces.upstream_dir("demo-clone")
    assert (ws / "README.md").read_text() == "hello\n"
    assert (ws / ".git").is_dir()


def test_fetch_skips_when_ref_matches(
    isolated_paths: Path, tmp_path: Path
) -> None:
    """Second fetch with no upstream changes returns action=skipped."""
    bare = make_local_bare_repo(tmp_path, {"README.md": "hello\n"})
    spec = SourceSpec(type="git", url=f"file://{bare}", ref="main")

    first = integration_fetcher.fetch_for_integration("demo-skip", spec)
    assert first.action == "cloned"

    second = integration_fetcher.fetch_for_integration("demo-skip", spec)
    assert second.action == "skipped"
    assert second.commit == first.commit
    assert second.error is None


def test_fetch_updates_when_new_commit(
    isolated_paths: Path, tmp_path: Path
) -> None:
    """Adding a new upstream commit and re-fetching reports action=updated."""
    bare = make_local_bare_repo(tmp_path, {"README.md": "v1\n"})
    spec = SourceSpec(type="git", url=f"file://{bare}", ref="main")

    first = integration_fetcher.fetch_for_integration("demo-update", spec)
    assert first.action == "cloned"

    new_sha = _add_commit(bare, tmp_path, "extra.txt", "added\n")

    second = integration_fetcher.fetch_for_integration("demo-update", spec)
    assert second.action == "updated"
    assert second.commit == new_sha
    ws = managed_workspaces.upstream_dir("demo-update")
    assert (ws / "extra.txt").read_text() == "added\n"


def test_fetch_failed_on_bad_url(isolated_paths: Path, tmp_path: Path) -> None:
    """A non-existent URL produces action=failed with a populated error."""
    bogus = tmp_path / "does-not-exist.git"
    spec = SourceSpec(type="git", url=f"file://{bogus}", ref="main")

    result = integration_fetcher.fetch_for_integration("demo-bad", spec)

    assert result.action == "failed"
    assert result.commit is None
    assert result.error and result.error.strip()
