# python-cli 템플릿

HEAXHub에 등록할 **파이썬 CLI 앱**의 기본 양식이다. 이 디렉터리를 그대로 새 저장소로 복사한 뒤 `mytool/` 패키지를 실제 도구 이름으로 바꾸고, `.portal/manifest.yaml`의 메타데이터를 채우면 곧바로 신청 가능한 형태가 된다.

## 디렉터리 구조

```
python-cli/
├─ README.md                 # 본 문서
├─ pyproject.toml            # 패키지 메타데이터 (이름, 버전, entry-point)
├─ requirements.txt          # pip 의존성 (필요 시)
├─ src/
│   └─ mytool/
│       ├─ __init__.py
│       └─ main.py           # argparse 진입점
├─ tests/
│   └─ test_smoke.py         # pytest 스모크 테스트
├─ .portal/
│   ├─ manifest.yaml         # HEAXHub manifest (스키마 v1)
│   ├─ run.sh                # 포탈 표준 진입점 (input, output, params.json)
│   └─ params.schema.json    # 입력 파라미터 JSON Schema
├─ .gitignore
└─ .github/
    └─ workflows/release.yml # 태그 push 시 GitHub Release 생성
```

## 로컬 개발 흐름

```bash
# 1) 가상환경 생성
python3.11 -m venv .venv
source .venv/bin/activate

# 2) 의존성 설치 (개발 모드)
pip install -U pip
pip install -e .

# 3) 단독 실행 — 포탈 외부에서 직접 호출
python -m mytool.main \
    --input ./sample_input \
    --output ./sample_output \
    --params ./sample_params.json

# 4) 포탈과 동일한 진입점으로 실행
bash .portal/run.sh ./sample_input ./sample_output ./sample_params.json

# 5) 테스트
pytest -q
```

## 환경 변수 규약

HEAXHub의 LocalRunner는 다음 환경 변수를 자동 주입한다. `main.py`에서 인자 대신 환경 변수를 직접 사용해도 된다.

| 변수 | 의미 |
|---|---|
| `JOB_INPUT` | 입력 디렉터리 절대 경로 |
| `JOB_OUTPUT` | 출력 디렉터리 절대 경로 |
| `JOB_PARAMS` | params.json 절대 경로 |
| `JOB_ID` | 작업 식별자 (`job_YYYYMMDD_NNNN`) |

## 출력 규약

- `output/result.json` — `status`, `summary`, `warnings`, `errors`, `outputs` 필드를 포함 (schemas/result.schema.json 참고)
- `output/report.html` — 사용자에게 보여줄 리포트 (선택, 있으면 작업 상세 페이지에서 인라인 렌더링)
- 그 외 파일은 자유 — `output/` 하위에 두면 다운로드 가능

## manifest.yaml 채우기

`.portal/manifest.yaml`에서 다음 항목을 실제 값으로 교체한다.

- `id` — 소문자+숫자+언더스코어 (`^[a-z][a-z0-9_]{2,63}$`)
- `name`, `description`, `version`, `owner`
- `inputs` — 사용자에게 받을 파라미터 (실행 폼 자동 생성에 사용)
- `outputs` — 사용자에게 보여줄 결과 파일
- `build.python_version` — 빌드 시 사용할 파이썬 버전

## 등록 흐름

1. 이 양식을 사내 GitHub에 새 리포지토리로 push
2. HEAXHub `/submit`에서 해당 git URL로 신청
3. 운영자 승인 → 포탈이 자동으로 clone, venv 생성, requirements 설치
4. 빌드 성공 시 카탈로그에 노출
