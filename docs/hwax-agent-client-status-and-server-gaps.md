# HWAXAgent 클라이언트 빌드 완료 — 서버 구현 갭 (런처 측 피드백)

문서 상태: v1 (HWAXAgent 런처 측에서 작성)
작성일: 2026-06-07
대상 독자: HEAXHub 백엔드 담당자
관련 문서:
- `docs/NEXT_STEPS.md` — 서버측 우선순위 매트릭스 (P0×6). **이 문서는 그 보완.**
- `docs/hwax-agent-backend-plan.md` — 서버측 상세 계획 (모(母)문서)
- `docs/hwax-agent-split-strategy.md` §6.1 — HWAXAgent→HEAXHub 피드백 흐름
- `contracts/hwax-agent/openapi.yaml` — 계약 (PR #1 머지 반영)

---

## §0. 한 줄 현황

**런처(HWAXAgent)는 이제 "부트스트랩 진행 중"이 아니라 빌드·패키징·실행검증까지 끝났다.**
서버측 `NEXT_STEPS.md` §2 의 **P0 6건이 아직 미구현**이라 실서버↔런처 E2E 가 막혀 있고,
PR #1 로 계약에 추가된 **`/api/v1/installers/{app_id}/latest` (Tauri updater feed) 가
백엔드 task 목록에 빠져 있다**. 본 문서는 *빌드된 클라이언트가 실제로 호출하는 표면*을
서버 task 에 매핑하고, 현 플랜에 없는 갭 3건을 적는다.

> 런처 레포는 **`squall321/HWAXLauncher`** (NEXT_STEPS §5 의 `koopark/HWAXAgent` 는 갱신 필요).
> 14 커밋, Tauri 2 + Rust + React, 코어 40 테스트 통과, 실제 NSIS 설치파일 생성 + 트레이 기동
> 실행검증 완료. **미검증은 단 하나 — 실서버 페어링/설치 1사이클(서버 P0 대기).**

---

## §1. 런처가 실제로 호출하는 엔드포인트 (client call inventory)

런처 소스 기준(`apps/agent/src-tauri/src/{auth,http,ws,telemetry}.rs`, `tauri.conf.json`).
"서버 task" 열은 `NEXT_STEPS.md` 의 항목 번호.

| 호출 (method path) | 호출부 | 인증 | 비고 / 서버가 보장해야 할 것 | 서버 task |
|---|---|---|---|---|
| POST `/api/v1/launcher-agents/enroll` | auth.rs | 없음(부트스트랩) | enrollment_token→{agent_id, access, refresh, expires_in} | §2.4 (P0, 실구현) |
| POST `/api/v1/launcher-agents/refresh` | auth.rs (401 시 1회 자동) | 없음 | refresh 회전 → 새 access(+rotated refresh) | §2.4 (P0) |
| GET `/api/v1/launcher-agents/manifest?os=windows-x64` | http.rs | Bearer aud=`hwax-agent` | **`If-None-Match` 보냄 → 304 처리함.** 200 시 **`ETag` 응답 헤더 필수**(런처가 캐시 키로 저장) | §2.3+§2.4 (P0) |
| GET `/api/v1/installers/{id}/download` | http.rs | Bearer aud=`hwax-agent` | 302→presigned. 런처가 redirect 따라가고 sha256 검증 | §2.5 (P0) |
| POST `/api/v1/launcher-agents/installs` | telemetry.rs | Bearer | install-report.schema 본문. Phase1 은 202 stub 도 OK | §3.1 (P1) |
| POST `/api/v1/launcher-agents/audit` | telemetry.rs (5분 배치 + 즉시) | Bearer | audit-event.schema 본문(단건). 202 | §3.1 (P1) |
| POST `/api/v1/launcher-agents/heartbeat` | lib.rs (30분) | Bearer | {agent_version, hostname?, modules[]} → 204 | §3.1 (P1) |
| **GET `/api/v1/installers/hwax-agent/latest`** | tauri-plugin-updater | 없음(서명검증) | **❗갭 G1** — 계약엔 있으나 task 없음 | (없음 → §2 갭) |
| **WS `/ws/agent/{agent_id}?token=<jwt>`** | ws.rs | 쿼리 토큰 | **❗갭 G2** — 구독자는 이미 구현됨 | §4 (Phase 4) |

핵심: **P0 4건(enroll·refresh·manifest·download)** 만 들어오면 런처는 페어링→매니페스트→
다운로드→설치까지 돈다. installs/audit/heartbeat 는 Phase1 에서 202 stub 라도 런처는 정상
동작(보고만 누락). 즉 **E2E 차단의 임계 경로는 P0 4건**이다.

---

## §2. 현 플랜에 없는 신규 백엔드 갭

### G1 — updater feed `GET /api/v1/installers/{app_id}/latest` (권장 P1)

런처 자동 업데이트(`tauri-plugin-updater`, v2 §18)가 폴링하는 정적 JSON.
PR #1 로 `openapi.yaml` 에 `TauriUpdaterManifest` 스키마 + 엔드포인트는 추가됐으나
**구현 task 가 `NEXT_STEPS`/`backend-plan` 어디에도 없다.** 형식(계약 그대로):

```json
{ "version": "1.0.3", "notes": "...", "pub_date": "2026-06-07T09:00:00Z",
  "platforms": { "windows-x86_64": { "signature": "<base64 ed25519>", "url": "https://.../HWAXAgent_1.0.3_x64-setup.exe" } } }
```

- `204` = 최신(업데이트 없음).
- `signature` 는 빌드 파이프라인의 ed25519 키(런처 레포 `.github/workflows/build-and-sign.yml`
  의 `TAURI_SIGNING_PRIVATE_KEY`)로 만든 `*.sig` 내용. 서버는 저장·서빙만.
- 인증 불필요(공개; 무결성은 서명으로 보장).

### G2 — WS push 메시지 스키마 + `/ws/agent/{agent_id}` (Phase 4, 단 계약 필요)

런처에 **WS 구독자가 이미 구현돼 있다**(`ws.rs`: 푸시 오면 즉시 manifest 재동기화, 끊기면
30분 폴링 폴백). 서버측은 `NEXT_STEPS` §4 / `backend-plan` §4 에 "Phase 4 옵션"으로만 있고
**푸시 메시지 스키마가 미정의**. 결정 필요 2가지:
1. **메시지 계약**: 런처는 *어떤 메시지든* 받으면 재동기화하므로 최소 동작엔 스키마가
   필요 없으나, 계약상 `{ "type": "manifest_changed" }` 정도는 `contracts/` 에 박는 게 좋다.
2. **토큰 전달**: 런처는 현재 v2 §13 대로 `?token=<jwt>` (쿼리). 보안상 `Authorization: Bearer`
   헤더가 낫다 — 서버 구현 시 택1 후 계약/런처 양쪽 정렬(런처 변경은 1줄).

### G3 — installer publish 엔드포인트 (계약/플랜에 없음)

런처 레포의 `build-and-sign.yml` + `scripts/publish.ps1` 이 서명된 산출물을
`installer_packages` 로 올린다(presigned PUT, `{upload_url}` 응답 가정). 이 발행 엔드포인트가
계약/플랜에 없다. 릴리스 자동화를 닫으려면 `POST /api/v1/apps/{id}/installers`(또는 동등) +
presigned PUT 흐름의 계약·구현이 필요.

---

## §3. 서버가 신뢰해도 되는 클라이언트 측 보장

런처가 이미 강제하므로 서버는 중복 구현 불필요(단, 서버측 audience 격리는 여전히 필수):

- 다운로드는 `config.allowed_origins` 정확-오리진 매칭만(자유 URL 입력 없음).
- 모든 패키지 sha256 검증 후에야 압축 해제/실행. zip-slip 방어. staging→final atomic rename.
- 실행은 `manifest.entry.executable` 화이트리스트만. 사용자 입력 인자 없음.
- device JWT/refresh 는 Windows Credential Manager 에만(평문 파일 없음).
- updater 패키지는 내장 ed25519 pubkey 로 서명 검증 후에만 적용.

서버 회귀 체크리스트(audience 격리 등)는 `NEXT_STEPS.md` 부록 A 를 그대로 따른다.

---

## §4. E2E 종료조건 (v2 §23 Phase-1)

> 신규 사용자가 MSI/EXE 더블클릭 → 페어링 → 모듈 1개 다운로드 → 검증 → 실행까지 5분 이내.

이 조건은 **서버 P0 4건(enroll·refresh·manifest·download)** 이 머지되는 순간 런처와 함께
실측 가능해진다. 그 시점에 런처 측은 모의서버 단위테스트(`download_to` 등)를 넘어 **실서버
통합 검증**(verify 스킬로 트레이 기동까지는 이미 확인)을 마저 돌릴 수 있다. 현재는 서버
엔드포인트 부재로 거기서 막혀 있다(런처 결함 아님).

---

## §5. NEXT_STEPS.md / backend-plan.md 갱신 제안

- §5 "HWAXAgent 측 진행 상태": `koopark/HWAXAgent`, "Tauri 2 부트스트랩 진행 중"
  → **`squall321/HWAXLauncher`, 빌드·패키징·실행검증 완료(14커밋); 실서버 페어링/설치만
  서버 P0 대기**.
- 우선순위 매트릭스에 **G1(updater feed)** 를 P1 로 추가(자동 업데이트 의존). G2/G3 은 Phase
  3~4 로 명시하되 "런처 클라이언트 이미 존재" 표기.
