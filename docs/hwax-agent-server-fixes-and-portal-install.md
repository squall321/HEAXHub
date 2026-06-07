# 서버 수정/할일 + "포털에서 런처 설치" 설계 (런처 측 정리)

문서 상태: v1 (HWAXAgent 런처 측에서 작성, split-strategy §6.1)
작성일: 2026-06-08
대상 독자: HEAXHub 백엔드/프런트/배포 담당자
관련: `docs/NEXT_STEPS.md`(P0 매트릭스), `docs/hwax-agent-backend-plan.md`,
`docs/hwax-agent-client-status-and-server-gaps.md`(PR #2), `docs/HWAX-PORTAL-INTEGRATION.md`,
`contracts/hwax-agent/openapi.yaml`(v0.2.0 + updater feed).

> 런처(`squall321/HWAXLauncher`)를 **HWAX 포털 경유**(`https://hwax.sec.samsung.net/heax-hub`)로
> 전환 완료(updater 엔드포인트·CSP·페어링 fallback). 이 문서는 **지금 올라간 서버 사이드 최신본을
> 직접 점검**해서 (1) 수정해야 할 불일치/갭, (2) **포털에서 런처를 설치(다운로드)하게 만드는 설계**를
> 정리한다. 백엔드 구현은 검증 환경(Postgres/alembic/pytest)이 런처 측에 없어 *계획만* 제출한다.

---

## §0. 점검 결과 한눈에 (실제 코드 기준)

| 항목 | 현재 상태 (cite) | 판정 |
|---|---|---|
| `/api/v1/launcher-agents/*` | `backend/app/api/v1/` 에 `launcher_agents.py` **없음**. `agents.py`(기존 폴링형)만 존재 | ❌ 미구현 (P0) |
| `agent_service.py` / `agent_manifest_builder.py` | `services/` 에 `agent_registry.py` 만 있음 | ❌ 미구현 (P0) |
| `windows_agents.device_kind` / alembic 0006 | 최신 리비전 `0005_submission_source_config` | ❌ 미구현 (P0) |
| 인스톨러 업로드/서빙 | **존재** — `installers.py`: `POST/GET /api/v1/apps/{app_id}/installers...`, 저장소 `var/installers` (`config.py:113`) | ✅ 단, 경로 다름 |
| 인스톨러 다운로드 경로 | 서버: `GET /api/v1/apps/{app_id}/installers/{os}/{version}` / 런처·계약: `GET /api/v1/installers/{id}/download` | ⚠️ **경로 불일치** |
| updater feed | 서버: `GET /api/v1/apps/{app_id}/installers/latest`(redirect JSON, `installers.py:120`) / 런처: `GET /api/v1/installers/{app_id}/latest`(Tauri JSON) | ⚠️ 경로+포맷 불일치 |
| `/ws/agent/{agent_id}` | `ws.py` 에 `/ws/jobs/{job_id}/logs` 만 | ❌ 미구현 (Phase4) |
| 운영자 에이전트 발급 UI | `frontend/.../admin/AgentsTable.tsx` 존재 ("백엔드 `/admin/agents` 준비되면 표시") | 🟡 프런트 대기 |
| 다운로드/페어링 페이지 | `frontend/src/routes/` 에 `apps/` 는 있으나 `download`/`devices/pair` **없음** | ❌ 미구현 |

**결론: P0 4건(enroll·refresh·manifest·download)은 여전히 미구현이고**, 인스톨러 인프라는 이미 있으나
**경로 스킴이 계약과 다르다**. 단, 그 인프라 덕에 "포털에서 런처 설치"는 비교적 적은 추가로 가능하다(§2).

---

## §1. 서버 수정/할일 (우선순위)

### F1 — launcher-agents P0 (P0, 이미 NEXT_STEPS §2 에 계획됨)
`launcher_agents.py` 라우터 + `agent_service.py` + `agent_manifest_builder.py` + alembic 0006/0007.
**새로 더 적을 건 없음 — `NEXT_STEPS.md` §2.1~§2.5 그대로 구현.** E2E 차단의 임계 경로.

### F2 — 인스톨러 다운로드 **경로 불일치** (P0, 신규 발견)
- 런처는 manifest 의 `programs[].package.url`(절대 URL)을 **그대로 GET** 하고 302 를 따라간다. 즉
  **경로가 무엇이든 상관없다** — `agent_manifest_builder` 가 `package.url` 을 **서버가 실제 서빙하는
  경로**(`/api/v1/apps/{app_id}/installers/{os}/{version}` 또는 그 `latest` 리다이렉트)로 채우기만 하면 된다.
- 따라서 권장: **계약 openapi 의 `/api/v1/installers/{id}/download` 를 강제하지 말고**, manifest_builder 가
  기존 라우트를 가리키게 한다. (origin 허용목록은 scheme+host 만 비교하므로 sub-path/경로 무관 — 런처 OK.)
- 대안(원하면): 계약 경로 그대로 `/api/v1/installers/{id}/download` alias 라우트 신설. 둘 중 택1은 백엔드 재량.
- **단, manifest 의 `package.sha256` 은 반드시 채워야 한다**(런처가 추출 전 검증; 불일치 시 거부).

### F3 — updater feed (P1, G1)
런처 자동 업데이트(`tauri-plugin-updater`)는 **고정 경로** `GET /api/v1/installers/hwax-agent/latest` 를
폴링하고 **Tauri static-JSON 포맷**을 기대한다(서명 검증). 기존 `/api/v1/apps/{app_id}/installers/latest`
는 경로·포맷이 다르다. 둘 중 하나:
- (a) 신규 라우트 `GET /api/v1/installers/{app_id}/latest` → 아래 포맷 그대로 반환(계약: `TauriUpdaterManifest`).
- (b) 런처의 updater 엔드포인트를 기존 경로로 바꾸고, 그 핸들러가 Tauri 포맷을 반환하도록 수정(런처 변경 1줄 + 서버 포맷 추가).
```json
{ "version": "1.0.3", "notes": "...", "pub_date": "...Z",
  "platforms": { "windows-x86_64": { "signature": "<base64 ed25519 .sig 내용>", "url": "https://.../HWAX Agent_1.0.3_x64-setup.exe" } } }
```
`204` = 최신. `signature` 는 런처 빌드 파이프라인(`build-and-sign.yml`, `TAURI_SIGNING_PRIVATE_KEY`)이
만든 `*.sig` 파일 내용. 서버는 저장·반환만.

### F4 — WS push (Phase 4, G2)
`backend/app/api/v1/ws.py` 의 `@router.websocket("/ws/jobs/{job_id}/logs")` 패턴을 따라
`@router.websocket("/ws/agent/{agent_id}")` 추가. 런처에 **구독자는 이미 있음**(아무 메시지나 받으면 즉시
재동기화, 끊기면 폴링 폴백). 최소 동작엔 스키마 불필요하나, 계약에 `{ "type": "manifest_changed" }` 정도
정의 권장. 토큰: 런처는 현재 `?token=<jwt>`(쿼리). `Authorization` 헤더가 더 안전 — 택1 후 정렬(런처 1줄).

### F5 — 인스톨러 publish (G3) — **이미 존재**
`POST /api/v1/apps/{app_id}/installers`(operator) 가 이미 업로드를 받는다(`installers.py:30`). 런처
`scripts/publish.ps1` 이 이 경로를 타게 하면 G3 해결 — **신규 백엔드 작업 거의 없음**(인증/스토리지 확인만).

---

## §2. "포털에서 런처 설치" 설계 — 인프라 재사용

핵심: **인스톨러 업로드/서빙/`latest` 인프라가 이미 있다**(`installers.py` + `services/installer_packages.py`
+ `var/installers`). 그래서 런처를 *그 인프라 위의 한 앱*으로 올리면 된다.

```
[런처 빌드 (GitHub windows-latest, online)]
  build-and-sign.yml → 서명된 HWAX Agent_x.y.z_x64-setup.exe + *.sig + sha256
        │  POST /api/v1/apps/hwax-agent/installers  (기존 업로드 라우트, publish.ps1)
        ▼
[HEAXHub: installer_packages(app_id='hwax-agent', os='windows-x64', version, sha256, signed=true)]
        │
        ├─►(웹) GET /heax-hub/download  ← 신규 SPA 페이지
        │        → GET /api/v1/apps/hwax-agent/installers/latest?os=windows-x64 → 302 → 설치파일
        │
        └─►(자동업데이트) GET /api/v1/installers/hwax-agent/latest (F3, Tauri JSON)
```

구현 항목:
1. **App row `app_id='hwax-agent'`** — 런처 자체를 카탈로그 한 앱으로. `app_type` 은 신설(`desktop_agent`)
   하거나 기존 `windows_gui` 재사용(매니페스트에는 안 뜨게 플래그). **결정 필요(open Q1).**
2. **빌드 파이프라인 업로드** — F5(기존 라우트). 런처 `publish.ps1` 만 경로 정렬.
3. **웹 다운로드 페이지** — `frontend/src/routes/download.tsx`(신규, TanStack file-route). api base 는
   `BASE_URL + "api/v1"`(포털 sub-path 자동 반영, `HWAX-PORTAL-INTEGRATION.md`). `latest?os=windows-x64`
   호출 → 버전/크기/서명 표시 + 다운로드 버튼. **인증 여부 결정(open Q2)** — 사내 배포면 로그인 뒤, 또는 공개.
4. **페어링 페이지** — `frontend/src/routes/devices/pair.tsx`(신규). 런처 `start_pairing` 이
   `<server>/devices/pair?code=XXXXXX` 를 브라우저로 연다(현재 라우트 없음 → 404). 로그인 사용자가 코드를
   확인하고 운영자 발급 `enrollment_token` 을 받아 런처 UI 에 붙여넣는 흐름. (운영자 발급 UI 는
   `admin/AgentsTable.tsx` 에 이미 골격 있음 — 백엔드 `/admin/agents` + launcher-agents 와 연결.)
5. **SmartScreen/EDR** — 최초 실행 SmartScreen 경고는 EV 서명으로 완화. 다운로드 페이지에 "사내 검증됨"
   배지 + `docs/EDR-WHITELIST.md`(런처 레포) 의 4종 화이트리스트 안내 링크.

---

## §3. 런처 측 잔여 (런처 레포에서 처리)
- `scripts/publish.ps1` → 업로드 경로를 `POST /api/v1/apps/hwax-agent/installers` 로 정렬(F5).
- updater 엔드포인트는 F3 결정에 따름: (a)면 런처 무변경(이미 `/api/v1/installers/hwax-agent/latest`),
  (b)면 런처 endpoint 1줄 수정.
- 그 외 런처는 포털 sub-path/경로 변화에 **무관**하게 동작(상대 경로 append + origin scheme+host 비교).

## §4. 결정 필요 (open questions)
1. **Q1** 런처를 담을 `App.app_type` — `desktop_agent` 신설 vs `windows_gui` 재사용(+매니페스트 제외 플래그)?
2. **Q2** 다운로드 페이지 인증 — 공개 vs 로그인 뒤?
3. **Q3** updater feed — F3 (a) 신규 경로 vs (b) 기존 경로 + Tauri 포맷? (런처 변경량 차이뿐.)
4. **Q4** manifest `package.url` — 기존 `/apps/{id}/installers/...` 경로 사용 vs `/installers/{id}/download` alias 신설?
5. **Q5** 인스톨러 다운로드 인증 — 런처 JWT(aud=hwax-agent) 강제 vs 서명+sha256 만으로 공개?
