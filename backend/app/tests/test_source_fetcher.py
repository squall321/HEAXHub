"""Tests for the source_fetcher service.

Covers:
- git clone from a small public repo (octocat/Hello-World)
- archive_url download + extraction; sha256 mismatch raises ValidationError
- local_path rsync from tmpdir → tmpdir
- system_command verify_command (success + failure)

Network-dependent tests use pytest.skip on connection errors so the suite is
runnable in offline CI.
"""
from __future__ import annotations

import hashlib
import io
import os
import socket
import tarfile
import tempfile
from pathlib import Path

import pytest

from app.config import get_settings
from app.core.errors import ValidationError
from app.services import source_fetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _network_available(host: str = "github.com", port: int = 443) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def _make_tarball(files: dict[str, bytes]) -> bytes:
    """Build an in-memory .tar.gz containing the given path->bytes mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def test_fetch_git_clones_public_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _network_available():
        pytest.skip("network unavailable")
    # Ensure the allowlist doesn't block github.com.
    monkeypatch.setenv("ALLOWED_GIT_HOSTS", "github.com")
    get_settings.cache_clear()  # type: ignore[attr-defined]

    dest = tmp_path / "upstream"
    try:
        result = source_fetcher.fetch_source(
            {"type": "git", "url": "https://github.com/octocat/Hello-World.git"},
            dest,
        )
    except Exception as exc:  # git binary missing / network refused
        pytest.skip(f"git clone failed (likely offline / no git): {exc}")
    assert (dest / ".git").exists()
    assert isinstance(result.get("commit_sha"), str)
    assert len(result["commit_sha"]) == 40


# ---------------------------------------------------------------------------
# archive_url
# ---------------------------------------------------------------------------


def test_fetch_archive_extracts_and_validates_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-trip an in-memory tarball through fetch_source by serving it locally."""
    import http.server
    import threading

    payload = _make_tarball({"hello.txt": b"hi\n", "sub/dir.txt": b"x"})
    expected = hashlib.sha256(payload).hexdigest()

    serve_root = tmp_path / "served"
    serve_root.mkdir()
    (serve_root / "pkg.tar.gz").write_bytes(payload)

    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]

    cwd_before = os.getcwd()
    os.chdir(serve_root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        dest = tmp_path / "ws"
        url = f"http://127.0.0.1:{port}/pkg.tar.gz"

        # Good sha256
        result = source_fetcher.fetch_source(
            {"type": "archive_url", "url": url, "sha256": expected},
            dest,
        )
        assert (dest / "hello.txt").read_text() == "hi\n"
        assert result["sha256"] == expected

        # Bad sha256 → ValidationError
        dest2 = tmp_path / "ws2"
        with pytest.raises(ValidationError):
            source_fetcher.fetch_source(
                {
                    "type": "archive_url",
                    "url": url,
                    "sha256": "0" * 64,
                },
                dest2,
            )
    finally:
        server.shutdown()
        os.chdir(cwd_before)


# ---------------------------------------------------------------------------
# local_path
# ---------------------------------------------------------------------------


def test_fetch_local_path_copies_tree(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("beta")

    dest = tmp_path / "dest"
    # Use 'copy' to avoid hard dependency on rsync binary in CI.
    result = source_fetcher.fetch_source(
        {"type": "local_path", "path": str(src), "sync": "copy"},
        dest,
    )
    assert (dest / "a.txt").read_text() == "alpha"
    assert (dest / "sub" / "b.txt").read_text() == "beta"
    assert result["sync_mode"] == "copy"


def test_fetch_local_path_rsync_when_available(tmp_path: Path) -> None:
    import shutil as _sh
    if _sh.which("rsync") is None:
        pytest.skip("rsync not installed")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha")

    dest = tmp_path / "dest"
    result = source_fetcher.fetch_source(
        {"type": "local_path", "path": str(src), "sync": "rsync"},
        dest,
    )
    assert (dest / "a.txt").read_text() == "alpha"
    assert result["sync_mode"] == "rsync"


# ---------------------------------------------------------------------------
# system_command
# ---------------------------------------------------------------------------


def test_fetch_system_command_success(tmp_path: Path) -> None:
    import shutil as _sh
    if _sh.which("bash") is None:
        pytest.skip("bash not installed")
    dest = tmp_path / "ws"
    result = source_fetcher.fetch_source(
        {"type": "system_command", "verify_command": "which bash"},
        dest,
    )
    assert (dest / ".system-managed").exists()
    assert result["fetched_from"]["type"] == "system_command"


def test_fetch_system_command_missing_binary_raises(tmp_path: Path) -> None:
    dest = tmp_path / "ws"
    # `command -v` is the POSIX-defined way to test for executables; it returns
    # a non-zero exit when the target is missing, regardless of distro flavor.
    with pytest.raises(ValidationError):
        source_fetcher.fetch_source(
            {
                "type": "system_command",
                "verify_command": "command -v definitely-not-a-real-binary-xyzzy",
            },
            dest,
        )


# ---------------------------------------------------------------------------
# Misc validation
# ---------------------------------------------------------------------------


def test_unsupported_source_type_raises(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        source_fetcher.fetch_source({"type": "unknown_kind"}, tmp_path / "ws")
