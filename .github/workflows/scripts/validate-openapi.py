#!/usr/bin/env python3
"""Validate OpenAPI specs found under contracts/.

Looks for contracts/**/openapi.yaml and contracts/**/openapi.yml and runs
openapi-spec-validator on each. Exits non-zero on the first invalid spec.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml
from openapi_spec_validator import validate


def iter_openapi_files(repo_root: Path):
    contracts_dir = repo_root / "contracts"
    if not contracts_dir.is_dir():
        return
    for pattern in ("openapi.yaml", "openapi.yml"):
        for path in sorted(contracts_dir.rglob(pattern)):
            yield path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    files = list(iter_openapi_files(repo_root))

    if not files:
        print("No OpenAPI specs found; skipping.")
        return 0

    failures: list[str] = []
    for path in files:
        rel = path.relative_to(repo_root)
        print(f"::group::Validate {rel}")
        try:
            with path.open("r", encoding="utf-8") as fh:
                spec = yaml.safe_load(fh)
            validate(spec)
            print("OK")
        except Exception as exc:  # noqa: BLE001 — surface any failure
            print(f"FAIL: {exc}")
            failures.append(str(rel))
        finally:
            print("::endgroup::")

    if failures:
        print("::error::Invalid OpenAPI specs: " + ", ".join(failures))
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
