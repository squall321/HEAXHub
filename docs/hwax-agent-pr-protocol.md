# HWAXAgent ↔ HEAXHub PR/이슈 협업 규약

문서 상태: Draft v1
대상 독자: HWAXAgent 레포 메인테이너, HEAXHub 백엔드/플랫폼 메인테이너
관련 문서: `docs/CHANGE_REQUEST_DESIGN.md`, `docs/MANIFEST_SPEC.md`, `docs/API_REFERENCE.md`

---

## §1. 협업 모델 개요

본 규약은 두 개의 서로 다른 레포지토리가 단일 계약(contracts)을 공유하면서
독립적으로 릴리스되는 환경을 가정한다.

| 항목 | HWAXAgent | HEAXHub |
|---|---|---|
| 레포지토리 | `koopark/HWAXAgent` (별도) | `koopark/HEAXHub` (본 레포) |
| 실행 환경 | Windows 10 / 11 (워크스테이션) | Linux 서버 (Ubuntu LTS / RHEL 호환) |
| 주요 책임 | 사용자측 launcher, 매니페스트 수집, 결과 업로드 | API 게이트웨이, 인증/세션, 잡 큐, 저장소, 감사 로그 |
| 배포 주기 | 사용자 PC 기준 수동 / 자동 업데이트 | 서버 컨테이너 롤아웃 (CI/CD) |
| 언어/스택 | Tauri 2 + Rust (core) + React/TypeScript (UI) | Python (FastAPI) + Celery + PostgreSQL |

두 레포 사이의 통신은 다음 세 가지 표면(surface)에 한해서만 일어난다.

1. **HTTP API**: `contracts/hwax-agent/openapi.yaml` 에 정의된 endpoint.
2. **Manifest schema**: `schemas/manifest.schema.json` 및 차기 버전.
3. **보안 계약**: `contracts/SECURITY.md` (JWT 만료, 회전 주기, mTLS 옵션 등).

세 표면 모두 **계약(contracts) 디렉터리에 단일 출처(single source of truth)** 로 존재하며,
HWAXAgent 측은 이를 git submodule 또는 release tarball 형태로 동기화한다.
따라서 모든 cross-repo 변경은 결국 본 레포의 contracts/ 또는 schemas/ 변경으로
환원된다.

---

## §2. 4가지 협업 시나리오와 PR 흐름

각 시나리오는 (1) 트리거, (2) 발의 측, (3) 절차, (4) 머지 후 후속 작업 순으로 기술한다.

### A. HWAXAgent가 새 endpoint 가 필요한 경우 → HEAXHub로 PR

**예시**: launcher가 사용자 PC의 GPU 정보를 미리 검사한 뒤 서버에 등록하려고
`POST /v1/agent/host-capability` 가 필요해진 상황.

**발의 측**: HWAXAgent 메인테이너.

**절차**

1. HWAXAgent 레포에서 issue 작성: `[contract] need POST /v1/agent/host-capability`.
   필드, 응답 코드, idempotency 키 정책 등 의도를 적는다.
2. HEAXHub 레포를 fork 하여 다음 두 변경을 한 PR 에 묶는다.
   - `contracts/hwax-agent/openapi.yaml` 에 endpoint 스펙 추가.
   - `backend/app/api/v1/agent/host_capability.py` 에 stub 핸들러 추가
     (501 Not Implemented 라도 무방). pydantic 스키마는 contracts 와
     1:1 매칭되어야 하며, 미스매치 시 `test_submission_schema_alignment.py`
     스타일의 정합성 테스트를 함께 추가한다.
3. PR 라벨: `hwax-agent`, `contracts`, `enhancement`.
4. HEAXHub 메인테이너가 리뷰. 책임자 매트릭스 §5 참고.
5. 머지 시 다음을 같은 PR 또는 후속 PR 로 수행:
   - `contracts/CHANGELOG.md` minor bump (예: `1.4.0 → 1.5.0`).
   - `contracts/VERSION` 파일 갱신.
   - `contracts-validate` 워크플로우 통과 확인.
6. 머지 후 HWAXAgent 측은 새 버전의 contracts 를 pull 하여 client 구현 PR 을 올린다.

**주의**

- endpoint stub 만 머지된 상태에서 HWAXAgent 가 real call 을 보내면 501 이 반환되므로,
  HWAXAgent client 구현 PR 머지 전까지 launcher 의 feature flag 로 호출을 막아둔다.
- breaking change (기존 endpoint 의 path/method/필수 필드 변경) 는 §6 의 SemVer 규칙을 따른다.

### B. HWAXAgent가 manifest field 가 필요한 경우 → HEAXHub로 PR

**예시**: launcher 가 사용자 워크스테이션의 라이선스 파일 경로를 manifest 에
실어 보내고자 `license_paths: string[]` 가 필요해진 경우.

**발의 측**: HWAXAgent 메인테이너.

**절차**

1. HWAXAgent 레포에서 issue 작성: `[schema] add manifest.license_paths`.
2. HEAXHub 레포 fork 후 단일 PR 에 다음을 묶는다.
   - `schemas/manifest.schema.json` (현행) 와 `schemas/manifest.schema.v2.json`
     (차기) 모두에 필드 추가. 한쪽만 추가하면 schema drift 가 발생한다.
   - 필드는 optional 로 시작한다. required 승격은 별도 PR 에서 deprecation
     기간을 둔다.
   - `integrations/*/manifest.yaml` 중 영향받는 데모 한두 개에 예제 값 추가.
   - 백엔드 측 `backend/app/schemas/submission.py` 의 pydantic 모델에도
     동일 필드 추가.
3. PR 라벨: `hwax-agent`, `contracts`, `schema-change`.
4. 머지 후 후속 작업:
   - `schemas/CHANGELOG.md` patch/minor bump.
   - alembic 마이그레이션이 필요한 경우 별도 PR.
   - HWAXAgent 측 manifest 빌더에 필드 노출.

**주의**

- field 추가는 항상 additive 로 시작한다. 기존 필드의 타입 변경,
  enum 값 제거는 breaking 으로 간주하며 §6 규칙 적용.

### C. HEAXHub가 서버 모델/스키마 변경 → HWAXAgent에 통지

**예시**: 서버 측에서 `Submission.status` 에 새 enum 값 `awaiting_artifact`
가 추가됨. launcher 가 이를 사용자에게 표시하려면 client 측 enum 도 확장해야 함.

**발의 측**: HEAXHub 메인테이너.

**절차**

1. HEAXHub PR 에서 모델/스키마 변경.
2. 같은 PR 내에 `contracts/CHANGELOG.md` 항목 추가. enum 추가는 minor,
   enum 제거/의미 변경은 major.
3. 머지 시 GitHub Actions `notify-hwax-agent` 워크플로우가
   `repository_dispatch` 이벤트를 HWAXAgent 레포로 송신한다. payload 에는
   contracts 버전, 변경 요약, 관련 PR URL 이 포함된다.
4. HWAXAgent 레포의 디스패치 핸들러가 자동으로 issue 를 생성한다
   (라벨: `incoming-contract-change`, `from-heaxhub`).
5. HWAXAgent 메인테이너가 issue 를 받아 client 측 변경 PR 을 올린다.

**주의**

- 자동 issue 생성이 실패해도 (예: 네트워크 일시 단절) contracts/CHANGELOG.md
  자체가 정본이므로 누락은 발생하지 않는다. workflow 는 best-effort.
- HEAXHub 측이 minor bump 했음에도 launcher 가 구버전 contracts 로 동작하는
  상황을 허용하려면, 서버는 unknown enum 을 graceful 하게 다루어야 한다
  (예: client 에는 `unknown` 으로 변환). 이 책임은 HEAXHub 측에 있다.

### D. HEAXHub가 보안 정책 변경 → HWAXAgent 측 client 변경 필요

**예시**: JWT access token 만료를 1 시간에서 15 분으로 단축. launcher 는
refresh 주기를 조정해야 하고, 백오프 정책도 변경되어야 함.

**발의 측**: HEAXHub 보안 책임자 (또는 백엔드 메인테이너).

**절차**

1. `contracts/SECURITY.md` 에 변경 사항 기재. 이전 값 → 신규 값, 시행 시점,
   grace period.
2. `contracts/CHANGELOG.md` 에 `security` 카테고리로 항목 추가. 만료 단축은
   client 호환성을 깨므로 minor 가 아니라 major 로 분류한다.
3. HWAXAgent 레포에 `[security] follow-up: refresh interval` 형태의 issue 를
   수동으로 생성한다 (자동화는 시나리오 C 와 동일한 dispatch 로 가능하지만,
   보안 변경은 사람의 확인이 권장된다).
4. HWAXAgent 측은 PR 로 launcher 의 refresh scheduler 를 수정. 머지 후
   사용자 PC 의 자동 업데이트가 grace period 안에 완료되는지 모니터링.
5. grace period 종료 시 HEAXHub 가 서버 측 enforcement 를 활성화 (feature flag flip).

**주의**

- 보안 변경은 grace period 가 종료될 때까지 두 정책 (구/신) 을 병행 수용해야
  하며, 이는 `backend/app/services/` 측 코드의 책임이다.
- 보안 정책 변경 PR 은 `security` 라벨 필수, 리뷰어 2 인 이상.

---

## §3. PR 체크리스트

### HWAXAgent 측 PR 체크리스트

- [ ] 변경된 contracts 버전이 HWAXAgent 빌드와 일치하는가
- [ ] 새 endpoint 호출은 feature flag 뒤에 숨겨졌는가 (서버 미배포 시 안전)
- [ ] manifest 신규 필드는 optional 로 처리되는가
- [ ] 사용자 PC 가 구버전 contracts 로 동작해도 crash 하지 않는가 (graceful degradation)
- [ ] launcher 자동 업데이트 채널 (stable / canary) 분리되었는가
- [ ] 변경 사항이 `CHANGELOG.md` 에 사용자 문구로 기록되었는가
- [ ] Win10 / Win11 양쪽에서 smoke test 가 통과했는가

### HEAXHub 측 PR 체크리스트

- [ ] contracts/ 또는 schemas/ 변경이 있다면 `contracts-validate` 워크플로우 통과
- [ ] OpenAPI 와 pydantic 모델이 1:1 정합 (정합성 테스트 추가/갱신)
- [ ] alembic 마이그레이션이 필요한지 확인 (필요 시 별도 PR 또는 동일 PR 명시)
- [ ] `contracts/CHANGELOG.md` 항목 추가 및 SemVer 규칙 (§6) 준수
- [ ] breaking change 시 HWAXAgent 측 follow-up issue 가 자동/수동으로 생성됨
- [ ] 보안 변경 시 grace period 가 정의됨
- [ ] 영향 범위 (영향받는 integrations/, 데모 매니페스트) 명시
- [ ] 리뷰어 매트릭스 (§5) 에 따른 reviewer 지정

---

## §4. 라벨 체계

| 라벨 | 용도 | 색상 (제안) |
|---|---|---|
| `hwax-agent` | HWAXAgent 관련 변경 전반 | `#5319e7` |
| `contracts` | contracts/ 또는 schemas/ 변경 포함 | `#1d76db` |
| `schema-change` | manifest/result/llm_response schema 변경 | `#0e8a16` |
| `breaking` | SemVer major 를 강제하는 변경 | `#b60205` |
| `security` | 보안 정책/암호화/인증 관련 | `#d93f0b` |
| `ux` | launcher 또는 portal 의 사용자 경험 변경 | `#fbca04` |
| `perf` | 응답 시간/메모리/디스크 IO 개선 | `#c5def5` |
| `incoming-contract-change` | 타 레포에서 dispatch 로 자동 생성된 이슈 | `#bfd4f2` |
| `from-heaxhub` / `from-hwax-agent` | 발의 레포 식별 | `#ededed` |

라벨은 양쪽 레포에서 동일한 슬러그로 유지한다.

---

## §5. 리뷰어 매트릭스

| 영역 | HEAXHub 측 책임자 | HWAXAgent 측 책임자 |
|---|---|---|
| OpenAPI / endpoint 정의 | @koopark (백엔드) | @koopark (launcher I/O) |
| Manifest schema | @koopark | @koopark |
| 인증 / JWT / 세션 | @koopark (보안) | @koopark |
| 잡 큐 / Celery / 결과 저장 | @koopark | — |
| Frontend portal | @koopark | — |
| 사용자 PC 패키징 / 코드 사이닝 | — | @koopark |
| 라이선스 / 외부 솔버 연동 | @koopark | @koopark |

향후 팀이 확장되면 본 표에 GitHub handle 을 추가한다. 변경 영역이 두 개 이상
에 걸치면 각 영역의 책임자 모두를 reviewer 로 지정한다.

---

## §6. 릴리스 동기화

contracts 는 SemVer 를 따른다.

| 변경 유형 | contracts bump | HEAXHub 강제 bump | HWAXAgent 강제 bump |
|---|---|---|---|
| 신규 endpoint 추가 (optional) | minor | minor | minor |
| 기존 endpoint 의 optional 필드 추가 | patch | patch | patch |
| 기존 endpoint 의 필수 필드 추가/삭제 | major | minor | major |
| enum 값 추가 | minor | patch | minor |
| enum 값 제거/의미 변경 | major | minor | major |
| 보안 정책 강화 (만료 단축, 알고리즘 교체) | major | minor | major |
| 문서/주석만 변경 | patch (또는 bump 없음) | bump 없음 | bump 없음 |

규칙

- contracts major bump 시 HEAXHub 는 최소 minor bump 를 강제한다.
  이는 운영팀이 "어느 서버 버전이 어떤 contracts major 와 호환되는지"를
  서버 버전만으로 판단할 수 있게 한다.
- contracts major bump 시 HWAXAgent 는 동일하게 major bump.
- HEAXHub minor 릴리스 노트에는 contracts 버전 범위 (`>=1.4, <2.0`) 가
  기재되어야 한다.

---

## §7. 충돌 해결

양쪽 레포가 동시에 contracts 를 다른 방향으로 수정하면, 늦게 머지되는
쪽이 다음 절차로 해소한다.

1. 머지 전 conflict 가 감지되면 즉시 PR 작성자에게 알린다 (`contracts-validate`
   워크플로우가 base 브랜치의 최신 contracts 와의 diff 를 비교).
2. 두 PR 의 의도를 비교한다. 의도가 서로 호환 가능하면 (예: 같은 endpoint 에
   서로 다른 optional 필드 추가) 늦은 PR 이 rebase 후 머지.
3. 의도가 충돌하면 (예: 한쪽은 필드 삭제, 한쪽은 필드 의미 변경) 다음 중
   하나를 택한다.
   - **a.** 동기 미팅 1 회 후 단일 통합 PR 로 재작성. 통합 PR 의 작성자는
     양쪽 레포 메인테이너 합의로 지정.
   - **b.** 한쪽 PR 을 보류하고, 머지된 쪽의 변경을 contracts 로 반영한 뒤
     보류 PR 을 그 위에 rebase.
4. 충돌이 자주 발생하는 영역은 별도의 sub-API 로 분리하거나, contracts 를
   기능 단위 파일로 쪼개는 것을 검토한다 (예: `contracts/hwax-agent/auth.yaml`,
   `contracts/hwax-agent/jobs.yaml`).
5. 충돌 해소 후 양쪽 PR 에 `resolved-via: <PR URL>` 코멘트를 남겨 추적성을 확보한다.

긴급 핫픽스 (서버 장애 상황) 의 경우 contracts 검증을 일시 우회할 수 있으나,
- 우회 사용 시 PR 에 `bypass-contracts` 라벨을 붙이고,
- 24 시간 내 contracts 정합성 회복 PR 을 의무화한다.

---

## 부록 A. 디스패치 워크플로우 의사 코드

```yaml
# .github/workflows/notify-hwax-agent.yml (제안, 본 PR 범위 밖)
on:
  push:
    branches: [main]
    paths: ['contracts/**', 'schemas/**']
jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - uses: peter-evans/repository-dispatch@v3
        with:
          token: ${{ secrets.HWAX_AGENT_DISPATCH_PAT }}
          repository: koopark/HWAXAgent
          event-type: heaxhub-contracts-changed
          client-payload: |
            { "sha": "${{ github.sha }}",
              "pr_url": "${{ github.event.head_commit.url }}" }
```

## 부록 B. contracts 디렉터리 레이아웃 (목표)

```
contracts/
  VERSION                 # 현재 contracts SemVer
  CHANGELOG.md            # 사람이 읽는 변경 이력
  SECURITY.md             # JWT 만료, 회전 주기, mTLS 옵션 등
  hwax-agent/
    openapi.yaml          # launcher ↔ hub HTTP API
    manifest.schema.json  # (또는 schemas/ 로 심볼릭)
    examples/
      submit.json
      result.json
```
