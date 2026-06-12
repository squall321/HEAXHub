# HEAXHub 파이프라인 로드맵

- 작성일: 2026-06-10
- 기준 브랜치: `merge/launcher-stack` (main 대비 launcher 스택 머지 진행 중)
- 입력: 5축 갭 분석 (stack-coverage / build-reliability / serving-routing / operator-ux / security-tenancy)
- 검증 수준: 적대적 검증을 통과한 confirmed findings 29건 + 미검증 후보 25건(부록 A)
- 관련 문서: `docs/NEXT_STEPS.md`, `docs/RUNBOOK.md`, `docs/ARCHITECTURE.md`

---

## ■ §0 한 줄 진단

> **HEAXHub의 제출→빌드→서빙 파이프라인은 "최초 1회 배포만 자동"인 Level 2 (부분 자동화)다.
> 변경 반영·장애 복구·공개 전이가 전부 수동(SQL/ssh/curl)이며, 그 원인의 대부분은
> "스캐너가 새 AppVersion 행이 생길 때만 빌드/런치를 호출한다"는 단일 구조 결함과
> "버튼/endpoint 자체가 없다"는 UI-API 미연결로 수렴한다.**

### 자동화 성숙도 레벨 정의 (수동 개입 지점 수 기준)

| 레벨 | 정의 | 상시 수동 개입 지점 |
|---|---|---|
| 1 | 전 구간 수동 (스크립트 직접 실행) | 10+ |
| **2** | **최초 배포만 자동, 변경·복구·공개는 수동** | **5~9** |
| 3 | push→반영 자동, 실패 복구는 반자동(버튼) | 2~4 |
| 4 | 자가 복구 + 웹 셀프서비스, 예외만 수동 | 1 |
| 5 | 무중단 배포 + 격리 + 알림까지 무인 운영 | 0 |

### 현재 = Level 2 판정 근거: 상시 수동 개입 5종 (전부 코드로 확인됨)

1. **manifest version bump 재빌드** — 스캐너가 새 AppVersion 행 생성 시에만 빌드 (`integrations_scanner.py:393-417`)
2. **AppVersion 행 SQL DELETE** — 동일 버전 행이 재빌드를 영구 차단, 삭제 API 부재
3. **alembic stamp 수동 복구** — 마이그레이션 파일 rename(0008/0009→0010/0011)으로 배포 DB orphan revision
4. **Caddy 라우트 수동 PATCH** — 라우트가 admin API 메모리에만 존재, 재동기화 endpoint 미노출 (현재도 데모 7개가 SPA에 흡수된 라이브 장애 상태)
5. **integration_launcher.launch 직접 호출** — launch를 부르는 API 0개, 죽은 인스턴스는 무한 다운타임

여기에 더해 **승인→공개 상태머신은 built에서 영구 정지**한다(`current_version_id` 미설정으로 publish 409 + UI 버튼 부재). 즉 "웹에서 끝까지 가는 경로"가 현재 존재하지 않는다.

**목표: Phase 1(2주) 완료 시 Level 3, Phase 2(4주) 완료 시 Level 4 진입.**

---

## ■ §1 확인된 갭 매트릭스

적대적 검증을 통과한 29건. 심각도는 검증 후 조정값 기준.

| ID | 축 | 제목 | 심각도 | 공수 | 의존성 | Phase |
|---|---|---|---|---|---|---|
| SRV-01 | serving | Caddy 라우트 전면 휘발 — 재주입 경로 부재 (라이브 장애 중) | critical | 2d | 없음 | P0 |
| SRV-02 | serving | 인스턴스 생존성 부재 — 죽은 데모 무한 다운타임 | critical | 1d | 없음 | P0 |
| SEC-01 | security | 서빙 격리 부재 — 데모가 호스트 net/fs 무제한 접근 | critical | 1w | 없음 | P2 |
| BLD-01 | build | 빌드 트리거가 '새 AppVersion 행'뿐 — 변경 감지·재시도 전무 | high | 1d | BLD-03 결합 | P0 |
| BLD-03 | build | build_status 빌드 전 SUCCESS 하드코딩 — 실패 미기록·알림 부재 | high | 1d | 없음 | P0 |
| SRV-05 | serving | 워치독 플래핑 — 매분 postgres/redis pkill -9 (SIGPIPE 오탐) | high | 1d | 없음 | P0 |
| UX-01 | ux | 상태머신 데드락 — current_version_id 미설정으로 publish 불가 | high | 0.5d | 없음 | P0 |
| UX-05 | ux | 운영 UI에 publish/test-run 버튼 부재 | high | 1d | UX-01 | P0 |
| UX-06 | ux | CR 발행 버튼 전부 422 — frontend-backend 계약 불일치 | high | 0.5d | 없음 | P0 |
| UX-04 | ux | clone/빌드 실패 시 고아 App row가 재제출 영구 차단 | high | 1d | 없음 | P0 |
| SEC-04 | security | 공개 인스톨러가 status/인증 게이트 없이 임의 app_id 서빙 | high | 1d | 없음 | P0 |
| STK-02 | stack | rust_actix argv `/server` vs 템플릿 `/app/server` 불일치 | high | 0.5d | 없음 | P0 |
| STK-04 | stack | nodejs_express.def pnpm 버전 미고정 (nextjs 기수정 버그 잠복) | medium | 0.5d | 없음 | P0 |
| BLD-02 | build | SIF 빌드가 beat task/uvicorn startup 동기 실행 — 큐·락 부재 | medium | 3d | BLD-01/03 | P1 |
| UX-03 | ux | 빌드 가시성 제로 — 로그 API·'내 신청' 페이지 부재 | high | 3d | BLD-03 | P1 |
| SEC-05 | security | 시크릿 주입 시그니처 불일치 — 매번 os.environ 폴백 | high | 3d | 없음 | P1 |
| STK-01 | stack | entrypoint 4중 정의 — stacks.yaml entrypoint 미사용 | medium | 2d | 없음 | P1 |
| STK-05 | stack | lockfile 전략 부재 — 없으면 하드 페일 (실패 전례 3회) | high | 1d | STK-04 | P1 |
| STK-03 | stack | dotnet_aspnet argv/산출물 이중 불일치 — /app/app.dll 미존재 | high | 1d | 없음 | P1 |
| STK-06 | stack | 모노레포 subpath가 SIF 빌드에 미적용 | medium | 1d | 없음 | P1 |
| SRV-04 | serving | cgroup 리소스 제한 전무 — 공유 호스트 통째로 위협 | high | 2d | 없음 | P1 |
| UX-07 | ux | LLM final_manifest가 제출/빌드로 환류되는 경로 없음 | high | 1d | 없음 | P1 |
| UX-02 | ux | AI 분석 미리보기 — 일반 사용자 403 + 빈 디렉터리 분석 | high | 2d | 없음 | P1 |
| STK-07 | stack | 7개 스택 미검증 + Django/Jupyter/Gradio 부재 | high | 1w+ | STK-01 권장 | P2 |
| SEC-02 | security | 빌드 샌드박스 부재 — %post 임의 코드 + 무타임아웃 | high | 2w | 없음 | P2 |
| SEC-03 | security | /apps/<slug>/ 라우트 인증 게이트 전무 — private 앱 직타 노출 | high | 1w | 없음 | P2 |
| SRV-03 | serving | 새 SIF가 영영 미배포 + 무중단 배포 부재 | high | 3d | SRV-01/02, BLD-03 | P2 |
| UX-08 | ux | 수동 개입 5종의 구조적 원인 — admin API 4종 부재 | high | 1w | SRV-01/02 일부 흡수 | P2 |
| UX-09 | ux | 개발자 셀프서비스 4종 전무 + admin 시크릿 dead UI | high | 2w | UX-03, BLD-02 | P2 |

◆ 심각도 분포: critical 3, high 22, medium 4. 공수 합계 약 11주(병렬화 전 기준).
◆ 검증되지 않은 medium/low 후보 25건은 부록 A 참조.

---

## ■ §2 로드맵 Phase 1 (P0, 1~2주) — 운영자 개입 0회로 가는 최소 경로

목표: **"git push → 5분 내 자동 반영"과 "웹 UI에서 승인→공개 완주"라는 두 임계점을 넘는다.**
총 공수 약 11d (1인 2주 또는 2인 1주). 항목 순서 = 권장 착수 순서.

### ▶ P0-1. SRV-02: 스캐너 unchanged 분기에서 launch 호출 — 죽은 인스턴스 자동 복구

가장 적은 변경(사실상 분기 1곳)으로 가장 많은 결함을 동시에 푼다. launch()에는 이미
already_running 멱등 프로브와 Caddy 재등록 코드가 있는데, unchanged 조기 반환 때문에
최초 등록 이후 영영 호출되지 않는 것이 무한 다운타임과 라우트 휘발의 공통 원인이다.

- **변경 파일**: `backend/app/services/integrations_scanner.py` (`_process_dir` unchanged 분기, 419-435행)
- **변경 내용**: `stack.launch_mode == 'service'`이면 unchanged여도 `_build_and_launch`(또는 launch만) 호출. launch는 살아있으면 no-op, 죽었으면 SIF 캐시 히트 후 재기동.
- ◇ 수용 기준 1: 데모 인스턴스를 `apptainer instance stop`으로 죽인 뒤, 다음 스캔(≤5분)에서 자동 재기동되고 `/apps/<slug>/`가 200을 반환한다.
- ◇ 수용 기준 2: 전체 데모가 healthy일 때 스캔 소요 시간이 기존 대비 10초 이내 증가에 그친다(프로브만 추가).

### ▶ P0-2. BLD-01 + BLD-03: 빌드 트리거 확장 + 빌드 상태 실기록

소스 push·manifest·.def 템플릿·stacks.yaml 변경이 전부 미감지되는 결함과,
빌드 전에 SUCCESS를 커밋하고 sif_path/build_log_path/git_commit_hash를 전부 NULL로
버리는 결함(라이브 DB 124행 전수 확인)을 한 PR로 묶는다.

- **변경 파일**: `backend/app/services/integrations_scanner.py`, `backend/app/services/integration_sif_builder.py` (`_hash_inputs`)
- **변경 내용**:
  - unchanged 분기에서도 빌드 경로 진입(P0-1과 동일 분기). `integration_fetcher`가 skipped/updated를 판정하고 SIF hash 센티널이 불변 시 no-op이므로 추가 비용은 5분마다 git fetch 1회.
  - `_hash_inputs`에 `json.dumps(asdict(stack_spec))` 추가 → stacks.yaml 변경 감지.
  - AppVersion을 `build_status=PENDING`으로 생성 → 빌드 시작 시 BUILDING → 성공 시 SUCCESS + `sif_path`/`build_log_path`/`git_commit_hash` 기록 → 실패 시 FAILED + 에러 tail 기록.
  - FAILED 버전은 다음 스캔에서 재빌드(자동 재시도 규칙). 실패 시 `mail_service`로 관리자 1통(동일 hash 중복 억제).
- ◇ 수용 기준 1: 데모 repo에 commit push 후 10분 내(스캔 5분 + 빌드) 새 SIF가 빌드되고 DB에 git_commit_hash가 기록된다.
- ◇ 수용 기준 2: 고의로 깨지는 manifest를 push하면 build_status=FAILED가 DB에 남고 메일 1통이 발송되며, 수정 push 후 자동 재빌드된다.
- ◇ 수용 기준 3: `SELECT count(*) FROM app_versions WHERE build_status='success' AND sif_path IS NULL`이 신규 행에 대해 0이다.

### ▶ P0-3. SRV-01: Caddy 라우트 reconcile 루프 + proxy-sync 엔드포인트

라우트의 desired state는 이미 `var/integration_state/*.json`에 있다(port, base_path, strip_prefix).
백엔드가 진실원본을 갖는 reconcile 방식으로 영속성과 stale 라우트 정리를 동시에 해결한다.

- **변경 파일**: `backend/app/workers/integration_tasks.py` (beat 태스크 신설), `backend/app/services/proxy_manager.py`, `backend/app/api/v1/admin.py`, `deploy/apptainer/start.sh`
- **변경 내용**:
  - 30~60초 beat 태스크: state 파일 순회 → `_is_healthy` 통과 항목에 대해 `proxy_manager.list_routes()`와 diff → 누락 라우트 멱등 재주입(PUT).
  - `caddy_registered` 불리언 폐기(스테일 진실원본).
  - `POST /api/v1/admin/integrations/proxy-sync` 신설, start.sh의 Caddy 기동 직후 호출 1줄 추가.
- ◇ 수용 기준 1: Caddy 프로세스를 kill 후 재기동하면 60초 내 전체 앱 라우트가 복원된다(현재: 부트스트랩 3개만 남고 영구 유실).
- ◇ 수용 기준 2: 현재 라이브 장애(데모 7개 SPA 흡수)가 배포 직후 자동 해소된다.
- ◇ 수용 기준 3: state 파일 기준 desired 라우트와 `list_routes()` 결과의 diff가 상시 0이다(§6 KPI-5).

### ▶ P0-4. SRV-05: 워치독 오탐 제거 — pkill -9 플래핑 중단

실제 원인은 `watchdog.sh:16`의 `set -o pipefail` + `grep -q` 조기종료로 ss가
SIGPIPE(rc=141)를 받아 port_listening이 항상 false가 되는 버그(라이브 9,997회 '복구' 실증).

- **변경 파일**: `scripts/watchdog.sh`
- **변경 내용**: (1) start.sh와 동일한 APPTAINER 해석 블록 공유(bare apptainer 금지), (2) `ss` 절대경로 해석 + 검사 도구 부재 시 복구 건너뛰고 WARN만, (3) 복구 후 5초 대기 → 재검사로 성공/실패 구분 로깅, 연속 3회 실패 시 복구 중단 + alert 라인, (4) `pkill -9` → `pg_ctl stop -m fast` 우선.
- ◇ 수용 기준 1: 정상 상태에서 24시간 동안 watchdog.log에 'recovered' 라인이 0회다(현재: 매분 1회).
- ◇ 수용 기준 2: postgres 프로세스 etime이 24시간 이상 유지된다(현재: 매분 SIGKILL로 30초).
- ◇ 수용 기준 3: postgres를 고의로 죽이면 1회 복구 후 재검증 성공 로그가 남는다.

### ▶ P0-5. UX-01: publish 시 current_version_id 자동 해결 — 상태머신 데드락 해제

- **변경 파일**: `backend/app/services/app_lifecycle.py` 또는 `backend/app/api/v1/submissions.py` (publish 경로), `backend/app/tests/`
- **변경 내용**: `publish_submission`에서 `current_version_id`가 None이면 해당 앱의 최신 `build_status=SUCCESS` AppVersion을 자동 해결. 테스트 fixture의 수동 주입 제거(`test_publish_route.py:123` — 갭을 가리던 코드).
- ◇ 수용 기준: clone→build→publish 전 구간 e2e 테스트가 fixture 수동 주입 없이 통과하고, BUILT 제출에 publish 호출 시 409가 아닌 200이 반환된다.

### ▶ P0-6. UX-06: CR 발행 계약 일치 — 422 전면 해소

- **변경 파일**: `frontend/src/lib/api/changeRequests.ts`, `frontend/src/components/` (ChangeRequestReview), `backend/app/api/v1/change_requests.py` (테스트만)
- **변경 내용**: `issue()`를 body `{ via }`로 수정, 응답 타입을 backend의 `{url, content}`에 맞춤, onSuccess 분기 수정. `remove()`는 미사용 dead code이므로 frontend에서 제거(또는 backend DELETE 라우트 추가 중 택1 — 제거 권장). POST /issue 경로 HTTP 레벨 테스트 추가.
- ◇ 수용 기준: UI에서 PR/Issue/Markdown 발행 버튼 3종이 모두 2xx로 동작하고 결과 URL이 화면에 표시된다.

### ▶ P0-7. UX-05: SubmissionQueue 상태머신 기반 버튼 — 웹에서 승인→공개 완주

- **변경 파일**: `frontend/src/components/admin/SubmissionQueue.tsx`, `frontend/src/lib/api/submissions.ts`
- **변경 내용**: 상세 패널을 상태 분기로 재구성 — pending/under_review→승인·반려, building→로그 보기(P1에서 활성화), built→테스트 실행·공개, failed→재시도(P0-8 연동). `submissionsApi.publish(id)` 추가, 기정의된 `testRun` 연결, building 상태 refetchInterval 폴링.
- ◇ 수용 기준: 운영자가 제출 1건을 **웹 UI만으로** 승인→빌드 대기→테스트 실행→공개까지 완주하고, 공개된 앱이 카탈로그와 `/apps/<slug>/`에 나타난다(curl/SQL 0회).

### ▶ P0-8. UX-04: 실패 제출 retry — SQL DELETE 강제 해소

- **변경 파일**: `backend/app/api/v1/submissions.py`, `backend/app/workers/sync_tasks.py`, `backend/app/services/app_lifecycle.py`
- **변경 내용**: `POST /submissions/{id}/retry` (admin) 신설 — FAILED submission의 기존 App/AppVersion row를 재사용하거나 정리 후 clone_upstream 재enqueue. 근본 수정으로 provision(App row commit)을 fetch 성공 이후로 이동 검토. SubmissionQueue FAILED 행에 '재시도' 버튼(P0-7과 연동).
- ◇ 수용 기준: 잘못된 repo URL로 제출→FAILED 후, URL 수정 없이 retry 버튼으로 재시도되고, 동일 app_id 신규 제출도 차단되지 않는다(SQL 개입 0회).

### ▶ P0-9. SEC-04: 공개 인스톨러 status 게이트 — 익명 바이너리 유출 차단

P0 중 유일한 보안 항목. 1d 공수로 즉시 막을 수 있는 노출 경로라 선행한다.

- **변경 파일**: `backend/app/api/v1/installers.py` (`public_latest` 338-368행, `public_download` 371-394행)
- **변경 내용**: by-id 경로(266행)와 동일하게 `is_servable_installer_app` 게이트 적용 — DRAFT/ARCHIVED 404, `visibility==company`이고 PUBLISHED인 앱만 익명 서빙. 다운로드 이벤트 audit_log 추가.
- ◇ 수용 기준: DRAFT/ARCHIVED/team/private 앱의 app_id로 public-latest/public-download 호출 시 404가 반환되고, 정상 다운로드는 audit_log에 남는다. `backend/app/tests/test_installers_download.py`에 게이트 회귀 테스트 추가.

### ▶ P0-10. STK-02 + STK-04: 실행 불가 스택 핫픽스 (rust 경로 + pnpm 핀)

근본 수정(STK-01 entrypoint 단일화)은 P1로 보내되, 첫 사용자가 즉사하는 두 곳만 먼저 막는다.

- **변경 파일**: `backend/app/services/integration_launcher.py` (620행 `["/server"]`→`["/app/server"]`), `backend/app/services/sif_templates/nodejs_express.def` (10-11행에 `corepack prepare pnpm@9.15.0 --activate`)
- ◇ 수용 기준: rust_actix·nodejs_express 최소 데모 repo로 SIF 빌드→서빙→200 응답까지 1회 통과(STK-07의 데모 자산으로 재사용).

### ◆ Phase 1 완료 정의 (Definition of Done)

- 수동 개입 5종 중 4종(재빌드 bump, AppVersion DELETE, Caddy PATCH, launch 직접 호출)이 불필요해짐. (alembic 규율은 P2 UX-08에서 문서화)
- "git push → 서비스 반영"이 무인으로 ≤10분.
- 운영자가 승인→공개를 웹에서 완주.
- watchdog 로그 오염 중단.

---

## ■ §3 Phase 2 (P1, 2~4주) — 신뢰성 / 가시성

목표: **P0가 만든 자동 경로를 "믿고 쓸 수 있게" 만든다** — 빌드를 API 프로세스에서 분리하고,
실패를 웹에서 보게 하고, 시크릿이 실제로 컨테이너에 흐르게 한다. 총 공수 약 21d.

### ▶ P1-1. BLD-02: 빌드 큐 분리 + per-slug 분산 락 (3d)

- 파일: `backend/app/workers/integration_tasks.py`, `backend/app/services/integrations_scanner.py`, `backend/app/main.py`, `Makefile`
- scan(수 초, 메타데이터 reconcile)과 build(수 분)를 분리: scan은 빌드 필요 slug만 산출해 `build_integration_task(slug)`를 전용 'builds' 큐로 enqueue. redis per-slug 락(`heaxhub:build:{slug}`, timeout 3600) + builds 워커 `--concurrency=1~2`. uvicorn startup에서는 등록만 하고 빌드는 enqueue만.
- ◇ 수용: 신규 스택 배포 시 API 기동이 빌드에 블로킹되지 않고(기동 ≤30초), 동일 slug 동시 빌드가 락으로 차단된 로그가 확인된다.

### ▶ P1-2. UX-03: 빌드 로그 웹 노출 + '내 신청' 페이지 (3d)

- 파일: `backend/app/api/v1/submissions.py`, `backend/app/api/v1/apps.py`, 스키마(AppVersionOut에 build_log_path), `frontend/src/` (/submit/mine 라우트, admin 로그 탭)
- `GET /submissions/{id}/build-log`, `GET /apps/{app_id}/versions/{vid}/build-log` (owner+admin, tail N줄). P0-2가 build_log_path를 DB에 기록하므로 작업량 적음.
- ◇ 수용: 제출자가 로그인 후 자기 제출의 상태 타임라인(pending→…→built/failed)과 실패 로그를 ssh 없이 본다.

### ▶ P1-3. SEC-05: 시크릿 주입 경로 수리 (3d)

- 파일: `backend/app/runners/resource_hooks.py`, `backend/app/services/integration_launcher.py`, `backend/app/services/secret_manager.py` 테스트
- `_inject_secrets`의 호출을 `inject_for_app(db, app_id, env_required)` 올바른 시그니처로 수정(현재 매번 TypeError→os.environ 폴백 = Fernet 저장소 우회 + 백엔드 환경변수 잡 노출). 반환 dict를 env에 merge. os.environ 폴백은 제거 또는 명시적 dev 플래그 한정. service-mode에도 `env_required→inject_for_app→instance_start(env=...)` 경로 추가(`APPTAINERENV_` 주입은 apt_runner.py:186-194가 이미 지원).
- ◇ 수용: 시그니처 회귀 단위 테스트 + env_required 선언 데모가 service/job 양 모드에서 Fernet 저장 값으로 기동된다.

### ▶ P1-4. STK-01: entrypoint 단일화 — 4중 정의 제거 (2d)

- 파일: `backend/app/services/integration_launcher.py` (`_sif_argv_for` 543-623, `_argv_for` 768-1014, `_PREFIX_AWARE_STACKS` 72-83), `config/stacks.yaml`
- 기본 경로를 `["/bin/sh", "-lc", spec.entrypoint]`로 — PORT/ROOT_PATH는 이미 APPTAINERENV_*로 주입됨(apt_runner.py:262-267). 해석 우선순위: manifest.launch.command > stacks.yaml entrypoint > 스택별 오버라이드. `prefix_aware`도 stacks.yaml 필드로 이관. 이후 신규 스택 추가가 'stacks.yaml 1엔트리 + .def 1개'로 줄어 STK-07 비용이 절반 이하가 된다.
- ◇ 수용: 데모 15개 재기동 스모크 테스트 전체 통과 + `.entrypoint` 소비처가 단일 함수 1곳.

### ▶ P1-5. STK-05: lockfile 3단 fallback (1d)

- 파일: `backend/app/services/sif_templates/{nextjs,fastapi_react,nodejs_express}.def` (공통 스니펫 추출)
- %post 분기: (1) package.json `packageManager` 필드 우선 corepack prepare, (2) pnpm-lock.yaml 있으면 frozen 시도→실패 시 `--no-frozen-lockfile` 1회 fallback + 경고 마커, (3) lockfile 없으면 처음부터 no-frozen. npm/yarn lockfile만 있으면 해당 매니저 폴백. pnpm 버전 핀은 `config/stacks.yaml` 한 곳.
- ◇ 수용: lockfile 삭제 데모 + pnpm10 lockfile 데모 2개 픽스처 빌드 통과 (데모 repo의 shamefully-hoist 워크어라운드 제거 가능 확인).

### ▶ P1-6. STK-03: dotnet_aspnet 산출물 통일 (1d)

- 파일: `backend/app/services/sif_templates/dotnet_aspnet.def`, `backend/app/services/integration_launcher.py` (614-616행), `config/stacks.yaml` (165행)
- %post에서 .deps.json 짝이 있는 entry dll 탐지 → `/app/publish/app.dll` 심볼릭 링크. launcher/stacks.yaml 모두 해당 경로로 통일. dotnet 데모 1개 추가 검증.

### ▶ P1-7. STK-06: 모노레포 subpath 결합 (1d)

- 파일: `backend/app/services/integrations_scanner.py` (`_fr_to_dict` workspace 필드 보존), `backend/app/services/integration_sif_builder.py` (`_upstream_dir`→`managed_workspaces.upstream_dir(slug, subpath)`)
- ◇ 수용: `source.subpath: app/` 구조 픽스처로 SIF 빌드→서빙 통과.

### ▶ P1-8. SRV-04: 리소스 제한 1단계 (2d)

- 파일: `backend/app/services/apt_runner.py`, `backend/app/services/integration_launcher.py`, manifest 스키마(`resources: {memory_mb, cpus}`)
- 즉효: instance_exec argv를 `/bin/sh -c 'ulimit -v <kb>; exec ...'`로 래핑. 정공: dbus 가용 시 systemd cgroups + `--memory/--cpus`, 불가 시 `systemd-run --user --scope -p MemoryMax=` 폴백. 적용 여부를 state에 기록해 admin UI '무제한 실행 중' 경고.
- ◇ 수용: 메모리 폭주 데모가 호스트 OOM 대신 자기 한도에서 죽는다 (`/proc/<pid>/limits` 실측).

### ▶ P1-9. UX-07: LLM final_manifest 환류 (1d)

- 파일: `backend/app/api/v1/change_requests.py`, `backend/app/services/change_request.py`, frontend ChangeRequestReview
- `POST /change-requests/{id}/apply-to-submission`: final_manifest를 연결 Submission.proposed_manifest에 복사 + 상태 전환. '제출서에 적용' 버튼.

### ▶ P1-10. UX-02: AI 미리보기 수리 (2d)

- 파일: `backend/app/services/change_request.py` (`_resolve_workspace`), 스키마(ChangeRequestCreate에 source_config), 권한(CurrentUser 허용 + rate limit)
- create_draft 진입 시 shallow clone(depth=1) 후 분석. 또는 단순화 대안: 제출 시 미리보기를 제거하고 승인 후 overlay_synthesizer 결과를 제출자에게 보여주는 방식으로 일원화(구현 전 1시간 스파이크로 택1).

### ▶ P1-11. [후보] 원자적 SIF 교체 + 빌드 타임아웃 (0.5d)

- 부록 A의 BLD-C1(미검증이지만 코드 인용이 명확). `apptainer build --force`가 기존 SIF 자리에 직접 빌드해 실패 시 정상 이미지가 소실되는 문제 — tmp 경로 빌드 후 `os.replace` 원자 교체, `run_build(timeout=600)` 지정.
- ◇ 수용: 빌드를 강제 실패시켜도 기존 SIF로 서빙이 유지된다.

### ▶ P1-12. [후보] 알림 최소 연결 (0.5d)

- 부록 A의 UX-C2. P0-2의 빌드 실패 메일을 제출 상태 전이(built/failed/published)에도 확장 — 제출자에게 mail_service 1통씩.

### ▶ P1-13. [후보] webhook 빌드 트리거 (1d)

- 부록 A의 BLD-C2. 현재 webhook은 태그 audit_log만 남기고, scanner가 upstream_repo_url을 `file://`로 저장해 매칭 불가. 정규화된 repo URL 저장 + webhook 수신 시 해당 slug 빌드 task enqueue(P1-1 큐 재사용). 반영 지연을 5분(폴링)→수 초로 단축.
- ◇ 수용: 데모 repo push 시 webhook 경유로 5분 폴링을 기다리지 않고 빌드가 시작된다.

### ◆ Phase 2 완료 정의

- 빌드가 API/beat에서 분리되어 기동·스캔이 빌드에 블로킹되지 않음.
- 제출자/운영자가 빌드 로그·상태를 전부 웹에서 봄. ssh 디버깅 0회.
- 시크릿이 Fernet 저장소→컨테이너로 정상 주입. os.environ 폴백 제거.
- 신규 스택 추가 비용이 "stacks.yaml 1엔트리 + .def 1개"로 감소.

---

## ■ §4 Phase 3 (P2, 1~2개월) — 스택 확장 + 보안 격리 + 무중단 배포

목표: **외부(타 팀) 제출을 받아도 안전한 멀티테넌트 플랫폼.** 총 공수 약 8주 — 2개 트랙 병렬 권장
(트랙 A: 보안 격리, 트랙 B: 스택·배포·셀프서비스).

### ▶ P2-1. SEC-01: 서빙 격리 (1w) — 트랙 A 최우선

- 파일: `backend/app/services/apt_runner.py` (instance_start/instance_exec), `deploy/apptainer/start.sh` (Caddy admin)
- `--contain --no-home --writable-tmpfs` + 네트워크 네임스페이스 분리(데모는 0.0.0.0:$PORT 리슨이 필요하므로 network none이 아닌 별도 net ns/ptp로 호스트 127.0.0.1의 Caddy admin(2019)·DB·Redis 도달 차단). 동시에 Caddy admin을 unix socket으로 이전.
- ◇ 수용: 데모 컨테이너 내부에서 `wget 127.0.0.1:2019/config/`·redis 6379·backend 4040이 전부 도달 불가, 호스트 $HOME 미노출. 기존 데모 15개 정상 서빙 유지.
- ◆ 부록 A의 SEC-C4(ENTRYPOINT 셸 치환)/SEC-C5(shell=True)는 이 격리가 선결되어야 실질 완화됨.

### ▶ P2-2. SEC-02: 빌드 샌드박스 (2w) — 트랙 A

- 파일: `backend/app/services/integration_sif_builder.py`, `backend/app/services/apt_runner.py` (run_build)
- 빌드 타임아웃(P1-11에서 선반영), 네트워크 격리 빌드(의존성 vendoring 후 `--net none` 또는 egress allowlist), pip/pnpm 신뢰 인덱스 고정, lockfile+해시 검증, npm `--ignore-scripts` 검토, builder cgroup(메모리/CPU/디스크).
- ◇ 수용: `%post`에서 무한 sleep·외부 임의 호스트 접속을 시도하는 악성 픽스처가 타임아웃/네트워크 차단으로 실패 처리되고 빌드 호스트에 흔적이 없다.

### ▶ P2-3. SEC-03: /apps 라우트 forward_auth (1w) — 트랙 A

- 파일: `backend/app/services/proxy_manager.py` (_build_route/_build_static_route handle_chain 선두), `backend/app/api/v1/apps.py` (GET /apps/{id}/_authz)
- 각 app 라우트에 forward_auth prepend — 백엔드 authz 서브리퀘스트(세션쿠키/JWT + assert_view) 2xx일 때만 프록시. 최소한 visibility!=company 앱은 강제.
- ◇ 수용: team/private 데모를 만들어 비로그인 직타 시 401/302, 권한 사용자는 200.

### ▶ P2-4. STK-07: 스택 검증 완주 + 신규 3종 (1w + 3d) — 트랙 B

- (1) 미검증 5개(nodejs_express, java_springboot, dotnet_aspnet, rust_actix, shiny_for_python)에 최소 데모 추가 — "미검증 스택 = 동작 안 하는 스택" 패턴(4/4 재현)을 데모로 끊는다. 스택당 0.5d.
- (2) 신규 수요순: Gradio·Jupyter/Voila(python_venv 빌더 + streamlit 패턴 복제, 각 1d), Django(flask/gunicorn 패턴 복제, 1d) — CAE·ML 사용자층 대비 최대 커버리지 갭.
- (3) `config/stacks.yaml`의 examples 필드를 실데모와 일치시켜 검증 기록으로 복원(현재 9곳 불일치).
- ◇ 수용: 22+3개 스택 전부 "데모 1개 이상 SIF 빌드→서빙 200" CI급 스모크 체크리스트 통과. examples 필드와 실데모 100% 일치.

### ▶ P2-5. SRV-03: 새 SIF 반영 보장 → blue-green (3d) — 트랙 B

- 파일: `backend/app/services/integration_launcher.py` (재사용 분기 384-408), state 스키마 v3, `backend/app/services/port_allocator.py`
- 1단계(0.5d): state에 build_hash 저장, 재사용 분기에서 SIF hash 센티널 비교 → 불일치 시 재기동(다운타임 ~20s 수용).
- 2단계(+2.5d): 불일치 시 `heax_app_<slug>_<hash8>` 새 인스턴스+신규 포트 기동 → 헬스 통과 후 라우트 원자 스왑(@id PUT) → 구 인스턴스 drain 후 정리.
- ◇ 수용: 새 commit push 후 다음 스캔 주기 내 신버전 응답으로 바뀌고, 스왑 중 연속 curl 루프에서 5xx가 0건.

### ▶ P2-6. UX-08: admin API 4종 + alembic 규율 (1w) — 트랙 B

- 신설: `POST /admin/integrations/{slug}/rebuild`(센티널·캐시 무효화 후 강제 빌드), `POST /admin/integrations/{slug}/relaunch`, `POST /admin/proxy/sync`(P0-3 reconcile의 수동 트리거로 사실상 완료), `DELETE /admin/apps/{id}/versions/{vid}`. admin/integrations 페이지 행별 버튼.
- alembic: "merge 시 down_revision 충돌이면 `alembic merge` revision 생성, 기존 파일 rename 금지"를 CONTRIBUTING에 명문화 + CI에서 `alembic history` 단일 head 검증(이번 0008/0009 rename 사고 재발 방지).
- ◇ 수용: §0의 수동 개입 5종이 전부 웹 버튼 또는 자동 경로로 대체 — Level 4 진입 조건.

### ▶ P2-7. UX-09: 개발자 셀프서비스 4종 (2w) — 트랙 B

- `can_manage_app(owner)` 권한 기반: `POST /apps/{id}/rebuild`, `GET .../build-log`(P1-2 재사용), `PUT /apps/{id}/env`(secret_manager app-scope — set_secret 호출자 0개 문제 해소), `POST .../versions/{vid}/promote`(롤백 겸용). 앱 상세 owner 전용 '관리' 탭. `/admin/secrets` dead UI는 backend CRUD 실구현 또는 제거 중 택1.
- ◇ 수용: 앱 소유 개발자가 재빌드·로그·환경변수·버전 롤백을 운영자 호출 없이 수행.

### ◆ Phase 3 완료 정의

- critical 3건 전부 해소 (SRV-01/02는 P0, SEC-01은 P2).
- 전 스택 데모 검증 + Django/Jupyter/Gradio 서비스 가능.
- 배포 무중단, 수동 개입 상시 0회 — Level 4~5.

---

## ■ §5 명시적 비목표 (지금 안 할 것들과 이유)

| 항목 | 보류 이유 |
|---|---|
| **Rails / Laravel / Deno / Bun 스택** | 사내 수요 미확인. STK-01 이후 추가 비용이 절반 이하로 떨어지므로 수요 확인 후가 더 싸다. |
| **compose(다중 컨테이너) 지원** | 스키마만 허용되고 구현 0줄. 현재 모든 데모가 단일 컨테이너이고, 멀티 컨테이너 수요가 생기면 격리(SEC-01) 설계와 함께 다뤄야 해 지금 손대면 두 번 만든다. |
| **빌드 캐시 (apt/pip/pnpm/maven/cargo)** | 빌드 시간 문제는 불편이지 장애가 아니다. BLD-02 큐 분리로 빌드가 API를 막지 않게 되면 체감 비용이 급감. P2 이후 별도 검토(부록 STK-C3). |
| **멀티스테이지 .def 전면 전환** | 이미지 크기(r 319MB vs go 33MB)는 디스크 문제일 뿐. SEC-02 샌드박스 작업 시 템플릿을 어차피 손대므로 그때 동반 적용(부록 STK-C4). |
| **SEC-C4/C5 (ENTRYPOINT 셸 치환, shell=True)** | SEC-01/02 격리가 선결되지 않으면 고쳐도 실질 완화가 안 된다는 검증 결론. P2 격리 완료 후 후속. |
| **Kubernetes/컨테이너 오케스트레이터 이관** | 현 규모(데모 ~20개, 단일 호스트)에서 apptainer+Caddy+reconcile로 충분. 운영 부담 대비 이득 없음. |
| **LLM 자동 manifest 생성의 완전 자동화** | 스텁→awaiting_assistant 수동 핸드오프는 유지. P1-9의 '제출서에 적용' 환류만 연결하고, 사내 LLM 게이트웨이(LLM_LOCAL_ENDPOINT) 도입은 인프라 결정 사항이라 본 로드맵 범위 밖. |
| **외부 인터넷 공개 서빙** | 본 로드맵의 보안 목표는 사내 멀티테넌트까지. 외부 공개는 SEC 전 항목 + 침투 테스트 후 별도 프로젝트. |

---

## ■ §6 측정 지표 (KPI)

각 Phase 완료 판정은 아래 지표로 한다. 측정은 기존 audit_log/DB/로그 기반으로 자동 수집 가능한 것만 채택.

| # | 지표 | 현재 (실측) | P0 목표 | P1 목표 | P2 목표 | 측정 방법 |
|---|---|---|---|---|---|---|
| KPI-1 | git push → 서비스 반영 시간 | ∞ (수동 bump 필요) | ≤10분 | ≤3분 (webhook) | ≤3분 + 무중단 | push 시각 vs 신버전 응답 시각, 주 1회 카나리아 push |
| KPI-2 | 월 수동 개입 횟수 (SQL/ssh/curl) | 상시 5종, 월 10+회 추정 | ≤2회 | ≤1회 | 0회 | RUNBOOK 개입 기록 + audit_log 대조 |
| KPI-3 | 빌드 성공률 / 실패 기록률 | 측정 불능 (전 행 success 허위) | 실패 100% DB 기록 | 성공률 ≥95% | ≥98% | `app_versions.build_status` 집계 |
| KPI-4 | 데모 인스턴스 MTTR | ∞ (무한 다운타임) | ≤5분 | ≤5분 | ≤1분 (경량 liveness) | 인스턴스 kill 카오스 테스트 월 1회 |
| KPI-5 | Caddy 라우트 정합성 (desired vs actual diff) | 7/10 유실 중 | 상시 0 | 0 | 0 | reconcile 태스크가 diff 건수를 메트릭 로그로 출력 |
| KPI-6 | 미검증 스택 수 (examples 불일치 포함) | 7개 미검증 / 9곳 불일치 | 5 / 7 | 5 / 0 | 0 / 0 | stacks.yaml examples vs 라이브 데모 대조 스크립트 |
| KPI-7 | watchdog 허위 복구 횟수/일 | 1,440회 (매분) | 0 | 0 | 0 | watchdog.log 'recovered' grep |
| KPI-8 | 익명 접근 가능한 비공개 자원 수 | 인스톨러 전 앱 + /apps 전 라우트 | 인스톨러 0 | 인스톨러 0 | 0 (forward_auth) | 비로그인 크롤 스크립트 |
| KPI-9 | 시크릿 주입 정상 경로 비율 | 0% (전부 os.environ 폴백) | - | 100% | 100% | 주입 단위 테스트 + audit_log |
| KPI-10 | 격리 플래그 적용 인스턴스 비율 | 0% (limits 전부 unlimited) | - | ulimit 100% | cgroup+netns 100% | state 파일 적용 기록 + /proc 실측 |

◆ 운영 원칙: KPI-1/4/5/7은 P0 머지 직후부터 주간 리포트로 추적. 회귀(예: 라우트 diff > 0)가 이틀 연속이면 신규 기능 작업보다 우선 수선.

---

## ■ 부록 A — 미검증 후보 findings (적대적 검증 미통과, 착수 전 재검증 필요)

전체 54건 중 검증 통과 29건(§1)을 제외한 후보 목록. 심각도는 원 분석 주장값이며 확정이 아니다.
P1-11/12/13처럼 로드맵에 선반영한 항목은 표기했다.

### ◇ stack-coverage 후보

| ID | 제목 | 주장 심각도 | 비고 |
|---|---|---|---|
| STK-C1 | floating 베이스 이미지(rust:1-slim, debian:stable-slim) + NodeSource curl\|bash — 재현성·공급망 위험 | medium | SEC-02 작업 시 베이스 pin과 함께 처리 권장 |
| STK-C2 | r_script `\|\| true`가 의존성 설치 실패를 은폐하고 캐시 sentinel까지 기록 | medium | 실패가 성공으로 캐시되는 패턴 — BLD-03 사상과 동류 |
| STK-C3 | 빌드 캐시 전무 — 매 빌드 apt/pip/pnpm/maven/cargo 풀다운로드 | medium | §5 비목표(보류) |
| STK-C4 | 멀티스테이지 go_service 1곳뿐 — r 319MB, nextjs 193MB vs go 33MB | low | §5 비목표(SEC-02 동반 적용) |
| STK-C5 | apt 시스템 패키지 선언 채널 부재 — slim 이미지에서 opencv/vtk류 즉사 예정 | medium | CAE 수요 발생 시 P2-4와 함께 (manifest `system_packages` 필드) |
| STK-C6 | 스키마가 compose를 허용하나 구현 전무 | low | §5 비목표 |
| STK-C7 | stacks.yaml examples 필드 9곳 실데모 불일치 | low | P2-4 (3)에 흡수 |

### ◇ build-reliability 후보

| ID | 제목 | 주장 심각도 | 비고 |
|---|---|---|---|
| BLD-C1 | `apptainer build --force` 제자리 빌드 — 실패 시 기존 SIF 소실 + 빌드 타임아웃 부재 | high | **P1-11로 선반영** (코드 인용 명확) |
| BLD-C2 | webhook이 태그 audit_log만 기록 + upstream_repo_url `file://` 저장으로 매칭 불가 | medium | **P1-13으로 선반영** |
| BLD-C3 | 빌드 자원 통제 부재 (동시성 제한·디스크 체크·tmpdir) | medium | BLD-02(동시성) + SEC-02(cgroup)에 분산 흡수 |
| BLD-C4 | 사설 git 토큰 인증 미지원 + URL 내장 토큰 DB 평문 유출 경로 | medium | SEC-C3과 동일 사안 — 사설 repo 수요 발생 시 착수 |
| BLD-C5 | stack entrypoint fallback 미구현 | low | STK-01(P1-4)에 흡수 |

### ◇ serving-routing 후보

| ID | 제목 | 주장 심각도 | 비고 |
|---|---|---|---|
| SRV-C1 | 크래시-재기동 시 포트 할당 행 누수 | medium | SRV-03 2단계(포트 할당기 개편) 때 동반 수선 |
| SRV-C2 | 얕은 헬스체크 — 404도 healthy 판정 | medium | reconcile(P0-3)이 의존하므로 P1 초에 재검증 권장 |
| SRV-C3 | 인스턴스 로그 웹 접근·로테이션 부재 | low | UX-03(P1-2)의 로그 API에 인스턴스 로그 탭으로 확장 검토 |

### ◇ operator-ux 후보

| ID | 제목 | 주장 심각도 | 비고 |
|---|---|---|---|
| UX-C1 | 온보딩 문서 schema_version 1 vs 2 드리프트 | low | 문서 수정 0.5h — P0 머지 시 같이 처리해도 무방 |
| UX-C2 | 알림 전 구간 0건 (제출·빌드·공개 어디서도 메일/웹훅 없음) | medium | **P1-12로 선반영** (P0-2 실패 메일이 첫 단추) |

### ◇ security-tenancy 후보

| ID | 제목 | 주장 심각도 | 비고 |
|---|---|---|---|
| SEC-C1 | audit_log 미커버리지 — approve_and_provision·scanner 빌드 무기록 | medium | KPI-2 측정 정확도를 위해 P1 중 저비용 삽입 권장 |
| SEC-C2 | 제출/빌드 트리거 rate-limit + SIF/스토리지 디스크 quota 부재 | medium | 외부 팀 제출 개방(P2) 전 필수 재검증 |
| SEC-C3 | scanner 사설 git 토큰 안전 보관 미구현 | medium | BLD-C4와 동일 사안 |
| SEC-C4 | manifest 유래 ENTRYPOINT 무이스케이프 셸 치환 | medium | SEC-01/02 선결 후 착수 (§5 참조) |
| SEC-C5 | system_command 소스의 shell=True 호스트 명령 실행 | medium | SEC-01/02 선결 후 착수 (§5 참조) |

◆ 후보 착수 규칙: 후보 항목은 **착수 전 30분 재현 스파이크**(코드 인용 행 확인 + 가능하면 라이브 재현)를 통과해야 백로그에 승격한다. 재현 실패 시 폐기하고 본 문서에서 제거.

---

## ■ 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-06-10 | 최초 작성 — 5축 갭 분석(검증 29건 + 후보 25건) 종합 |
