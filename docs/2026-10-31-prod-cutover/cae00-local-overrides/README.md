<!-- cae00 이 운영 노드 전환 창에 둘 박스 덮어쓰기 넷과 그 차례(HH-14 를 추적 파일 대신 박스 파일로 — 사용자 결정 2026-10-31) -->
# cae00 박스 덮어쓰기 넷 — StepForge · DynaForge 를 운영 노드로

## 무엇을 정했나

- 요청서 HH-14 는 박스 덮어쓰기가 없던 때라 추적 매니페스트를 바꾸라고 했다. HH-15(`0b02759` ~ `055b023`)로 박스 덮어쓰기
  `integrations/<폴더>/.portal/manifest.local.yaml` 이 생겼다.
- 그래서 사용자가 2026-10-31 에 정했다(검토 D-20 의 대안). **추적 매니페스트는 지금 값(dev 와 같은 서비스 · 8700 · 8701)으로 둔다.**
  cae00 만 전환 창에 이 폴더의 파일 넷을 두어 운영 노드 터널(16110 · 16120 · 16121)로 넘긴다.
- 합칠 때를 맞출 필요가 없다. 되돌리는 커밋도 없다(파일을 지우면 된다). dev 와 새 박스는 안전한 기본값으로 뜬다.
- 가지 `cutover/prod-1031`(`c1f8fbc`)은 합치지 않는다. 이 파일 넷을 얹은 결과가 그 가지의 매니페스트와 타입까지 같다는 것을 허브 로더
  (`load_file_with_override`)로 확인했다(넷 다 `applied=True` · `held=False`).

| 파일 | 하는 일 |
|---|---|
| `step_forge/manifest.local.yaml` | 서비스 앱을 proxy 로 바꾼다 — upstream `127.0.0.1:16110` · `strip_prefix` · 빌드 · 자원 · 원천 블록을 지운다(`portal_auth` 와 `mcp` 는 추적 값 그대로) |
| `kooremapper/manifest.local.yaml` | upstream 을 `127.0.0.1:16120` 으로 |
| `kooremapper-api/manifest.local.yaml` | upstream 을 `127.0.0.1:16120` 으로 |
| `kooremapper-mcp/manifest.local.yaml` | upstream 을 `127.0.0.1:16121` 로 |

## 차례(cae00, 리포 루트 `~/Projects/HEAXHub` 에서)

1. **전환 창 전에 — 새 허브 코드를 먼저 받는다.** update-all 이 HEAXHub `055b023` 이후 판을 받게 한다. 받기 전에
   [`../context-notes.md`](../context-notes.md) 「받기 전 점검」의 읽기 스크립트로 첫 reconcile 이 내릴 것을 미리 본다(dev 는 0 이었다).
2. 허브가 그 판으로 다시 떴는지 본다 — `cat var/run/backend.rev` 가 `055b023` 이후 커밋이다. 옛 허브가 떠 있는 동안 파일을 두면 덮어쓰기를
   읽지 못한다.
3. **전환 창 안에서 — 터널부터 본다.** 사용자 유닛 `hwaxprod-tunnel` 이 떠 있고 세 포트가 답하는지 본다.
   `curl -s -o /dev/null -w '%{http_code}\n' --noproxy '*' http://127.0.0.1:16110/health`(16120 · 16121 도 같은 꼴, 앱의 헬스 경로는
   운영 노드 쪽 설정을 따른다)이다. 하나라도 답하지 않으면 여기서 멈춘다.
4. 파일 넷을 둔다 — 이 폴더에서 `integrations/<폴더>/.portal/manifest.local.yaml` 로 복사한다.
   `for d in step_forge kooremapper kooremapper-api kooremapper-mcp; do cp docs/2026-10-31-prod-cutover/cae00-local-overrides/$d/manifest.local.yaml integrations/$d/.portal/; done`
5. 45초 안에 reconcile 이 라우트를 운영 노드로 바꾼다. 그 뒤 HH-16 이 `heax_app_step_forge` 를 내리고 포트(9115)를 돌려준다.
   **손으로 먼저 멈추지 않는다** — 파일을 두기 전에 멈추면 reconcile 이 45초 안에 되살린다(검토 #11).
6. 확인한다.
   - 허브 `GET /admin/integrations/builtin` 의 네 행에 `local_override` 가 있고 보류(`held`)가 아니다.
   - 게이트웨이의 `heaxkooremapper_mcp_system_status` 가 운영 노드 API 를 답한다. `heaxstep_forge_system_capabilities` 의 판이 운영 판이다.
   - 리스크 심사 캡처 1건 · 심의 DynaForge 위임 1건 · 포털 STEP 업로드 1건 · 웹 타일 둘이 열린다(요청서 7절 '운영 전환').
   - `apptainer instance list` 에 `heax_app_step_forge` 가 없다. 남아 있으면 heax 재기동이 빠진 것이니 `apptainer instance stop heax_app_step_forge` 를 손으로 한다.
7. 게이트웨이 쪽 박스 값 — 심의 DynaForge 위임 주소와 REST 다리는 cae00 `provision.env` 의 `KOORM_BASE` 로 바꾼다(요청서 그대로, 추적 변경 없음).

## 되돌리기

- 파일 넷을 지운다. 다음 reconcile 이 추적 값(서비스 step_forge · 8700 · 8701)으로 돌린다. step_forge 는 박스의 `var/sifs/step_forge.sif` 로
  다시 뜬다. cae00 의 DynaForge 8700 · 8701 이 그때 떠 있어야 한다.

## 지킬 것

- 이 파일들은 `.gitignore` 의 `integrations/*/.portal/manifest.local.yaml` 이라 커밋되지 않는다. update-all 의 `git stash -u` 도 가져가지 않는다.
  `git clean -x` 는 지운다 — 쓰지 않는다.
- 파일이 깨지면(꼴 오류 · 빈 파일) 허브는 그 앱을 보류한다(D-13) — 라우트와 인스턴스를 마지막으로 좋았던 상태로 두고 상태 화면에 `held` 를 띄운다.
  파일을 고치면 다음 reconcile 에 풀린다.
- cae00 은 SIF 를 짓지 않으므로 dist-to-drive 의 '덮어쓴 앱의 SIF 는 싣지 않는다'(D-19)는 이 박스와 상관없다.
