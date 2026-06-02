# HEAXHub Runbook

운영자가 자주 마주치는 시나리오와 빠른 처치 방법.

## 1. 첫 부팅

```bash
make docker-up                       # postgres + redis + mailhog
cp .env.example .env                 # 그리고 JWT_SECRET 등 채우기
cd backend && pip install -e ".[dev]"
make migrate                          # alembic upgrade head
make seed                             # 초기 admin 1명 생성
cd ../frontend && pnpm install
# 3 터미널:
make backend / make frontend / make worker
```

웹: <http://localhost:5173>, 메일: <http://localhost:8025>

## 2. 새 앱 신청 처리

1. 사용자가 `/submit` 에서 신청
2. 운영자가 `/admin/submissions` 에서 검토
   - manifest 자동 검증 결과 확인
   - "Approve" 클릭 → 워크스페이스 자동 생성 + 빌드 시작
3. 빌드 진행은 `app_workspaces/{id}/build/status.json` 또는 `build.log` 확인
4. 빌드 성공 → submission `status=built` 로 자동 전환
5. 운영자가 한 번 `test-run` 수행해 정상 동작 확인 (선택)
6. 운영자가 `status=published` 로 PATCH → `/apps` 카탈로그에 노출

## 3. 빌드 실패 디버깅

| 증상 | 확인 |
|---|---|
| Submission `failed` | `app_workspaces/{id}/build/build.log` |
| venv 생성 실패 | `.env`의 `PYTHON_BUILD_PATH` 가 실제 Python 경로인지 |
| pip install 실패 | `requirements.txt`의 외부 PyPI 접근 가능한지 |
| Apptainer 실패 | `scripts/build_apptainer_sif.sh` 실행 권한 + `apptainer` 바이너리 |

재시도: `/admin/submissions` 의 동일 항목 다시 Approve. 워크스페이스는 보존되며 build_status만 갱신됨.

## 4. 사용자 실행 실패 디버깅

| 증상 | 확인 |
|---|---|
| 작업이 `failed` | `job_storage/{Y}/{M}/{job}/logs/stdout.log` |
| `result.json` 없음 | 앱이 `output/result.json` 을 안 만든 것 — manifest 가이드 따르도록 개발자에게 안내 |
| 실행 도중 hang | `/jobs/{id}/cancel` → 작업 중단 |

## 5. Upstream 업데이트 처리

1. Celery beat 또는 cron으로 `sync_tasks.check_upstream_updates` 주기 호출
2. 새 commit 발견 시 audit_log `upstream.update_available` 생성
3. 운영자 `/admin/updates` 에서 확인 후 Approve 클릭
4. `sync_tasks.refresh_upstream` 가 pull + 새 AppVersion + 빌드 진행
5. 빌드 성공 후 운영자가 publish 로 승격해야 사용자에게 노출됨

## 6. 비밀번호 재설정 (운영자가 사용자 대신)

운영 DB에서 직접:

```sql
UPDATE users SET status='active', email_verified=true WHERE email='target@company.com';
```

또는 사용자가 `/password/reset-request` 에서 직접 진행. 메일 못 받으면 MailHog 또는 사내 SMTP 로그 확인.

## 7. Refresh 토큰 일괄 무효화 (계정 도용 의심 시)

```sql
UPDATE refresh_tokens
SET revoked_at = now()
WHERE user_id = (SELECT id FROM users WHERE email='target@company.com')
  AND revoked_at IS NULL;
```

또는 사용자 본인이 `/auth/logout` body `{"all_devices": true}` 로 호출.

## 8. 디스크 정리

```bash
# 90일 지난 job 결과 압축
scripts/rotate_job_storage.sh 90

# 사용 중단된 앱 워크스페이스 수동 정리 (반드시 archived 상태 확인 후)
rm -rf app_workspaces/{archived_app_id}
```

## 9. 시스템 헬스

```bash
scripts/healthcheck.sh    # JSON 출력 — postgres / redis / disk
curl -s http://localhost:8000/api/v1/admin/system/health -H "Authorization: Bearer $TOKEN"
```

## 10. 새 운영자 추가

1. 본인이 일반 사용자로 가입
2. 기존 admin이 `/admin/users` → 해당 사용자 role을 `admin` 으로 변경
3. 두 번째 admin 확보 권장 (운영 안전성)

## 11. 장기 데몬 웹앱 호스팅

`launch.mode: service` 로 등록된 앱(Streamlit·Jupyter·내부 대시보드 등)은
`service_manager` 가 데몬으로 띄우고, Caddy 인스턴스가 경로 기반 리버스
프록시로 외부 노출한다.

### 기동/종료

Caddy 는 다른 인프라(Postgres·Redis·MailHog) 와 동일하게 Apptainer 인스턴스로
관리된다. 별도 root 권한 없이 동작하도록 HTTP 는 **4180** 포트를 사용한다.

```bash
bash deploy/apptainer/start.sh   # postgres/redis/mailhog/caddy + backend/worker 일괄
bash deploy/apptainer/stop.sh    # heax-caddy 도 함께 정리됨
```

최초 실행 시 `~/serviceApptainers/heaxhub_caddy.sif` 가 없으면
`apptainer pull docker://caddy:2-alpine` 로 자동 받는다.

### 경로 레이아웃

- 외부 진입점: `http://<PUBLIC_HOST>:<PUBLIC_PORT>/apps/{app_id}/`
  - 기본값: `http://localhost:4180/apps/{app_id}/`
- Caddy 는 `/apps/{app_id}` 접두어를 strip 한 뒤 `127.0.0.1:{port}` 로 포워딩한다.
  앱은 `ROOT_PATH=/apps/{app_id}` 환경변수를 통해 자신이 어느 base path 에
  매달려 있는지 인지할 수 있다 (`base_path_aware: true`).
- 포트는 `port_allocator` 가 `APP_PORT_RANGE_LOW..HIGH` 풀에서 자동 할당한다.

### 라우트 점검

```bash
# 백엔드 관점에서 현재 떠 있는 ServiceInstance 와 포트
curl -s http://localhost:4040/api/v1/admin/services \
  -H "Authorization: Bearer $TOKEN" | jq

# Caddy 가 실제로 들고 있는 라우트 (정상이면 fallback-404 + app-* 항목들)
curl -s http://127.0.0.1:2019/config/apps/http/servers/srv0/routes | jq '.[]."@id"'

# 라우트가 살아 있는지 직접 확인
curl -i http://localhost:4180/apps/{app_id}/healthz
```

루트 페이지(`/`) 에 대해서는 항상 404 가 떨어지도록 bootstrap 에서 설정되어
있다. 활성 앱이 없는데 200 이 떨어진다면 부트스트랩이 덮어쓰이지 않았을
가능성이 있다 — `var/caddy/bootstrap.json` 과 `/data/caddy.log` 를 확인한다.

### 라우트 수동 제거

비정상 종료된 경우 잔여 라우트가 남아 있을 수 있다.

```bash
curl -X DELETE http://127.0.0.1:2019/id/app-{app_id}
```

## 12. SSO 전환 (2단계 작업)

1. IdP에 클라이언트 등록, redirect URI = `/api/v1/auth/sso/callback`
2. `.env` 에서 `AUTH_MODE=sso`, `OIDC_*` 채우기
3. 마이그레이션 스크립트(`scripts/migrate_to_sso.py` — 작성 예정) 로 local 사용자를 `auth_source=sso` 로 매핑
4. `/auth/register`, `/auth/login` 비활성화 안내 페이지 노출
