"""mytool 패키지 기본 동작을 확인하는 스모크 테스트."""
from __future__ import annotations

import json
from pathlib import Path

from mytool.main import main


def test_smoke_runs_end_to_end(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "sample.txt").write_text("hello\n", encoding="utf-8")

    params_file = tmp_path / "params.json"
    params_file.write_text(json.dumps({"foo": 1, "bar": "x"}), encoding="utf-8")

    rc = main([
        "--input", str(input_dir),
        "--output", str(output_dir),
        "--params", str(params_file),
    ])

    assert rc == 0
    result_path = output_dir / "result.json"
    assert result_path.is_file()

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] in {"success", "warning"}
    assert result["summary"]["input_file_count"] == 1
    assert "foo" in result["summary"]["received_params_keys"]
    assert (output_dir / "report.html").is_file()
