<!-- 구현 중 내린 결정과 그 근거를 적어 두는 메모 -->
# 컨텍스트 노트 — cae00 운영 수정요청(2026-10-27) HEAXHub 담당분

## 사전검토 결과 (구현 시작 전 실측)

문서의 파일:줄 근거를 현재 HEAD(973444a 이후, `4e1954d` 의 자손)에서 전부 재확인했다.
보정할 내용 없음 — 8항목 모두 문서 주장이 정확하고 아직 미수정 상태.

- HH-02: `dist-from-drive.sh:40` `cp -p` 그대로.
- HH-04: `dist-to-drive.sh:112` `sha256sum ./* > SHA256SUMS` 가 스테이징분만으로 생성 → `:116`
  이 그걸 `latest/` 에 copy. 부분배포 때 과거분 체크섬이 빠진다.
- HH-05: `appdata-to-drive.sh:60` 제외목록에 `cred.key`·`secrets.env` 만 있음(`knobs.env` 없음).
- HH-06: 추가로 찾은 근본원인 — `_sif_fail()`(:539)은 로그만 쓰고 `instance_stop` 을 호출하지
  않는다. 헬스 영구실패 경로(:757)는 `_sif_fail` 도 안 타고 그냥 라우트를 등록해버린다 — 인스턴스가
  뜬 채로 끝나는 지점이 최소 3곳.
- HH-07: `_is_healthy()`(:1477)의 4-URL 순서 중 첫 번째(`root+health_path`)가 prefix-aware
  스택(Next.js·Streamlit·Dash, `_PREFIX_AWARE_STACKS`)이 아닌 앱에는 **항상** 404 — 두 번째
  URL 에서 성공해 판정은 맞지만 httpx 가 모든 시도를 INFO 로 로그.
- HH-08: 같은 파일에 기동실패용 쿨다운(`_LAUNCH_FAIL_COOLDOWN_S`, 1시간)은 이미 있는데
  빌드실패 알림(`_notify_build_failed`)엔 없음 — 비대칭이 원인.
- HH-09: `HEAX_APP_URL_<slug>` 류 기능 전혀 없음(그렙 0건).
- HH-12: `launch()` 호출 경로(scan 5분 / reconcile 45초) 에 앱별 잠금 없음. 628400d 의 잠금은
  **SIF 빌드**(`integration_sif_builder.build_sif`)에만 걸려 있고 **launch/exec** 는 안 걸림 →
  동시 기동 경로가 실제로 열려 있음(질문에 대한 답 = 예, 겹칠 수 있다).

## 결정

- **순서**: HH-12(잠금) → HH-06(잠금 위에 쌓이는 정리 로직) → HH-07(독립) → HH-02(독립) →
  HH-04(독립) → HH-05(독립) → HH-08(독립) → HH-09(독립). 공유 패턴부터 깔아야 뒤 항목이 그 위에
  쌓인다.
- **HH-12 잠금 메커니즘**: 628400d 가 쓴 `fcntl.flock` 비차단 패턴을 그대로 재사용(새 메커니즘
  도입 안 함). 잠금 파일은 `var/integration_state/<canonical>.launch.lock` — 기존 state 파일과
  같은 디렉터리, 같은 네이밍 축. 프로세스가 죽으면 flock 이 자동 해제되므로 고착 위험 없음(빌드
  잠금과 동일 보장).
  잠금 범위는 `launch()` 중 `mode == "service"` 분기(= `_launch_once` 호출)로 한정한다 —
  proxy/static/url/iframe 모드는 프로세스를 띄우지 않아(인스턴스 셸 문제가 없음) 잠글 필요가
  없고, 범위를 넓히면 그만큼 불필요한 직렬화가 생긴다.
  잠겨 있으면 `action="busy"` 반환(SIF 빌드의 `"busy"` 와 같은 어휘 — 코드베이스 일관성).
  `launch()` 의 백오프 로직(`_note_launch_failure`/`_clear_backoff`)은 busy 를 "실패"로도
  "성공"으로도 치지 않음 — 아무 일도 안 일어났으므로 건드리지 않는다.
- **HH-07**: `_is_healthy` 에 `prefix_aware: bool` 매개변수를 추가해 호출부가 이미 계산해 둔
  `strip_prefix`(= `stack_name not in _PREFIX_AWARE_STACKS`)의 반대값을 넘긴다. prefix-aware
  앱은 `root+health_path` 를 먼저(기존과 동일, 원래도 맞았음), 아닌 앱은 `health_path` 단독을
  먼저 시도해 구조적으로 보장된 404 시도를 없앤다. 최종 판정(어느 URL이든 <500 이면 healthy)은
  그대로 — 순서만 바꾼다.
- **HH-08**: 새 쿨다운 상수를 만들지 않고 기존 `_LAUNCH_FAIL_COOLDOWN_S` 를 빌드실패 알림에도
  재사용 — 두 실패 종류를 같은 간격으로 다루는 게 기존 설계 의도(이름에 "LAUNCH" 가 박혀 있어
  범용화하려면 이름도 같이 정리하는 게 맞는지는 구현 중 재확인).
- **HH-04**: Drive 에 기존 `latest/SHA256SUMS` 가 있으면 내려받아 이번 스테이징분 줄로 갈아끼운
  뒤 합쳐서 올린다(없으면 지금처럼 새로 생성). rclone 왕복 1회 추가 — 배포 스크립트라 빈도가
  낮아 비용 무시 가능.
- **HH-05**: 양쪽 다 막는다 — 올리는 쪽(제외목록에 추가)과 받는 쪽(시드 단계에서 건너뜀) 이중
  방어. 이미 Drive 에 올라간 `knobs.env` 가 있을 가능성은 문서가 "지금은 없다"고 확인해 줘서
  과거분 정리는 범위에서 뺐다.

## 구현 중 메모 (진행하며 추가)
