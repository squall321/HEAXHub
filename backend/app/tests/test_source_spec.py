"""Unit tests for ``integrations_scanner.SourceSpec.from_manifest``.

SourceSpec is the typed wrapper around manifest.yaml's ``source:`` block.
``None`` for legacy in-tree manifests (no ``source:`` key); raises for
malformed values; returns a populated dataclass for well-formed git
sources.
"""
from __future__ import annotations

import pytest

import yaml

from app.services.integrations_scanner import _REPO_ROOT, SourceSpec


def test_from_manifest_returns_none_when_missing() -> None:
    """No ``source:`` block → None (legacy in-tree integration)."""
    assert SourceSpec.from_manifest({}) is None
    assert SourceSpec.from_manifest({"id": "x", "version": "0.1.0"}) is None
    # Non-dict manifest → None (callers will have already rejected, but be
    # defensive).
    assert SourceSpec.from_manifest(None) is None  # type: ignore[arg-type]


def test_from_manifest_parses_git_block() -> None:
    """A well-formed git block produces a populated SourceSpec."""
    manifest = {
        "id": "heax-demo-streamlit",
        "version": "0.1.0",
        "source": {
            "type": "git",
            "url": "https://github.com/heaxhub-demos/demo-streamlit.git",
            "ref": "v0.1.0",
            "subpath": "app/",
        },
    }
    spec = SourceSpec.from_manifest(manifest)
    assert spec is not None
    assert spec.type == "git"
    assert spec.url == "https://github.com/heaxhub-demos/demo-streamlit.git"
    assert spec.ref == "v0.1.0"
    assert spec.subpath == "app/"


def test_from_manifest_defaults_for_optional_fields() -> None:
    """Missing ref → 'main'; missing subpath → '' (empty)."""
    manifest = {
        "source": {
            "type": "git",
            "url": "https://example.com/foo.git",
        }
    }
    spec = SourceSpec.from_manifest(manifest)
    assert spec is not None
    assert spec.type == "git"
    assert spec.ref == "main"
    assert spec.subpath == ""


def test_from_manifest_raises_on_invalid_block() -> None:
    """Non-mapping source / git source without url → ValueError."""
    with pytest.raises(ValueError):
        SourceSpec.from_manifest({"source": "https://x.git"})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        SourceSpec.from_manifest({"source": {"type": "git"}})  # url missing
    with pytest.raises(ValueError):
        SourceSpec.from_manifest({"source": {"type": "git", "url": ""}})


def test_relative_url_resolves_against_repo_root() -> None:
    """A scheme-less url is a path relative to the HEAXHub repo root.

    Manifests must not hardcode a box-specific absolute path — the dev box
    keeps repos under ``~/claude/`` and the deploy server under ``~/Projects/``.
    """
    spec = SourceSpec.from_manifest(
        {"source": {"type": "git", "url": "var/local-demo-repos/heax-demo-cli.git"}}
    )
    assert spec is not None
    expected = (_REPO_ROOT / "var/local-demo-repos/heax-demo-cli.git").resolve().as_uri()
    assert spec.url == expected
    assert spec.url.startswith("file://")


def test_parent_relative_url_reaches_sibling_repo() -> None:
    """``../StepForge`` resolves to the sibling repo next to HEAXHub."""
    spec = SourceSpec.from_manifest({"source": {"type": "git", "url": "../StepForge"}})
    assert spec is not None
    assert spec.url == (_REPO_ROOT.parent / "StepForge").resolve().as_uri()


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/squall321/StepForge.git",
        "file:///srv/already/absolute",
        "git@github.com:squall321/StepForge.git",
        "ssh://git@example.com/x.git",
    ],
)
def test_urls_with_scheme_pass_through_untouched(url: str) -> None:
    """https / absolute file:// / SSH shorthand are never rewritten."""
    spec = SourceSpec.from_manifest({"source": {"type": "git", "url": url}})
    assert spec is not None
    assert spec.url == url


def test_shipped_manifests_carry_no_absolute_paths() -> None:
    """No manifest in integrations/ may hardcode a box-specific absolute path.

    This is the regression guard: an absolute ``file:///home/...`` fetch-fails
    on the deploy server, and the scanner fetches before it considers a
    prebuilt SIF, so the app lands in FAILED on every scan.
    """
    offenders = []
    for mf in sorted((_REPO_ROOT / "integrations").glob("*/.portal/manifest.yaml")):
        block = (yaml.safe_load(mf.read_text(encoding="utf-8")) or {}).get("source")
        if not isinstance(block, dict):
            continue
        raw = str(block.get("url") or "")
        if raw.startswith("file://") or raw.startswith("/home/"):
            offenders.append(f"{mf.parent.parent.name}: {raw}")
    assert offenders == [], "절대경로 매니페스트: " + ", ".join(offenders)
