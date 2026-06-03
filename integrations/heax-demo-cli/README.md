# heax-demo-cli

HEAXHub `python_cli` 스택 데모. 업로드된 CSV 파일의 데이터 행 수를 세어
`output/result.json` 으로 기록한다. 입력 폼, 잡 실행, 출력 회수 등 HEAXHub
표준 흐름이 잘 동작하는지 확인하기 위한 픽스처 통합.

## HEAXHub 가 이 통합을 인식하는 방법

`.portal/manifest.yaml` 하나로 끝난다.

- `build.stack: python_cli` — HEAXHub Runner 는 `pyproject.toml` 을 보고
  Python venv 를 만들고 패키지를 설치한다.
- `launch.command: ./.portal/run.sh` — 잡 실행 시 호출되는 엔트리포인트.
- `inputs:` — 포털 UI 의 파라미터 폼을 자동 생성한다 (`csv_file`,
  `skip_header`, `max_rows`).
- `outputs:` — 잡 종료 후 회수할 파일 경로 (`output/result.json`).

Runner 는 `run.sh` 를 다음 인자와 함께 호출한다.

```
./.portal/run.sh <INPUT_DIR> <OUTPUT_DIR> <PARAMS_JSON>
```

`run.sh` 가 다시 `python -m heax_demo_cli.cli` 를 호출하면서
`--input-dir / --output-dir / --params` 를 전달한다.

## 로컬 테스트

```bash
pip install -e .

mkdir -p /tmp/heax-demo/{in,out}
printf 'name,score\nA,1\nB,2\nC,3\n' > /tmp/heax-demo/in/sample.csv
echo '{"skip_header": true, "max_rows": 0}' > /tmp/heax-demo/params.json

heax-demo-cli \
  --input-dir /tmp/heax-demo/in \
  --output-dir /tmp/heax-demo/out \
  --params /tmp/heax-demo/params.json

cat /tmp/heax-demo/out/result.json
```

## 출력 스키마

```json
{
  "row_count": 3,
  "file_size_bytes": 24,
  "elapsed_ms": 1,
  "params": { "skip_header": true, "max_rows": 0 }
}
```
