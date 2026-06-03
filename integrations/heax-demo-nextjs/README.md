# heax-demo-nextjs

HEAXHub Next.js 스택 데모. Caddy 의 `/apps/{id}/` 뒤에서 Node 빌드 산출물이 정상
서빙되는지, `basePath` / `assetPrefix` 가 런처에서 주입한 값으로 동작하는지 확인하는
픽스처.

## 구성

- `app/` — Next.js App Router (`layout.tsx`, `page.tsx`, `counter.tsx`, `health/route.ts`)
- `next.config.js` — `NEXT_PUBLIC_BASE_PATH` 를 읽어 `basePath` / `assetPrefix` 설정
- `.portal/manifest.yaml` — HEAXHub portal manifest (schema v2)

## 실행 (로컬)

```bash
pnpm install
pnpm build
NEXT_PUBLIC_BASE_PATH=/apps/heax_demo_nextjs PORT=3000 pnpm start -- --port 3000 --hostname 0.0.0.0
```

페이지에서:

- `<h1>HEAXHub · Next.js Demo</h1>`
- 주입된 basePath 표시
- 클라이언트 사이드 카운터 (증감 버튼)
- `GET /health` → `{ "ok": true }`

## HEAXHub 통합

런처는 다음을 보장한다:

1. `NEXT_PUBLIC_BASE_PATH` 환경변수로 Caddy 라우트 루트 (`/apps/{id}`) 주입
2. `$PORT` 로 서비스 포트 주입
3. `health_check.path` (`/`) 에 대해 주기적 헬스체크
4. `restart_policy: on_failure / max 3`
