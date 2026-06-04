"""Unit tests for ``apt_runner.local_apptainer_path`` resolution order.

The four candidates must be honored in the same order as
``deploy/apptainer/_common.sh::resolve_apptainer``:

    1. HEAXHUB_APPT_BIN env override
    2. deploy/apptainer/.tools/apptainer-*/usr/bin/apptainer (newest)
    3. /usr/local/bin/apptainer
    4. shutil.which("apptainer")

Each test monkeypatches the module-level paths/env so we don't touch the
real filesystem outside ``tmp_path``.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.services import apt_runner


def _touch_exec(path: Path) -> None:
    """Create ``path`` and mark it executable so :func:`os.access(X_OK)` passes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho stub\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point apt_runner at an empty ``tmp_path/.tools`` and clear env+PATH.

    Returns the synthetic ``.tools`` directory so individual tests can
    populate it with fake ``apptainer-<ver>`` installs.
    """
    tools = tmp_path / ".tools"
    tools.mkdir()
    monkeypatch.setattr(apt_runner, "_TOOLS_DIR", tools)

    # Wipe env override and PATH so only what we set up is visible.
    monkeypatch.delenv("HEAXHUB_APPT_BIN", raising=False)
    # Use a PATH that contains no apptainer.
    empty_dir = tmp_path / "empty_path"
    empty_dir.mkdir()
    monkeypatch.setenv("PATH", str(empty_dir))

    # Ensure /usr/local/bin/apptainer is treated as missing during the test
    # (we can't actually remove it). Patch the file-existence check.
    real_executable = apt_runner._executable

    def fake_executable(p):
        s = str(p)
        if s == "/usr/local/bin/apptainer":
            return False
        return real_executable(p)

    monkeypatch.setattr(apt_runner, "_executable", fake_executable)
    return tools


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_local_apptainer_path_prefers_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_resolver: Path,
) -> None:
    """HEAXHUB_APPT_BIN wins over .tools/ and PATH."""
    # Set up BOTH a tools install AND the env override; env should win.
    tools_bin = isolated_resolver / "apptainer-1.3.6" / "usr" / "bin" / "apptainer"
    _touch_exec(tools_bin)

    override = tmp_path / "my-pinned" / "apptainer"
    _touch_exec(override)
    monkeypatch.setenv("HEAXHUB_APPT_BIN", str(override))

    resolved = apt_runner.local_apptainer_path()
    assert resolved == str(override.resolve())


def test_local_apptainer_path_prefers_tools_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_resolver: Path,
) -> None:
    """No env override → newest .tools/apptainer-<ver> wins over PATH.

    Also asserts natural sort: 1.3.10 must beat 1.3.6.
    """
    # Add a PATH-resolvable apptainer to ensure it's NOT picked.
    path_dir = tmp_path / "path_bin"
    path_dir.mkdir()
    path_bin = path_dir / "apptainer"
    _touch_exec(path_bin)
    monkeypatch.setenv("PATH", str(path_dir))

    older = isolated_resolver / "apptainer-1.3.6" / "usr" / "bin" / "apptainer"
    newer = isolated_resolver / "apptainer-1.3.10" / "usr" / "bin" / "apptainer"
    _touch_exec(older)
    _touch_exec(newer)

    resolved = apt_runner.local_apptainer_path()
    assert resolved == str(newer.resolve())
    assert "1.3.10" in resolved
    assert resolved != str(path_bin.resolve())


def test_local_apptainer_path_falls_back_to_system(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_resolver: Path,
) -> None:
    """No env override and no .tools install → fall back to PATH."""
    # .tools/ is empty (the fixture made it so). Add an apptainer on PATH.
    path_dir = tmp_path / "path_bin"
    path_dir.mkdir()
    path_bin = path_dir / "apptainer"
    _touch_exec(path_bin)
    monkeypatch.setenv("PATH", str(path_dir))

    resolved = apt_runner.local_apptainer_path()
    # shutil.which returns whatever string it finds; normalize for compare.
    assert Path(resolved).resolve() == path_bin.resolve()


def test_local_apptainer_path_raises_when_none(
    isolated_resolver: Path,
) -> None:
    """All four candidates absent → FileNotFoundError with actionable text."""
    # Fixture already cleared env + emptied PATH + masked /usr/local/bin.
    with pytest.raises(FileNotFoundError) as ei:
        apt_runner.local_apptainer_path()
    msg = str(ei.value)
    assert "apptainer" in msg.lower()
    assert "HEAXHUB_APPT_BIN" in msg or "install-apptainer" in msg
