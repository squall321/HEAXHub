# HEAXHub — 전체 통합 계획서 v2

**작성일**: 2026-05-28
**전 버전**: [PROJECT_PLAN.md](./PROJECT_PLAN.md) (v1, MVP 구조)
**상태**: 계획 확정, 실구현 진행 중

이 문서는 운영 표준안 + 케이스 A~H 점검 + AI manifest 추론 + 변경 명세서 자동화까지 모두 포괄한 v2 통합 계획서다.

---

## 0. 한 줄 정리

HEAXHub은 **사내 흩어진 자동화 프로그램을 한 곳에서 등록·실행·관리**하는 포탈이며, 다음 두 축으로 작동한다.

1. **표준 강제 축**: 신규 프로젝트는 `portal-app-template`으로 시작, `.portal/manifest.yaml` 기본 제공
2. **AI 추론 축**: 레거시 / 외부 repo는 AI가 manifest 초안 + 개발자에게 보낼 **변경 명세서**를 자동 생성 → 운영자가 검토 → GitHub PR 자동 생성

---

## 1. 지원 케이스 매트릭스 (점검 결과)

| # | 케이스 | 자동화 비율 | Tier |
|---|---|---|---|
| A | 풀스택 웹앱 (React+FastAPI 등) | 60% → 80% | 1 |
| B | 윈도우 GUI EXE 배포 | 20% → 50% | 3 |
| C | 상용 프로그램 임베드 (LS-DYNA / ANSYS / MATLAB) | 25% → 60% | 2 |
| D | 소스 없는 도구 (NAS ZIP, 외부 URL, 시스템 명령) | 15% → 50% | 1 |
| E | 인터프리터별 빌드 (Python/Node 다중 버전) | 90% | 1 |
| F | GPU 작업 | 70% | 2 |
| G | 장기 실행 데몬 (Streamlit/Jupyter/대시보드) | 75% | 1-2 |
| H | 사용자 PC 자동 설치 (Custom Protocol) | 30% | 3 |

자동화 비율은 **AI inferrer + 결정론적 분석 + 운영자 확인** 3단 조합 시 달성 목표.

---

## 2. 핵심 아키텍처 추가

기존 `PROJECT_PLAN.md` §2 아키텍처 위에 다음 컴포넌트가 추가된다.

```text
[Submission/Sync 흐름]
       │
       ▼
[Stage 1: Static Analyzer]        결정론적, 확정 사실만
       │
       ▼
[Stage 2: Manifest Inferrer]      LLM 기반, manifest 초안 + 신뢰도 + 누락 항목
       │
       ▼
[Stage 3: Change Request Builder] manifest, run.sh, README diff 등 패키지 생성
       │
       ▼
[Stage 4: Operator Review]        3단 비교 화면(정적/AI/운영자)
       │
       ▼ ┌─────────────────────────────────┐
        │ A. upstream에 PR 자동 생성        │
        │ B. Markdown 명세서 다운로드/메일  │
        │ C. overlay에만 적용 (개발자 응답 없음 케이스) │
        └─────────────────────────────────┘
       │
       ▼
[Webhook 수신 → re-sync → 자동 빌드 → 공개]
```

### 새 공통 인프라

1. **port_allocator** — 9100~9999 풀, DB 트랜잭션 기반 원자적 할당
2. **proxy_manager** — Caddy Admin API 동적 라우트 등록
3. **secret_manager** — AES-GCM 암호화, scope 단위 주입
4. **interpreter_pool** — Python/Node 버전별 매핑
5. **license_pool** — FlexLM/RLM feature 단위 토큰 큐
6. **gpu_pool** — `/dev/nvidiaN` 단위 점유
7. **change_request_service** — AI 분석 + 명세서 + GitHub 자동화
8. **windows_agent_protocol** — REST 폴링 + 헬스체크 + 결과 회수

### 새 라이프사이클 모드

- `launch.mode: job_runner` (단발성, 기존)
- `launch.mode: service` (장기 데몬, 신규 — 케이스 G)
- `launch.mode: url` (외부 링크, 기존)
- `launch.mode: remote_agent` (Windows Agent, 신규 — 케이스 B)
- `launch.mode: local_protocol` (custom protocol, 신규 — 케이스 H)

---

## 3. manifest schema v2 — 확장 요약

### 새 필드

```yaml
schema_version: 2

source:                          # 신규 — upstream_repo_url 대체
  type: git | archive_url | local_path | system_command | docker_image
  url: ...
  sha256: ...
  auth: { type: basic, secret_key: ... }
  sync: rsync | symlink | copy   # local_path 시

build:
  type: python_venv | nodejs | apptainer | compose | docker_build | none | external
  python_version: "3.11"         # interpreter_pool에서 선택
  node_version: "20"
  steps:                         # compose 일 때
    - kind: nodejs
      cwd: frontend
      install: pnpm install
      build: pnpm build
    - kind: python_venv
      cwd: backend
      requirements_file: requirements.txt

env_required:                    # 신규 — secret_manager 주입
  - DATABASE_URL
  - JWT_SECRET

license:                         # 신규 — license_pool 점유
  pool: lsdyna-mpp
  tokens: 4
  blocking: true

resources:
  cpu: 8
  memory_gb: 16
  gpu:
    count: 1
    min_memory_gb: 16
    cuda_min: "11.8"
  timeout_seconds: 1800

launch:
  mode: service
  command: ./.portal/run.sh
  health_check:
    type: http
    path: /health
    interval_seconds: 30
    timeout_seconds: 5
  ready_timeout_seconds: 60
  restart_policy:
    policy: on_failure
    max_attempts: 3
    backoff_seconds: 10
  base_path_aware: true          # Caddy /apps/{id}/ 마운트 지원

windows_requirements:            # 신규 — 케이스 B
  os_min: "10.0.19041"
  arch: x64
  vcredist: "2019"
  dotnet: "4.8"

installer_packages:              # 신규 — 케이스 B/H
  - os: windows-x64
    version: 1.4.2
    sha256: "..."
    url: /api/v1/apps/{id}/installers/windows-x64/1.4.2
```

기존 v1 manifest는 schema_version 1로 동작, 자동 마이그레이션 가능한 필드는 inferrer가 변환.

---

## 4. 데이터 모델 추가 (Alembic 0003)

| 테이블 | 목적 |
|---|---|
| `port_allocations` | 9100~9999 포트 풀 |
| `secret_values` | AES-GCM 암호화된 환경 변수 |
| `license_pools` | FlexLM/RLM 라이선스 풀 정의 |
| `license_holdings` | job별 토큰 점유 |
| `gpu_devices` | GPU 인벤토리 |
| `gpu_holdings` | job별 GPU 점유 |
| `service_instances` | 장기 데몬 인스턴스 트래킹 |
| `windows_agents` | Worker Agent 등록 |
| `installer_packages` | 윈도우 설치 파일 메타 |
| `change_requests` | AI 변경 명세서 발행 이력 |
| `interpreter_pool` | (config 파일 또는 테이블) Python/Node 버전 매핑 |

`apps.source_config JSONB`, `submissions.source_config JSONB` 컬럼 추가 — `upstream_repo_url`은 nullable로 완화.

---

## 5. AI Manifest Inferrer & Change Request

### 5.1 3-Stage 파이프라인

| Stage | 결정 주체 | 산출 |
|---|---|---|
| 1. Static Analyzer | 결정론적 코드 | 확실한 사실 (`languages`, `python_version`, `package_json_scripts`, `has_dockerfile`, `env_references`, ...) |
| 2. Manifest LLM | Claude/GPT/사내 LLM | `manifest_draft`, `confidence{field: 0~1}`, `open_questions[]`, `developer_change_request` |
| 3. Change Request Builder | 결정론적 포맷터 | Markdown 명세서 + JSON 패치 + PR payload |

### 5.2 안전장치

- LLM JSON 응답 schema 검증 + 재시도 3회
- `confidence < 0.8`인 필드는 자동 채택 안 함, 운영자 검토 강제
- `developer_change_request.required_files[].path`가 `.portal/`로 시작하지 않으면 reject
- 자동 PR은 운영자 봇 토큰, 자동 merge 절대 금지
- upstream 폴더는 `chmod -R a-w`로 잠금

### 5.3 GitHub Integration

운영자는 두 종류의 GitHub repo와 통합:

- **각 앱의 upstream repo** — 변경 명세서가 PR로 들어감
- **Integration test repo** — 데모용 (`INTEGRATION_REPO_URL` 환경변수)

### 5.4 LLM 호출 정책

- Claude Sonnet (기본) / GPT-4o-mini (옵션) / 로컬 모델 (사내망)
- 모델은 `.env`의 `LLM_PROVIDER` + `LLM_API_KEY`로 결정
- 같은 commit hash + 같은 static facts 면 캐시 재사용 (Redis)
- 사내 LLM 게이트웨이 endpoint를 사용하면 외부망 비활성화 가능

---

## 6. 케이스별 작업 분량 (Tier별)

### Tier 1 (1~2주, 즉시 가치)

- 케이스 E: 인터프리터 풀 (~2일)
- 케이스 D: source 추상화 (~4일)
- 케이스 A: 포트 할당 + Caddy + 빌드 일반화 (~6일)
- 케이스 G: service 모드 (~5일)
- AI inferrer + change request (8 + 3일, 중복 제거 후 ~11일)

### Tier 2 (1~2주)

- 케이스 C: secret_manager + license_pool + ApptainerRunner 실구현 (~6일)
- 케이스 F: gpu_pool (~3일)

### Tier 3 (2~3주)

- 케이스 B/H: Windows Agent + installer hosting + custom protocol (~9일)

### Tier 4 (1주)

- 보안 강화 (sandbox, cgroups, ulimit)
- 디스크 quota + 자동 아카이브
- CI/CD + E2E 자동화

**총 ~34.5 영업일**, 병렬 작업 시 **약 6-7주**.

---

## 7. 작업 분배 — 6개 서브에이전트

각 에이전트가 독립적으로 작업 가능한 영역으로 분리. 의존성은 최소화.

| # | 에이전트 영역 | 분량 | 의존성 |
|---|---|---|---|
| **SA1** | DB 마이그레이션 0003 + 모델 + 기본 스키마 v2 | 2일 | 없음 |
| **SA2** | 공통 인프라 (port_allocator, secret_manager, proxy_manager, interpreter_pool) | 3일 | SA1 |
| **SA3** | source 추상화 + manifest schema v2 + static_analyzer + LLM inferrer + change_request | 5일 | SA1 |
| **SA4** | ApptainerRunner 실구현 + license_pool + gpu_pool + service mode | 4일 | SA1, SA2 |
| **SA5** | Windows Agent (.NET self-contained) + installer hosting + custom protocol | 5일 | SA1 |
| **SA6** | 프론트엔드 신규 화면 6종 (Secrets/Licenses/GPU/Agents/Services/ChangeReq/Wizard) | 4일 | SA1~SA3 API |

---

## 8. GitHub Integration repo 자동화 흐름

```text
.env에 INTEGRATION_REPO_URL + GITHUB_BOT_TOKEN 설정
       │
       ▼
운영자가 /admin/integrations 에서 "테스트 신청 발행"
       │
       ▼
Submission 자동 생성 (upstream_repo_url = INTEGRATION_REPO_URL)
       │
       ▼
static_analyzer + LLM inferrer 자동 실행
       │
       ▼
운영자 검토 (`/admin/submissions/{id}/review`)
       │
       ▼
"PR 자동 발행" 버튼
       │
       ▼
PyGithub로 fork → branch → .portal/* 파일 commit → PR open
       │
       ▼
PR 본문 = change_request.markdown_body (이쁘게 포맷된 명세서)
       │
       ▼
개발자 merge 시 webhook 수신 → re-sync → 자동 빌드
```

운영자 화면에서 PR 상태(open/merged/closed) 실시간 확인.

---

## 9. 환경 변수 추가

```bash
# AI/LLM
LLM_PROVIDER=anthropic          # anthropic | openai | local
LLM_API_KEY=
LLM_MODEL=claude-sonnet-4-5
LLM_TEMPERATURE=0.0
LLM_MAX_TOKENS=8000

# GitHub 통합
INTEGRATION_REPO_URL=https://github.com/squall321/MXCAEGroupAutomationSample
GITHUB_BOT_TOKEN=               # Personal Access Token (repo, pull_request 권한)
GITHUB_BOT_USERNAME=heaxhub-bot
GITHUB_WEBHOOK_SECRET=

# Caddy reverse proxy
CADDY_ADMIN_URL=http://127.0.0.1:2019
APP_PORT_RANGE_LOW=9100
APP_PORT_RANGE_HIGH=9999
PUBLIC_HOST=hub.company.com

# Secret 암호화
SECRET_ENCRYPTION_KEY=          # Fernet base64 32 bytes

# 인터프리터 풀
INTERPRETERS_CONFIG=config/interpreters.yaml
```

---

## 10. 진행 순서 — 본 sprint

1. 본 계획서 작성 (이 문서)
2. `docs/CAPABILITY_MATRIX.md` + `docs/CHANGE_REQUEST_DESIGN.md`
3. Alembic 0003 마이그레이션
4. 서브에이전트 SA1~SA6 병렬 위임
5. 통합 검증

---

부록 — 케이스별 자동 추론 가능 영역과 LLM이 다루는 영역의 경계는 [`docs/CAPABILITY_MATRIX.md`](./docs/CAPABILITY_MATRIX.md)에 별도로 정리.
