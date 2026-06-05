#!/usr/bin/env python3
"""Validate the JSON code blocks in docs/hwax-agent-e2e-example.md against the
hwax-agent JSON schemas in contracts/hwax-agent/.

Scans for HTML markers of the form:

    <!-- validates: NAME.schema.json -->
    ```json
    { ... }
    ```

For each marker, the NEXT ```json ... ``` fence is parsed and validated against
contracts/hwax-agent/NAME.schema.json. Exit code 0 iff all blocks validate.

Usage:
    .venv/bin/python scripts/validate-e2e-examples.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "hwax-agent-e2e-example.md"
SCHEMA_DIR = REPO_ROOT / "contracts" / "hwax-agent"

MARKER_RE = re.compile(r"<!--\s*validates:\s*([a-z0-9._-]+\.schema\.json)\s*-->")
FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def load_schema(name: str) -> dict:
    path = SCHEMA_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"schema not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def find_blocks(text: str) -> list[tuple[int, str, str]]:
    """Return [(line_no, schema_name, json_body), ...] in document order."""
    blocks: list[tuple[int, str, str]] = []
    for m in MARKER_RE.finditer(text):
        schema_name = m.group(1)
        # find the next ```json fence after this marker
        rest = text[m.end():]
        fm = FENCE_RE.search(rest)
        if not fm:
            line_no = text[: m.start()].count("\n") + 1
            raise ValueError(
                f"marker at line {line_no} for {schema_name!r} has no following ```json fence"
            )
        body = fm.group(1)
        absolute_offset = m.end() + fm.start(1)
        line_no = text[:absolute_offset].count("\n") + 1
        blocks.append((line_no, schema_name, body))
    return blocks


def main() -> int:
    text = DOC_PATH.read_text(encoding="utf-8")
    blocks = find_blocks(text)
    if not blocks:
        print(f"[warn] no <!-- validates: ... --> markers found in {DOC_PATH}")
        return 1

    schema_cache: dict[str, dict] = {}
    counts: dict[str, int] = {}
    failures: list[tuple[int, str, str]] = []

    for line_no, schema_name, body in blocks:
        if schema_name not in schema_cache:
            schema_cache[schema_name] = load_schema(schema_name)
        schema = schema_cache[schema_name]
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            failures.append((line_no, schema_name, f"JSON decode error: {e}"))
            continue
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
        if errors:
            msg_lines = []
            for err in errors:
                path = "/".join(str(p) for p in err.absolute_path) or "<root>"
                msg_lines.append(f"    - at {path}: {err.message}")
            failures.append((line_no, schema_name, "\n".join(msg_lines)))
        counts[schema_name] = counts.get(schema_name, 0) + 1

    total = len(blocks)
    print(f"checked {total} JSON block(s) in {DOC_PATH}")
    for name, n in sorted(counts.items()):
        print(f"  - {name}: {n}")
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for line_no, schema_name, msg in failures:
            print(f"  line {line_no}  schema={schema_name}")
            print(msg)
        return 2
    print("all blocks validated against their declared schema.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
