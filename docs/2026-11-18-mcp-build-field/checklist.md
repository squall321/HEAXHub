<!-- MCP 레지스트리 목록에 떠 있는 인스턴스의 판(build)을 싣는 작업 체크리스트 -->
# MCP 목록 `build` 칸 — 체크리스트

원문: 포털 hwaxportal-71 청(2026-11-18) — 11-17 cae00 디지털워커 시험 PT-R3 · P-3(운영본 표시).
원문 파일은 `HWAXPortal/docs/ste-stepforge-1117/`(reddish-request.md 6장, condensation-request.md 3장).
결정과 까닭은 같은 폴더 `context-notes.md`.

- [x] `apt_runner.sif_commit(sif)` — `apptainer inspect --helpfile` 의 `upstream commit:` 줄을 읽는다(모르면 None).
- [x] 런처 SIF 기동 — state 에 `commit` 을 적는다(schema 판은 올리지 않는다).
- [x] 런처 재사용 경로 — state 에 `commit` 키가 없고 SIF 가 기동 뒤 바뀌지 않았으면(ctime ≤ started_at) 채운다.
- [x] `GET /api/v1/mcp/servers` — 줄마다 선택 칸 `build: {commit(7자), started_at(UTC ISO)}`, 모르면 뺀다.
- [x] 테스트 — sif_commit 파싱, 기동 기록, 재사용 보충(바뀐 SIF 는 안 채움), 목록 칸 유무.
- [x] 백엔드 테스트 — 새 시험 17개 통과, 나머지 실패 14개는 변경 전과 같은 묶음(dotnet argv · launcher_reports · updater_feed · etag)이다.
- [ ] 커밋 → hwaxportal-71 에 커밋 해시와 함께 알림.
