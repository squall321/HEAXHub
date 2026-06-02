# HEAXHub 오프라인 배포 가이드

대상: 인터넷 차단된 Ubuntu 24.04 운영 서버
방식: 인터넷 가능한 staging 박스에서 번들을 만들어 USB/scp 로 옮긴 뒤 설치

본 문서는 아래 5단계 운영자 워크플로를 다룬다.

1. 사전 준비
2. 온라인 staging 박스에서 번들 만들기
3. 타깃으로 옮기기
4. 타깃에서 설치하기
5. 검증

---

## 1. 사전 준비

### 1.1 온라인 staging 박스
다음이 설치되어 있어야 한다.

- python 3.10+ (백엔드와 같은 메이저/마이너 권장)
- pnpm (프론트엔드 빌드용)
- dotnet 8 SDK (HeaxAgent 빌드용)
- apptainer (SIF 확인용 — 빌드는 별도 BuildXxx.sh 로 이미 끝났다고 가정)
- tar, gzip

### 1.2 오프라인 타깃 서버
인터넷 없이도 미리 깔려 있어야 하는 apt 패키지 목록:

```
postgresql-client
redis-tools
apptainer
python3.11
python3.11-venv
```

해당 패키지는 사내 미러 또는 별도 `.deb` 번들로 미리 설치해 두어야 한다.
(install_offline.sh 가 시작 시 존재 여부만 확인한다.)

### 1.3 SIF 사전 빌드
운영에 필요한 SIF 는 사전에 빌드되어 staging 박스의
`~/serviceApptainers/` 에 존재해야 한다. 최소 필요 SIF:

- heaxhub_postgres.sif
- heaxhub_redis.sif
- heaxhub_mailhog.sif
- heaxhub_caddy.sif
- KooSimulationPython313.sif (옵션, 시뮬레이션 워크로드용)

---

## 2. 온라인 staging 박스에서 번들 만들기

```bash
cd ~/claude/HEAXHub
bash scripts/prepare_offline_bundle.sh
```

기본 동작:

- `backend/.venv/bin/pip freeze` 결과를 입력으로 `pip download` 실행
  → `dist-bundle/heaxhub-bundle-<VER>/wheels/`
- 프론트엔드 `pnpm install --frozen-lockfile && pnpm build`
  → `dist-bundle/heaxhub-bundle-<VER>/frontend-dist/`
- HeaxAgent 를 `dotnet publish` 로 linux-x64 / win-x64 single-file 산출
  → `dist-bundle/heaxhub-bundle-<VER>/agents/`
- SIF 들은 `~/serviceApptainers/*.sif` 에서 stage 디렉터리로 심볼릭 링크
- `interpreters.yaml`, `sif_registry.yaml`, `.env.template` 을 `config/` 로 복사
- `offline_bundle.json` 매니페스트 생성
- 최종적으로 `heaxhub-bundle-<VER>-<TIMESTAMP>.tar.gz` 생성

확인용 dry-run:

```bash
bash scripts/prepare_offline_bundle.sh --dry-run 2>&1 | head -40
```

부분 빌드 (자주 쓰는 옵션):

```bash
# 프론트엔드 / 에이전트는 다시 안 빌드하고 wheel만 갱신
bash scripts/prepare_offline_bundle.sh --skip-frontend --skip-agent

# 버전 명시
bash scripts/prepare_offline_bundle.sh --version 0.3.1
```

---

## 3. 타깃으로 옮기기

USB / scp / 사내 파일서버 등 가용한 수단으로 단 한 개 파일만 옮긴다.

```bash
scp dist-bundle/heaxhub-bundle-*.tar.gz \
    operator@target:/tmp/
```

체크섬은 운영자가 함께 계산하여 첨부:

```bash
sha256sum heaxhub-bundle-*.tar.gz > heaxhub-bundle.sha256
```

타깃에서 무결성 검증:

```bash
sha256sum -c heaxhub-bundle.sha256
```

---

## 4. 타깃에서 설치하기

```bash
cd /tmp
tar xzf heaxhub-bundle-*.tar.gz
cd heaxhub-bundle-*/
bash scripts/install_offline.sh
```

install_offline.sh 가 하는 일:

1. `python3`, `apptainer`, `psql`, `redis-cli` 가 PATH 에 있는지 확인
2. `~/heaxhub/backend/.venv` 생성 후 `pip install --no-index --find-links wheels/`
   로 모든 의존성을 오프라인 설치하고 `pip install -e backend`
3. `frontend-dist/` 를 `~/heaxhub/web/` 으로 복사 (Caddy/nginx 서빙 위치)
4. `agents/linux-x64/HeaxAgent` 를 `~/heaxhub/agent/` 에 배치하고
   `~/.config/systemd/user/heaxhub-agent.service` 등록
5. `sifs/` 의 SIF 들을 `~/serviceApptainers/` 로 복사 (기존 파일은 보존)
6. `alembic upgrade head` 로 DB 스키마 적용
7. `scripts/create_admin.py` 실행 (관리자 계정 생성)

옵션:

```bash
# 위치 커스터마이즈
bash scripts/install_offline.sh \
     --target-root /opt/heaxhub \
     --sif-dest /opt/serviceApptainers \
     --frontend-dest /var/www/heaxhub
```

`.env` 설정:

```bash
cp config/.env.template ~/heaxhub/backend/.env
vi ~/heaxhub/backend/.env
# 최소 수정 항목:
#   JWT_SECRET
#   DATABASE_URL (heaxhub_postgres.sif 가 5432 로 listen 한다고 가정)
#   REDIS_URL
#   APP_BASE_URL / FRONTEND_BASE_URL / CORS_ORIGINS
```

자동 기동 등록:

```bash
bash scripts/install_autostart.sh
```

---

## 5. 검증

### 5.1 서비스 헬스체크

```bash
curl -fsS http://localhost:8000/admin/system/health | jq .
```

응답 예시:

```json
{
  "status": "ok",
  "db": "ok",
  "redis": "ok",
  "apptainer": "ok"
}
```

### 5.2 Apptainer 인스턴스 상태

```bash
apptainer instance list
# heax-pg, heax-redis, heax-mailhog, heax-caddy 가 떠 있어야 함
```

### 5.3 프론트엔드

브라우저로 `http://<target>/` 접속해 로그인 화면이 나오는지 확인.

### 5.4 에이전트

```bash
systemctl --user status heaxhub-agent.service
journalctl --user -u heaxhub-agent.service -n 50
```

### 5.5 트러블슈팅

| 증상 | 원인/대처 |
| --- | --- |
| `pip install` 가 외부 mirror 시도 | `--no-index` 가 빠진 경우. `wheels/` 경로 확인 |
| `apptainer instance start` 실패 | userns / fakeroot 설정 누락. `apptainer config global` 확인 |
| alembic 실패 | `DATABASE_URL` 오타 또는 heax-pg 인스턴스가 아직 안 떴음 |
| HeaxAgent 가 곧장 죽음 | `appsettings.json` 에 백엔드 base URL/토큰이 비어 있음 |
| 헬스체크 503 | uvicorn 미기동. `deploy/apptainer/start.sh` 다시 실행 |

---

## 부록: 번들 매니페스트 스키마

`offline_bundle.json` 예시:

```json
{
  "bundle":   "heaxhub-bundle-0.3.1",
  "version":  "0.3.1",
  "built_at": "20260529-093000",
  "wheels":   { "count": 92, "dir": "wheels/" },
  "sifs":     { "count": 5,  "list": ["heaxhub_postgres.sif", "..."], "dir": "sifs/" },
  "agents":   { "linux_x64": "agents/linux-x64/HeaxAgent",
                "win_x64":   "agents/win-x64/HeaxAgent.exe",
                "dir":       "agents/" },
  "frontend": { "size": "1.2M", "dir": "frontend-dist/" },
  "config":   ["interpreters.yaml", "sif_registry.yaml", ".env.template"]
}
```

운영자가 번들을 검수할 때 이 파일을 먼저 확인하면 누락 여부를 빠르게 알 수 있다.
