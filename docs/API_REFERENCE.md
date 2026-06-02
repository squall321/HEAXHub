# API Reference (v1)

기본 prefix: `/api/v1`. 모든 응답은 JSON. 인증이 필요한 엔드포인트는 `Authorization: Bearer <access_token>` 헤더 필요. 실시간 로그는 WebSocket `/ws/jobs/{id}/logs?token=<access>`.

OpenAPI 스펙은 백엔드 실행 후 <http://localhost:8000/docs> 또는 `/openapi.json` 에서 자동 조회.

## 인증 (`/auth`, `/users`)

| Method | Path | 인증 | 설명 |
|---|---|---|---|
| POST | `/auth/register` | × | 자체 가입 — name, organization, email, password |
| POST | `/auth/verify-email` | × | 이메일 인증 토큰 확인 |
| POST | `/auth/login` | × | email + password → access/refresh tokens |
| POST | `/auth/refresh` | × | refresh token → 새 토큰 한 쌍 (이전 token revoke) |
| POST | `/auth/logout` | ✓ | 현재 또는 전체 디바이스 refresh token revoke |
| GET | `/auth/me` | ✓ | 현재 사용자 |
| PATCH | `/auth/me` | ✓ | display_name / organization 갱신 |
| GET | `/users/me` | ✓ | `/auth/me` 의 alias |
| PATCH | `/users/me` | ✓ | `/auth/me` PATCH 의 alias |
| POST | `/auth/password/reset-request` | × | 재설정 메일 발송 |
| POST | `/auth/password/reset` | × | 토큰 + 새 비밀번호 |

## 앱 (`/apps`)

| Method | Path | 설명 |
|---|---|---|
| GET | `/apps` | 카탈로그 (page, page_size, q, app_type, status, visibility, tag) |
| GET | `/apps/recommended` | 추천 8개 (stable, 최근 수정 순) |
| GET | `/apps/favorites` | 내가 즐겨찾기한 앱 |
| POST | `/apps/{id}/favorite` | 즐겨찾기 토글 |
| GET | `/apps/{id}` | 상세 + manifest + versions |
| GET | `/apps/{id}/manifest` | overlay manifest.yaml 원본 |
| GET | `/apps/{id}/versions` | 버전 목록 |
| GET | `/apps/{id}/history` | 이 앱의 실행 이력 (paginated) |
| POST | `/apps/{id}/run` | 실행 (multipart: params_json + files[]) |
| GET | `/apps/{id}/files/{path}` | 앱 워크스페이스 내 파일 다운로드 |

## 작업 (`/jobs`)

| Method | Path | 설명 |
|---|---|---|
| GET | `/jobs` | 내 작업 (관리자는 전체) |
| GET | `/jobs/{id}` | 상세 |
| GET | `/jobs/{id}/logs` | stdout.log 전체 텍스트 |
| GET | `/jobs/{id}/files` | output/ 파일 목록 |
| GET | `/jobs/{id}/files/{path}` | output/ 파일 다운로드 |
| POST | `/jobs/{id}/cancel` | 실행 중인 작업 취소 |
| POST | `/jobs/{id}/rerun` | 같은 params + input 파일로 새 작업 생성 |
| WS | `/ws/jobs/{id}/logs?token=...` | 실시간 로그 스트림 |

## 신청 (`/submissions`)

| Method | Path | 설명 |
|---|---|---|
| POST | `/submissions` | 새 앱 신청 |
| GET | `/submissions` | 내 신청 (관리자는 전체, status 필터 가능) |
| GET | `/submissions/{id}` | 상세 |
| PATCH | `/submissions/{id}` | 운영자 승인/반려 (`status` + `review_notes`) |
| POST | `/submissions/{id}/test-run` | 운영자가 샘플 실행 (built/published 상태에서만) |

## 관리자 (`/admin`)

| Method | Path | 설명 |
|---|---|---|
| GET | `/admin/users` | 사용자 목록 |
| PATCH | `/admin/users/{id}/role` | role 변경 |
| GET | `/admin/updates` | upstream 변경 알림 목록 |
| POST | `/admin/updates/{audit_id}/approve` | 자동 재빌드 트리거 |
| POST | `/admin/updates/{audit_id}/ignore` | 알림 무시 (audit 기록만) |
| GET | `/admin/audit` | 감사 로그 (action, target_type 필터) |
| GET | `/admin/stats` | 대시보드용 집계 (users/apps/submissions_pending/jobs_24h/jobs_running) |
| GET | `/admin/system/health` | redis · users_active 카운트 |

## Webhook (`/webhooks`)

| Method | Path | 설명 |
|---|---|---|
| POST | `/webhooks/github` | GitHub tag/push (HMAC-SHA256 검증) |
| POST | `/webhooks/windows-agent` | Windows Worker 상태 보고 (인증 토큰) |

## 공통 에러 응답

```json
{
  "detail": "Invalid credentials",
  "code": "unauthorized"
}
```

| HTTP | 의미 |
|---|---|
| 400 | ValidationError |
| 401 | UnauthorizedError (토큰 만료/없음/revoked) |
| 403 | ForbiddenError (권한 부족) |
| 404 | NotFoundError |
| 409 | ConflictError |
| 422 | Pydantic validation 실패 |
| 500 | 서버 오류 (로그 확인) |
