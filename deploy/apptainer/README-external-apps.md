# 운영서버 외부 연계 서비스 등록 (proxy)

운영서버에서 **다른 팀이 로컬 경로에 가상환경(venv)으로 띄운 서비스**(백엔드+프론트가 IP:port 로 접근 가능)를
HEAXHub 하위 경로 `/apps/ext_<id>/` 로 연동한다. 컨테이너/빌드/소스클론을 강제하지 않는다 — 그쪽은
서비스만 띄우고, HEAX 는 IP 를 물어 reverse_proxy 한다.

> 연동되는 앱이 지켜야 할 조건(서브패스 안전·health·MCP 등)은 [EXTERNAL-INTEGRATION-CONTRACT.md](EXTERNAL-INTEGRATION-CONTRACT.md) 참고.
> 이 문서는 "운영자가 어떻게 등록하는가", 규약 문서는 "앱이 무엇을 만족해야 하는가"를 다룬다.

## 왜 별도 파일인가 — 라이프사이클 분리

| | 내 관리 앱 | 운영팀 외부 연계 앱 |
|---|---|---|
| 등록 위치 | `integrations/<slug>/` (git 커밋) | `var/external-apps.yaml` (gitignore, 운영서버 로컬) |
| 실행 형태 | SIF 컨테이너 빌드 | 그쪽 venv 서비스, HEAX 는 proxy 만 |
| 갱신 흐름 | `update-all` (git pull + Drive) | `register-external.sh` (이 문서) |
| update-all 시 | 최신화됨 | **건드리지 않음** (gitignore 라 `git reset --hard` 무영향) |

`update-all` 의 `git_update` 는 `git stash -u` + `git reset --hard origin` 이라 **추적 파일만** 최신화한다.
`var/external-apps.yaml` 과 생성물 `integrations/ext_*/` 는 둘 다 gitignore 라 update-all 이 손대지 못한다.
→ **내 앱만 업데이트되고, 운영팀 외부앱은 그대로 유지**된다. 반대로 이 스크립트는 `ext_*` 만 만지므로 내 앱을 안 건드린다.

## 사용법

```bash
# 1) 레지스트리 준비 (운영서버에서 1회)
cp deploy/apptainer/external-apps.example.yaml var/external-apps.yaml

# 2) 편집 — 항목당 id + upstream(IP:port) 만 있으면 됨
$EDITOR var/external-apps.yaml

# 3) 적용 (운영팀 외부앱만 동기화; 내 앱 무영향)
bash deploy/apptainer/register-external.sh
```

`register-external.sh` 는 `var/external-apps.yaml` 을 읽어 각 항목을
`integrations/ext_<id>/.portal/manifest.yaml`(`launch.mode: proxy`)로 펼치고, 레지스트리에서 사라진
`ext_*` 는 삭제한다. `backend/.venv` python 이 있으면 끝에서 reconcile 을 즉시 당겨 라우트를 바로 등록한다.

## 레지스트리 형식

```yaml
services:
  - id: teamb_dashboard              # → 앱 ext_teamb_dashboard, /apps/ext_teamb_dashboard/
    name: "B팀 대시보드"
    upstream: http://10.12.34.56:8200 # 필수. 그쪽 서비스 IP:port
    strip_prefix: true                # 기본 true (upstream 이 "/" 루트 서빙 시)
    visibility: company               # company|department|team|private
    status: stable                    # stable|beta
  - id: teamc_api
    upstream: http://127.0.0.1:8300
    mcp: { expose: true, path: /mcp } # (선택) MCP 노출 — upstream 이 /mcp 서빙 시
```

- 필수: `id`(소문자/숫자/_), `upstream`(http/https). 하나라도 잘못되면 **아무것도 적용 안 함**(부분 적용 방지).
- 최종 앱 id 는 `ext_` 접두가 자동으로 붙어 내 관리 앱과 절대 충돌하지 않는다.

## 적용 타이밍

| 변경 | 즉시(스크립트가 backend/.venv 사용 시) | 자동(비트) |
|---|---|---|
| 추가/수정 | reconcile 즉시 당김 → 라우트 바로 | 라우트 ≤45s(reconcile), 카탈로그 ≤5분(scan) |
| 삭제(레지스트리에서 제거) | 라우트 즉시 해제 | 카탈로그 DB 행은 **안 지워짐**(아래 참고) |

즉시 반영이 필요한데 `backend/.venv` 가 없으면(host python3 만) 매니페스트만 갱신되고 위 비트로 자동 반영된다.
관리자 UI 의 "프록시 동기화"(reconcile) 로도 즉시 당길 수 있다.

## 앱을 완전히 지우려면 (카탈로그에서도 사라지게)

스캐너는 **App 행을 절대 삭제하지 않는다**(upsert-only). `register-external.sh` 로 레지스트리에서
빼면 라우트는 즉시 끊기지만, 카탈로그(`/apps` 목록)엔 그 앱이 계속 남는다. 완전히 지우려면.

```bash
bash deploy/apptainer/deprovision-app.sh <app_id>
```

Caddy 라우트 해제 + state 파일 삭제 + DB 행 삭제(참조 job 이 있으면 삭제 대신 `ARCHIVED` 전환,
job 이력은 보존)까지 한 번에 처리한다. **순서 주의** — 매니페스트 디렉터리(`integrations/ext_<id>/`)를
먼저 지운 뒤 실행해야 한다(안 지우면 재조정 비트가 라우트를 도로 살릴 수 있다는 경고가 뜬다).
내 관리 앱(`integrations/<slug>/`)에도 동일하게 쓸 수 있다 — id 는 `ext_` 접두 유무만 다르다.
