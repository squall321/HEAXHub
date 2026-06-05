#!/usr/bin/env python3
"""Validate JSON Schemas under schemas/ and contracts/.

Iterates over every *.json under schemas/ and every *.schema.json under
contracts/ (recursively) and runs Draft 2020-12 meta-schema validation.

Exits non-zero on the first invalid schema.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


def iter_schema_files(repo_root: Path):
    schemas_dir = repo_root / "schemas"
    contracts_dir = repo_root / "contracts"

    if schemas_dir.is_dir():
        for path in sorted(schemas_dir.glob("*.json")):
            yield path

    if contracts_dir.is_dir():
        for path in sorted(contracts_dir.rglob("*.schema.json")):
            yield path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    files = list(iter_schema_files(repo_root))

    if not files:
        print("No JSON schema files found; skipping.")
        return 0

    failures: list[str] = []
    for path in files:
        rel = path.relative_to(repo_root)
        print(f"::group::Validate {rel}")
        try:
            with path.open("r", encoding="utf-8") as fh:
                schema = json.load(fh)
            Draft202012Validator.check_schema(schema)
            print("OK")
        except Exception as exc:  # noqa: BLE001 — surface any failure
            print(f"FAIL: {exc}")
            failures.append(str(rel))
        finally:
            print("::endgroup::")

    if failures:
        print("::error::Invalid schemas: " + ", ".join(failures))
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
