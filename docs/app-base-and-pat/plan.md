# heax 앱 base 규약 방어 + PAT 발급 UI — 계획

작성 2026-07-18. 갱신 2026-07-18(진단 정정).

## 진단 정정 — 최초 가설과 실제 원인

**최초 증상:** cae00 `/heax-hub/` 자산 404, dev `/heax-hub/apps/materialtwin_web/` 이
"KooRemapper" 를 반환. → 앱 base 규약 결함으로 추정했으나 **정밀 추적 결과 대부분 오진**.

**실제 확정 사실:**
1. **cae00 자산 404 의 유일한 원인 = 허브 dist 가 루트 base(`/assets`)로 빌드됨** —
   빌드 env 이름 불일치(`HEAX_BASE_PATH` vs `VITE_BASE_PATH`). **이미 수정·배포**
   (HWAXPortal 3170589, HEAXHub dist-to-drive 가드 0ed153c). materialtwin 앱과 무관.
2. **materialtwin 앱은 완전 정상** — 소스 vite.config 가 이미 `base:"./"`(상대),
   런타임(:9124)이 `<title>MaterialTwin Web</title>` + `./assets/…` 를 정확히 서빙,
   Caddy 직행(:4180) 앱 경로도 정상. 배선(authz→strip_prefix→reverse_proxy :9124) 정상.
3. **dev 의 "KooRemapper" 는 dev 박스 한정 nginx 라우트 오염** — 런타임 nginx 가
   `/heax-hub/` 를 heax Caddy(:4180)가 아닌 KooRemapper 로 프록시. **cae00 엔 KooRemapper
   가 없어 무관**. (레포 hwax.conf 는 `/heax-hub/→:4180` 로 올바름 — 런타임만 오염.)
4. **PAT 백엔드는 이미 완비**(`POST/GET /auth/tokens`, `pat_service.issue`) — 빠진 건 UI 뿐.
   "MCP 전용 토큰" 은 없어도 됨(범용 heax PAT 를 HEAX_MCP_TOKEN 에 넣으면 게이트웨이가 씀).

**따라서 이 계획의 실제 범위(축소):**
- A. 앱 base **방어**(급하지 않음 — 현 앱은 이미 상대 base). 미래 앱의 실수를 빌드 단계에서
     차단하는 가드로서 가치. **구현·단위테스트 완료**(integration_builder).
- B. PAT 발급 **UI** — 실제 필요(백엔드는 있고 UI 만 없음). heax MCP 연동의 마지막 퍼즐.

---

(이하 원 계획 — A 는 방어 목적으로 이미 반영, B 는 후속 구현 대상)

## A. 앱 base 규약 (모든 앱 공통 — 근본 원인)

### 진단(코드 확정)
- `backend/app/services/proxy_manager.py` 규약: 앱은 `/apps/{id}` 로 프록시되고 기본
  `strip_prefix=True` — 업스트림은 **루트 경로**를 받는다(앱이 base 를 몰라도 되는 설계).
- 그러나 그러려면 프론트 자산이 **상대/루트 상대 경로**여야 한다. 그런데
  `backend/app/services/integration_builder.py` 의 node 빌드(`_run([pnpm,"build"], ...)`)는
  **base 주입이 전혀 없다** → 각 앱의 vite 기본 `base:"/"` 가 그대로 → `/assets/...` **절대경로**.
- 결과: `strip_prefix` 로 접두어를 떼도 브라우저가 `/assets/...`(포털 루트)를 찾다가 404.
  materialtwin 만의 문제가 아니라 **fastapi_react/node 스택 전 앱의 구조적 결함**이고
  laminate·미래 앱 모두 동일.

### 해결 — 빌더가 상대 base 를 강제(단일 지점 수정)
앱은 `/apps/{id}` 로 프록시되므로 절대 base 를 앱에 하드코딩하면 앱ID·포털유무에 종속된다.
**상대 base(`./`)** 가 정답 — strip_prefix 로 루트에서 서빙되든 서브패스로 서빙되든
`index.html` 기준 상대해석이라 양쪽에서 동작한다(SPA 라우터가 history fallback 이면 충분).

- [ ] `integration_builder._build_nodejs`: build 커맨드에 env 주입 —
      `VITE_BASE_PATH=./` + `BASE_URL=./`(범용) + `PUBLIC_URL=.`(CRA 호환) 를 넣어 실행.
      앱이 vite.config 에서 `base: process.env.VITE_BASE_PATH ?? "./"` 를 읽으면 반영되고,
      안 읽어도 **빌더가 `--base=./` 를 직접 전달**(vite/rolldown 은 CLI `--base` 를 항상 존중)해
      앱 협조 없이도 상대 base 를 보장한다.
- [ ] 빌드 후 검증 게이트: `dist/index.html` 이 `src="/assets` 같은 **루트 절대경로**를
      포함하면 빌드 결과를 실패로 표기(sentinel 미기록) — 규약 위반을 조용히 통과시키지 않음.
- [ ] 스택별 처리: fastapi_react/vite → `--base=./`; Next.js/Streamlit 등 base 를 자체
      라우팅에 굽는 스택은 `strip_prefix=False` 경로이므로 이 주입 대상에서 제외(스택 분기).
- [ ] materialtwin·laminate 강제 재빌드(sentinel 무효화) → 상대 base dist 생성 확인.

### 앱 개발 규약 문서화(미래 앱)
- [ ] `docs/authoring-web-apps.md`: "프론트 자산은 상대 base. vite 면 아무 설정 불필요
      (빌더가 `--base=./` 주입). 라우터는 basename 을 `import.meta.env.BASE_URL` 로."
      SPA 라우터 basename 주의 + 절대 링크 금지 명시.

## B. PAT 발급 UI (게이트웨이 연동 토큰)

### 진단(코드 확정)
- 백엔드 **이미 완비**: `POST /auth/tokens`(발급, `PatCreated`), `GET /auth/tokens`(목록),
  발급 로직 `pat_service.issue()`, 검증 `pat_service.resolve_user()`.
- `GET /api/v1/mcp/servers` 인증 = `CurrentUser`(PAT 수용). → **"MCP 전용 토큰"은 없어도 되고
  범용 heax PAT 하나면 게이트웨이가 그대로 쓴다**(HEAX_MCP_TOKEN 에 이 PAT 를 넣음).
- 빠진 것 = **프론트 UI 뿐**(auth.ts 에 함수 없음, 설정 페이지 없음). 그래서 cae00 에서
  "토큰 발급 인터페이스가 안 보인" 것 — 원래 없었던 게 맞다.

### 해결 — 최소 UI 추가(백엔드 무변경)
- [ ] `frontend/src/lib/api/auth.ts`: `listTokens()`, `createToken(name, scopes?)`,
      `revokeToken(id)` — 기존 `POST/GET /auth/tokens`, `DELETE /auth/tokens/{id}` 호출.
      (DELETE 라우트 존재 여부 확인 — 없으면 목록/발급만, revoke 는 후속.)
- [ ] `frontend/src/routes/settings/tokens.tsx`(또는 기존 설정 라우트에 섹션): 발급 폼
      (name 입력 → 발급) + **평문 토큰 1회 표시**(복사 버튼, 재조회 불가 경고) + 목록(prefix·
      생성일·폐기). MCP 게이트웨이용 안내 문구 1줄(이 토큰을 HEAX_MCP_TOKEN 으로).
- [ ] 네비/설정 진입점에 링크 노출. 관리자 전용인지 일반 사용자 가능한지 권한 확인 후 배치.
- [ ] 재빌드 → `/heax-hub/settings/tokens` 에서 발급 e2e.

## C. 배포 정합
- [ ] A/B 반영된 프론트를 `VITE_BASE_PATH=/heax-hub/` 로 빌드(허브 dist) → dist-to-drive
      (base 가드 통과) → cae00 deploy.
- [ ] 앱 재빌드는 heax 워커(build_tasks)가 스캔 시 수행 — cae00 에서 앱 sentinel 무효화 후
      재스캔되게(또는 강제 재빌드 트리거) 흐름 확인.

## 검증
1. dev: materialtwin/laminate dist `index.html` 이 `./assets` (상대) 인지 grep.
2. dev: `/heax-hub/apps/materialtwin_web/` 200 + 자산 200 + 화면 렌더(playwright).
3. dev: `/heax-hub/settings/tokens` 발급 → 평문 표시 → `GET /api/v1/mcp/servers` 를 그 PAT
   Bearer 로 호출 200(게이트웨이가 실제로 쓸 수 있음을 증명).
4. 빌드 가드: 절대경로 dist 를 만들면 빌드 실패 표기되는지(네거티브).

## 리스크
- 상대 base + SPA 라우터: BrowserRouter basename 미설정 시 새로고침/딥링크가 깨질 수 있음
  → 라우터 basename 을 `import.meta.env.BASE_URL` 로(앱별 확인, 규약 문서에 명시).
- `--base=./` 를 CLI 로 강제하면 vite.config 의 base 를 덮는다 — 자체 base 를 굽는 스택
  (Next/Streamlit)엔 적용 금지(스택 분기로 회피).
- heax 레포는 우리 관할(핸즈오프 아님) — RA 와 달리 커밋 가능. 앱 소스(MaterialTwinWeb 등)
  수정 최소화: 빌더 주입으로 앱 무수정이 목표.
- PAT UI 권한: 아무나 발급하면 안 되는 조직이면 관리자 게이트 — 권한 모델 확인 후 결정.
