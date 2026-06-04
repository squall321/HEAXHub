# heax-demo-static-html

HEAXHub `static_html` 스택 데모. 빌드 단계, 런타임 프로세스 없이
정적 파일만 서빙하는 가장 단순한 시그니처를 시연한다.

## 구조

- `index.html` — 단일 파일 랜딩 페이지. HEAXHub 팔레트(navy/indigo + amber)와
  Pretendard 폰트(CDN) 사용.
- `logo.svg` — HEAX 마크.
- `.portal/manifest.yaml` — `build.stack: static_html`, `build.root: .` 선언.

## static_html 스택이 하는 일

오직 하나: `build.root` 디렉터리를 Caddy 의 `file_server` 에 마운트.

명시적으로 일어나지 않는 일:

- venv / pip install 없음
- npm / pnpm install / build 없음
- 별도 런타임 프로세스(uvicorn, node, streamlit 등) 없음
- health check 폴링 없음 (프로세스가 없으니까)

## 로컬 미리보기

```bash
cd integrations/heax-demo-static-html
python -m http.server 8000
# http://localhost:8000/
```

미리보기 시 `location.pathname` 은 `/` 가 되므로
"Base Path" 카드에 mismatch 경고가 표시된다. HEAXHub 에 배포되면
`/apps/heax_demo_static_html/` prefix 가 잡힌다.

## HEAXHub 에서의 동작

Caddy 가 `/apps/heax_demo_static_html/*` 를 이 디렉터리에 매핑하여
정적 파일 그대로 응답한다.
