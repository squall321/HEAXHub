"""HEAXHub python-cli template entry point.

표준 호출 규약
--------------
    python -m mytool.main --input <DIR> --output <DIR> --params <FILE>

HEAXHub의 LocalRunner는 환경 변수도 함께 주입한다.

    JOB_INPUT, JOB_OUTPUT, JOB_PARAMS, JOB_ID

명시 인자가 없으면 환경 변수를 fallback으로 사용한다.

출력 규약
---------
- ``<output>/result.json`` 에 status/summary/warnings/errors/outputs 를 기록
- ``<output>/report.html`` 에 사용자에게 보여줄 HTML 리포트 (선택)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mytool",
        description="HEAXHub python-cli template",
    )
    parser.add_argument(
        "--input",
        dest="input_dir",
        default=os.environ.get("JOB_INPUT"),
        help="입력 디렉터리 (없으면 환경변수 JOB_INPUT 사용)",
    )
    parser.add_argument(
        "--output",
        dest="output_dir",
        default=os.environ.get("JOB_OUTPUT"),
        help="출력 디렉터리 (없으면 환경변수 JOB_OUTPUT 사용)",
    )
    parser.add_argument(
        "--params",
        dest="params_file",
        default=os.environ.get("JOB_PARAMS"),
        help="params.json 경로 (없으면 환경변수 JOB_PARAMS 사용)",
    )
    return parser.parse_args(argv)


def load_params(params_file: str | None) -> dict:
    if not params_file:
        return {}
    p = Path(params_file)
    if not p.is_file():
        return {}
    with p.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def write_result(output_dir: Path, result: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "result.json").open("w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)


def write_report(output_dir: Path, params: dict, summary: dict) -> None:
    """간단한 HTML 리포트 stub. 실제 앱에서는 jinja2 등으로 교체."""
    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>mytool report</title>
<style>
  body {{ font-family: -apple-system, sans-serif; padding: 24px; color: #222; }}
  h1 {{ border-bottom: 1px solid #ddd; padding-bottom: 8px; }}
  pre {{ background: #f6f6f6; padding: 12px; border-radius: 6px; overflow-x: auto; }}
  .meta {{ color: #666; font-size: 14px; }}
</style>
</head>
<body>
<h1>mytool report</h1>
<p class="meta">job_id: {os.environ.get("JOB_ID", "(local-run)")} · generated: {datetime.now(timezone.utc).isoformat()}</p>
<h2>Params</h2>
<pre>{json.dumps(params, ensure_ascii=False, indent=2)}</pre>
<h2>Summary</h2>
<pre>{json.dumps(summary, ensure_ascii=False, indent=2)}</pre>
</body>
</html>
"""
    (output_dir / "report.html").write_text(html, encoding="utf-8")


def run(input_dir: Path, output_dir: Path, params: dict) -> dict:
    """실제 도구 로직이 들어갈 자리.

    여기서는 템플릿이므로 입력 파일 개수와 파라미터를 echo 하는
    더미 동작만 수행한다.
    """
    n_inputs = 0
    if input_dir.is_dir():
        n_inputs = sum(1 for _ in input_dir.rglob("*") if _.is_file())

    summary = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "input_file_count": n_inputs,
        "received_params_keys": sorted(params.keys()),
    }

    warnings: list[str] = []
    if n_inputs == 0:
        warnings.append("입력 디렉터리에 파일이 없습니다.")

    return {
        "status": "warning" if warnings else "success",
        "summary": summary,
        "warnings": warnings,
        "errors": [],
        "outputs": {
            "result": "result.json",
            "report": "report.html",
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.input_dir or not args.output_dir:
        print("error: --input / --output (또는 JOB_INPUT / JOB_OUTPUT) 필요", file=sys.stderr)
        return 2

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    params = load_params(args.params_file)

    print(f"[mytool] start input={input_dir} output={output_dir}", flush=True)
    result = run(input_dir, output_dir, params)

    write_result(output_dir, result)
    write_report(output_dir, params, result["summary"])

    print(f"[mytool] done status={result['status']}", flush=True)
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
