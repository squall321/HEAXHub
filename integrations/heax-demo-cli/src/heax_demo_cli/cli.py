"""heax-demo-cli — CSV 행 카운터 (HEAXHub Python CLI 데모).

HEAXHub job_runner 가 호출하는 표준 인터페이스:
  --input-dir   업로드된 파일들이 있는 디렉터리
  --output-dir  결과 파일을 떨어뜨릴 디렉터리
  --params      inputs 폼 값이 들어있는 params.json 경로
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


CSV_EXTS = (".csv", ".txt")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="heax-demo-cli",
        description="CSV 행 수를 세고 result.json 으로 떨어뜨린다.",
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    return parser.parse_args(argv)


def _load_params(params_path: Path) -> dict:
    if not params_path.exists():
        return {}
    with params_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"params.json must be a JSON object, got {type(data).__name__}")
    return data


def _resolve_csv(input_dir: Path, params: dict) -> Path:
    # 1) params 에 csv_file 이 명시돼 있으면 우선
    candidate = params.get("csv_file")
    if candidate:
        p = Path(candidate)
        if not p.is_absolute():
            p = input_dir / p
        if p.exists():
            return p

    # 2) input_dir 에서 첫 번째 CSV/TXT 파일 탐색
    if input_dir.exists():
        for entry in sorted(input_dir.iterdir()):
            if entry.is_file() and entry.suffix.lower() in CSV_EXTS:
                return entry

    raise FileNotFoundError(f"No CSV/TXT file found in {input_dir}")


def _count_rows(csv_path: Path, skip_header: bool, max_rows: int) -> int:
    count = 0
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if skip_header and i == 0:
                continue
            # 빈 라인은 데이터로 치지 않음
            if line.strip() == "":
                continue
            count += 1
            if max_rows and count >= max_rows:
                break
    return count


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    start = time.perf_counter()

    params = _load_params(args.params)
    skip_header = bool(params.get("skip_header", True))
    max_rows = int(params.get("max_rows", 0) or 0)

    csv_path = _resolve_csv(args.input_dir, params)
    file_size = csv_path.stat().st_size
    row_count = _count_rows(csv_path, skip_header=skip_header, max_rows=max_rows)

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    result = {
        "row_count": row_count,
        "file_size_bytes": file_size,
        "elapsed_ms": elapsed_ms,
        "params": params,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "result.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[heax-demo-cli] wrote {out_path} (row_count={row_count})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
