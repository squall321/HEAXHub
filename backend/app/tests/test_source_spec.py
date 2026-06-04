"""Unit tests for ``integrations_scanner.SourceSpec.from_manifest``.

SourceSpec is the typed wrapper around manifest.yaml's ``source:`` block.
``None`` for legacy in-tree manifests (no ``source:`` key); raises for
malformed values; returns a populated dataclass for well-formed git
sources.
"""
from __future__ import annotations

import pytest

from app.services.integrations_scanner import SourceSpec


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
