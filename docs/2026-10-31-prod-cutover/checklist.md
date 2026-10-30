<!-- 2026-10-31 운영 전환 요청 중 HEAXHub 몫(HH-15 박스 덮어쓰기 · HH-16 남은 서비스 인스턴스) 체크리스트 -->
# 2026-10-31 운영 전환 — HEAXHub 몫 체크리스트

원문은 [request.md](request.md) 다. 이 브랜치(`wt1031/override`)가 맡은 것은 HH-15 · HH-16 이다. HH-14(매니페스트 넷)는
전환 창 바로 앞에 합칠 다른 브랜치(`cutover/prod-1031`) 몫이라 여기서 손대지 않는다. 결정과 근거는 [context-notes.md](context-notes.md) 에 있다.

## HH-15 — 박스 전용 매니페스트 덮어쓰기

- [x] `integrations/*/.portal/manifest.yaml` 을 읽는 곳 전부 찾기(파일:줄은 context-notes 「읽는 곳 지도」).
- [x] 공용 로더 `backend/app/services/integration_manifest.py` — RFC 7386 병합 · null 지우기 · 목록 통째로 바꾸기.
- [x] 로컬 파일이 없으면 `yaml.safe_load` 결과 그대로(리포의 추적 매니페스트 전부로 시험).
- [x] 깨진 로컬 파일(YAML 오류 · 매핑 아님 · 빈 파일 · 못 읽음)은 무시하고 추적 매니페스트로 — WARNING(같은 내용은 한 시간에 한 번).
- [x] `id` 바꾸기(다른 값 · null · 빈 글자)는 버리고 WARNING, 나머지는 얹는다.
- [x] 읽는 곳을 로더로 — 스캐너 · reconcile · 상태 화면 · apps API 폴백 · 런처 `read_manifest` · `redeploy-app.sh` · `diagnose-app.sh`.
- [x] 변경 감지가 효과 매니페스트를 본다 — 스냅숏 · SIF 빌드 해시 · 기동 지문(늦추기) · 라우트(다음 reconcile).
- [x] `.gitignore` 에 `integrations/*/.portal/manifest.local.yaml` — `git stash -u` 가 안 가져가는 것을 임시 리포로 시험.
- [x] `bundle.sh` 가 로컬 파일을 싣지 않는다.
- [x] 상태 화면 `GET /admin/integrations/builtin` 에 `local_override`(로컬 파일이 있는 앱에만) — 뿌리가 `backend/integrations` 를 가리키던 것도 바로잡음.
- [x] `docs/MANIFEST_SPEC.md` 10절 — 파일 · 규칙 · 깨졌을 때 · 보는 법 · dev 의 step_forge 예 · 받는 순서.
- [x] dev 덮어쓰기 넷의 내용을 HH-14 판(`d2e85c2`)에 얹어 지금 추적 매니페스트와 같은지 확인(같다 — 빌드 해시도 같다).
- [ ] dev 박스에 로컬 파일 넷 두기 — 사람 몫(이 브랜치가 dev 허브에 들어간 뒤, HH-14 를 받기 전). 내용은 context-notes 「dev 덮어쓰기 넷」.

## HH-16 — service 에서 proxy 로 바뀐 앱의 남은 인스턴스

- [x] 답 — 옛 코드는 남겼다. 다시 띄우는 길은 없었다(파일:줄 근거는 context-notes 「HH-16 답」).
- [x] reconcile 이 그 앱 것만 내린다 — 새 라우트를 건 뒤 · 이름 확인 · 잠금 안에서 다시 읽기 · 못 내리면 기록을 남겨 다시.
- [x] url · iframe · job_runner 로 바뀐 앱도 — 라우트 · 상태 파일까지 지운다(`retired`).
- [x] 다른 앱 · service 로 도는 앱은 건드리지 않는다(시험).
- [x] 다시 띄우지 않는다 — reconcile 을 거듭해도 · SIF 를 넘겨 launch() 를 불러도 instance start/exec 가 없다(시험).

## 시험

- [x] 기준선(origin/main `5e48b25`, 하네스로 기동 스캔을 막고) — 400 passed · 14 failed(환경) · 8 deselected.
- [x] 새 시험이 옛 코드 사본에서 실패하는지 — HH-15 는 모듈이 없어 수집부터 실패, 새 로더 + 옛 읽는 곳에서 읽는 곳 시험 여섯 실패. HH-16 은 12 중 11 실패(남은 하나는 지킴 시험).
- [x] 변이 실험(사본) — HH-15 여덟 · HH-16 열둘을 모두 잡는다.
- [x] 전체 — HH-15 뒤 438 passed · HH-16 뒤 450 passed, 실패 14 는 기준선과 같은 목록.
- [x] 변이 사본을 되돌린 뒤 __pycache__ 를 지우고 worktree diff 를 줄 단위로 대조(탐침 없음).
