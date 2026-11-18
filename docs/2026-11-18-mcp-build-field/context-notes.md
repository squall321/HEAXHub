<!-- MCP 목록 build 칸 작업 중 내린 결정과 까닭 -->
# MCP 목록 `build` 칸 — 결정 노트

## 2026-11-18

- **값의 출처는 기동 순간의 SIF 다.** 스캐너는 SIF 를 새로 빌드해 `var/sifs/<slug>.sif` 를 원자 교체하지만,
  정상 인스턴스는 재기동하지 않는다(redeploy-app.sh 머리말). 그래서 디스크의 SIF · `AppVersion.git_commit_hash`
  는 떠 있는 판보다 앞설 수 있다. 소스를 못 받아 기존 SIF 로 뜨는 폴백 경로(cae00)는 AppVersion 에 커밋을
  아예 적지 않는다. 기동할 때 그 SIF 의 `%help` 에 박힌 `upstream commit:` 을 읽어 state 에 적는다.
  (`apptainer inspect --helpfile` 은 이 박스에서 13ms.)
- **state schema 판은 올리지 않는다.** 선택 칸 하나 추가다. 판을 올리면 `_read_state` 가 기존 state 를 모두
  무시하고, `mcp._ever_launched` 가 거짓이 돼 모든 앱이 게이트웨이 목록에서 빠진다.
- **이미 떠 있는 인스턴스 보충은 SIF 가 기동 뒤 바뀌지 않았을 때만 한다(ctime ≤ started_at).** 처음엔 mtime 으로
  잡았다가 ctime 으로 바꿨다. cae00 은 SIF 를 dist-from-drive(rclone)로 받는데 rclone 은 mtime 을 원본(빌드 박스)
  시각으로 보존한다. 그래서 기동 뒤 받은 새 SIF 도 mtime 이 started_at 보다 이를 수 있다. ctime 은 새 파일 · rename
  때 그 시각이 되고 사용자 도구로 되돌릴 수 없다. 바뀌었으면 다음 재기동까지 칸을 뺀다(지어내지 않는다). 시도한
  결과(None 포함)를 state 에 적어, reconcile(45초)마다 inspect 를 다시 부르지 않는다.
- **보충 자리는 `_launch_once` 의 공통 생존 판정이다.** `_launch_via_sif` 안의 재사용 판정은 같은 조건이 앞에서 이미
  걸러 사실상 오지 않는다. 처음 거기 넣었다가 시험이 잡아내 옮겼다.
- **떠 있는 인스턴스 안에서 다시 exec 할 때**(앱 프로세스만 죽은 경우)는 디스크 SIF 를 읽지 않고, 같은 인스턴스
  이름의 앞선 state 에 적힌 commit 을 잇는다.
- **목록 쪽 꼴 검사.** 게이트웨이(629f32c)는 `commit` 이 `[0-9a-f]{7,40}` 일 때만 받는다. 빌더는 아카이브 소스면
  sha256 다이제스트를 커밋 자리에 박으므로, 허브도 16진 커밋이 아니면 칸을 뺀다.
- **`built_at` 은 싣지 않는다.** Drive 로 옮긴 SIF 의 파일 시각은 빌드 시각을 보장하지 않는다. 대신 인스턴스가 뜬
  `started_at` 을 싣는다.
- **목록 API 는 subprocess 를 부르지 않는다.** 게이트웨이가 자주 폴링하므로 state 파일만 읽는다.
- proxy 모드(kooremapper-mcp) · host-PATH 기동은 커밋을 모르므로 칸이 빠진다.
