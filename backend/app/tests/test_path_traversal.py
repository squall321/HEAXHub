"""Path-traversal guards on file-download endpoints.

These tests exercise the raw helpers (`safe_join`, `installer_packages.installer_dir`)
directly. We deliberately avoid spinning up the full FastAPI app + DB because
the SA-D scope is to verify the guard logic, not auth or routing.

The endpoint mapping verified here is:
    GET /apps/{app_id}/files/{path:path}            → safe_join
    GET /jobs/{job_id}/files/{path:path}            → safe_join
    GET /apps/{app_id}/installers/{os}/{version}    → installer_dir → _safe_segment
    GET /apps/{app_id}/installers/latest            → redirects only, no FS join

A traversal attempt should raise ValidationError before the file is opened
(status 422 surfaced via register_exception_handlers); absolute paths should
land in the same bucket; symlinks pointing outside the base should resolve to a
path that fails the `relative_to(base)` check.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.core.errors import ValidationError
from app.services.installer_packages import installer_dir, _safe_segment
from app.services.workspace_manager import safe_join


# ─── /apps/{id}/files and /jobs/{id}/files ───────────────────────────────────


def test_safe_join_blocks_dotdot_traversal(tmp_path: Path) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    (base / "ok.txt").write_text("ok")

    with pytest.raises(ValidationError):
        safe_join(base, "../../etc/passwd")


def test_safe_join_blocks_deeper_dotdot(tmp_path: Path) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    with pytest.raises(ValidationError):
        safe_join(base, "subdir/../../escape")


def test_safe_join_blocks_absolute_path(tmp_path: Path) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    # Path("/etc/passwd") is absolute → joined path resolves to /etc/passwd,
    # which can't be relative_to(base).
    with pytest.raises(ValidationError):
        safe_join(base, "/etc/passwd")


def test_safe_join_blocks_symlink_pointing_outside(tmp_path: Path) -> None:
    base = tmp_path / "workspace"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")

    link = base / "escape"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem does not support symlinks")

    # safe_join resolves the symlink first → relative_to(base) fails.
    with pytest.raises(ValidationError):
        safe_join(base, "escape/secret.txt")


def test_safe_join_accepts_in_base_path(tmp_path: Path) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    (base / "nested").mkdir()
    target = base / "nested" / "file.txt"
    target.write_text("hi")

    resolved = safe_join(base, "nested/file.txt")
    assert resolved == target.resolve()


# ─── /apps/{id}/installers/{os}/{version} ────────────────────────────────────


@pytest.mark.parametrize(
    "label,value",
    [
        ("os", "../../etc"),
        ("os", "win/../../etc"),
        ("os", "/absolute"),
        ("os", ""),
        ("version", "../v1"),
        ("version", "1.0.0/../../etc"),
        ("version", "with spaces"),
        ("version", "null\x00byte"),
    ],
)
def test_installer_segments_reject_dangerous_chars(label: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _safe_segment(label, value)


def test_installer_dir_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point the installer storage root at tmp so we never touch real files.
    monkeypatch.setattr(get_settings(), "installer_storage_root", tmp_path)

    with pytest.raises(ValidationError):
        installer_dir("myapp", "../../etc", "1.0.0")
    with pytest.raises(ValidationError):
        installer_dir("myapp", "windows-x64", "../1.0.0")


def test_installer_dir_accepts_safe_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "installer_storage_root", tmp_path)
    out = installer_dir("myapp", "windows-x64", "1.2.3")
    assert str(out).startswith(str(tmp_path.resolve()))
    assert out.name == "1.2.3"
    assert out.parent.name == "windows-x64"


# ─── absolute paths through installer_dir's app_id seg ──────────────────────


def test_installer_dir_rejects_absolute_app_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "installer_storage_root", tmp_path)
    # absolute style passes through _safe_segment? No — '/' isn't in [A-Za-z0-9._-]
    with pytest.raises(ValidationError):
        installer_dir("/etc/passwd", "windows-x64", "1.0.0")
