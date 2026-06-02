# Capability Matrix

HEAXHub이 케이스별로 어디까지 자동 처리하고, 운영자 입력이 어디부터 필요한지 한눈에 보여주는 표.

## 1. 케이스 정의

| 케이스 | 설명 | 대표 예시 |
|---|---|---|
| A | 풀스택 웹앱 (FE + BE) | Next.js + FastAPI 대시보드, React + Django 사내 도구 |
| B | 윈도우 GUI EXE | PyQt 메쉬 도구, WPF CAE preprocessor |
| C | 상용 프로그램 임베드 | LS-DYNA, ANSYS, ABAQUS, MATLAB |
| D | 소스 없는 도구 | NAS 공유폴더 ZIP, GitHub Release asset, 시스템 명령 wrapping |
| E | 인터프리터 버전 매칭 | Python 3.9/3.10/3.11, Node 18/20/22 동시 존재 |
| F | GPU 학습/추론 | PyTorch 학습, TensorRT 추론 |
| G | 장기 데몬 서비스 | Streamlit/Jupyter/대시보드 |
| H | 사용자 PC 자동 설치 | Custom Protocol (`koomesh://`) |

## 2. 자동 추론 가능성

| 케이스 | 정적 분석으로 확정 | LLM 추론 | 운영자 입력 필요 |
|---|---|---|---|
| A | 언어, 의존성, 빌드 명령, 포트 후보 | health check 경로, env 변수 용도, base path 지원 여부 | DB 마이그레이션 명령, 라이선스 visibility |
| B | C# / PyInstaller 빌드 도구 식별, GH Release asset | EXE 다운로드 URL, custom protocol 이름 | Windows 빌드 머신, code signing, headless 가능 여부 |
| C | `lmstat` / `LSTC_*` env / `ansys` 키워드 grep | 라이선스 필요 여부, MPI 패턴 | SIF 경로, feature 이름, 토큰 수 |
| D | 없음 (소스 없음) | 없음 | source URL, 실행 명령, 의존성 |
| E | `.python-version`, `pyproject`, `.nvmrc`, `engines.node` | 폴백 후보 | 정확한 패치 버전 (드물게) |
| F | `torch`, `tensorflow-gpu`, CUDA Dockerfile, `--nv` | multi-GPU 패턴, 최소 GPU 메모리 | 메모리 요구량 정확치 |
| G | 데몬 명령 패턴 (`uvicorn`, `streamlit`, `jupyter`) | 헬스체크 경로 후보 | 별도 없음 (대부분 자동) |
| H | GitHub Release `.exe`/`.msi` | custom protocol 이름 | 설치 후 등록 절차, 인증서 |

## 3. 자동화 비율

| 케이스 | 정적만 | + LLM | + 운영자 검토 | Tier |
|---|---|---|---|---|
| A 풀스택 웹앱 | 40% | 60% | 95% | 1 |
| B 윈도우 GUI | 10% | 20% | 60% | 3 |
| C 상용 임베드 | 15% | 25% | 80% | 2 |
| D 소스 없는 도구 | 0% | 15% | 70% | 1 |
| E 인터프리터 버전 | 80% | 90% | 99% | 1 |
| F GPU | 50% | 70% | 90% | 2 |
| G 장기 데몬 | 55% | 75% | 95% | 1-2 |
| H PC 자동 설치 | 10% | 30% | 60% | 3 |

## 4. Tier별 구현 우선순위

- **Tier 1** (1~2주): A, D, E, G  → 일상 사내 도구의 90% 커버
- **Tier 2** (1~2주): C, F  → 솔버·학습 작업 커버
- **Tier 3** (2~3주): B, H  → 윈도우 작업 커버
- **Tier 4** (1주): 보안/CI/quota

## 5. LLM이 다루지 않는 영역 (명시적)

다음은 **AI에게 절대 묻지 않고 정적 분석/운영자만 결정**한다.

- 의존성 hash / sha256 (보안)
- secret 값 (환경 변수의 실제 값)
- 라이선스 서버 호스트
- 사용자 권한 (visibility, executable_by)
- 자원 한도 (CPU/메모리 quota)
- upstream 소스 코드 수정 (절대 금지)

## 6. AI 추론 confidence 정책

| 신뢰도 | 처리 |
|---|---|
| ≥ 0.9 | manifest_draft에 포함, 운영자 통과 시 자동 채택 |
| 0.7 ~ 0.9 | manifest_draft에 포함하되 운영자 검토 필요 표시 |
| 0.5 ~ 0.7 | manifest_draft에서 제외, `open_questions[]`로 운영자에게 질문 |
| < 0.5 | LLM이 응답해도 무시, 운영자 수동 입력 강제 |

## 7. 실제 시연 시나리오

| repo 예시 | 추론 후 운영자 보완 항목 | 자동화율 |
|---|---|---|
| `pallets/flask` (튜토리얼 수준) | visibility만 | 95% |
| `streamlit/streamlit-hello` | visibility, base_path | 90% |
| 사내 풀스택 (React + FastAPI + Postgres) | DB 마이그레이션, ENV 4종 | 70% |
| 사내 LS-DYNA wrapper | SIF 경로, license pool, MPI 옵션 | 60% |
| 사내 PyQt EXE | 빌드 머신 지정, custom protocol | 35% |
