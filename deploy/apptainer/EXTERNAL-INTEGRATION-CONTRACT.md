# HEAXHub 외부 연계 규약 (External Integration Contract)

운영서버에서 IP:port로 떠 있는 서비스를 HEAXHub `/apps/ext_<id>/` 로 붙일 때, **앱이 지켜야 할 계약**입니다.
지키는 만큼 HEAX·MCP 게이트웨이에 자동으로 엮입니다. 앱은 자기에게 필요한 티어까지만 지키면 됩니다 —
단순 대시보드는 Tier 0, 에이전트 도구는 Tier 2.

등록 방법은 [README-external-apps.md](README-external-apps.md) 참고. 이 문서는 "앱이 만족해야 할 조건"만 다룹니다.

## 공통 전제 (HEAX가 보장하는 것)

- 앱은 `/apps/ext_<id>/` 하위 경로로 노출됩니다. Caddy가 이 프리픽스를 **떼고**(strip_prefix 기본 true) upstream에 넘깁니다.
- HEAX는 upstream에 `X-Forwarded-Prefix: /apps/ext_<id>` 를 보냅니다(내부 앱의 `--root-path` 와 동일한 신호).
- `Host` 헤더는 upstream 호스트로 세팅됩니다(가상호스트 라우팅 앱 대응).
- HEAX는 앱을 **실행하지 않습니다.** 앱은 스스로 떠 있어야 하고, HEAX는 라우팅만 합니다.

---

## Tier 0 — 필수 (안 지키면 화면 자체가 안 뜸)

### 0-1. 서브패스 안전 (셋 중 하나)
앱이 루트(`/`)가 아니라 `/apps/ext_<id>/` 아래에 있습니다. 다음 중 하나를 반드시 만족.

- **(권장) 상대경로 서빙.** 프론트의 asset·API 호출을 상대경로로. Vite `base: './'`, `<base href>` 사용.
  절대경로(`/assets/app.js`, `fetch('/api/x')`)는 포털 루트로 해석돼 **404**가 납니다.
- **또는 X-Forwarded-Prefix 반영.** 프레임워크가 이 헤더를 root_path로 읽게 설정
  (uvicorn `--proxy-headers`, Starlette `root_path`, Next.js `basePath` 등).
- **또는 고정 base_path 빌드.** `/apps/ext_<id>` 를 base로 빌드하고 레지스트리에 `strip_prefix: false`.

### 0-2. Health
- `GET /health` 가 200을 반환(다른 경로면 명시). readiness 판정에 사용.

### 0-3. 바인드/보안
- loopback 또는 방화벽 안쪽에 bind. 인터넷 직접 노출 금지(Caddy가 유일한 경계).
- MCP/DNS-rebinding Host 검증은 loopback 전제로 비활성(내부 앱과 동일 규약).

---

## Tier 1 — REST API (백엔드 API가 있으면)

- API를 **단일 프리픽스**(`/api/...`)로 모으고 상대경로로 호출.
- `/openapi.json`(또는 `/docs`)를 열어두면 HEAX 카탈로그가 API 문서 링크를 자동으로 겁니다.
- 같은 오리진(Caddy 경계 뒤)이라 **CORS 설정 불필요**.

---

## Tier 2 — MCP (에이전트가 부르는 도구로 노출)

- `<base>/mcp` 를 **streamable HTTP**로 서빙.
- 레지스트리 항목에 `mcp: { expose: true, path: /mcp }`, `status: stable`(또는 `beta`).
  → HEAX `/api/v1/mcp/servers` 에 등재되고 중앙 HWAX 게이트웨이가 자동 흡수.
- **인증:** 게이트웨이가 서비스 PAT를 헤더로 주입합니다. 앱은 그 토큰 인증을 받아들이고,
  자체 세션쿠키/로그인 리다이렉트를 강제하지 마세요.
- **툴 self-describe:** 각 tool의 `name`·`description`·`inputSchema`를 충실히 채우세요.
  HEAX 카탈로그와 게이트웨이가 그 설명을 **그대로** 사용합니다(사람이 따로 안 써도 됨).

---

## 체크리스트 (앱 담당자 복붙용)

```
[ ] Tier0  절대경로 asset 없음 (또는 X-Forwarded-Prefix 반영, 또는 고정 base_path+strip_prefix:false)
[ ] Tier0  GET /health → 200
[ ] Tier0  loopback/방화벽 안쪽 bind
[ ] Tier1  API 단일 프리픽스 /api/*, 상대호출 (선택: /openapi.json 노출)
[ ] Tier2  /mcp streamable HTTP, PAT 인증 수용, tool name/description/inputSchema 충실
```

## 운영자 등록 예

```yaml
# var/external-apps.yaml
services:
  - id: myteam_tool
    name: "우리팀 도구"
    upstream: http://127.0.0.1:8300
    mcp: { expose: true, path: /mcp }   # Tier 2 노출 시
```
```bash
bash deploy/apptainer/register-external.sh
```
