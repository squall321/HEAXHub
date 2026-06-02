"""Smoke tests that can run without DB connectivity (config + schema only)."""
from __future__ import annotations

from app.config import get_settings
from app.services.manifest_validator import validate_manifest


def test_settings_load() -> None:
    s = get_settings()
    assert s.app_env in {"development", "staging", "production"}
    assert s.password_min_length >= 8


def test_manifest_validator_minimal_valid() -> None:
    manifest = {
        "schema_version": 1,
        "id": "sample_tool",
        "name": "Sample",
        "version": "0.1.0",
        "owner": "cae-automation",
        "status": "draft",
        "app_type": "cli_tool",
        "execution_target": "linux_runner",
        "launch": {"mode": "job_runner", "command": "./run.sh"},
    }
    assert validate_manifest(manifest) == []


def test_manifest_validator_catches_bad_id() -> None:
    manifest = {
        "schema_version": 1,
        "id": "BadId-WithDashes",
        "name": "x",
        "version": "0.1.0",
        "owner": "x",
        "status": "draft",
        "app_type": "cli_tool",
        "execution_target": "linux_runner",
        "launch": {"mode": "job_runner"},
    }
    errors = validate_manifest(manifest)
    assert any("id" in e for e in errors)
