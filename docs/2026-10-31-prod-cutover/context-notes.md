<!-- 2026-10-31 운영 전환 HEAXHub 몫(HH-15 · HH-16)을 하며 내린 결정 · 근거 · 읽는 곳 지도 · 시험 수를 적어 두는 메모 -->
# 컨텍스트 노트 — 2026-10-31 운영 전환(HH-15 · HH-16)

브랜치 `wt1031/override`(origin/main `5e48b25` 에서). 10-27 의 HH-02 · HH-04~HH-09 · HH-12 는 이미 main 에 있어(`1dab32a..42d3b94`)
다시 하지 않았다. HH-14(매니페스트 넷)는 `cutover/prod-1031`(`d2e85c2`) 몫이라 이 브랜치에 없다.

## 전제 — dev 도 이 매니페스트를 쓰는 박스다(HH-15 의 답)

포털 세션이 확인한 사실이다. dev 는 `heax_app_step_forge` 를 허브가 빌드한 서비스 앱으로 띄우고, `kooremapper` · `kooremapper_api` ·
`kooremapper_mcp` 를 `127.0.0.1:8700` · `:8701` 로 넘긴다. 그래서 HH-14 가 합쳐지기 **전에** 박스 덮어쓰기가 있어야 한다 — 없으면
dev 의 네 앱과 게이트웨이 백엔드 `heax-step_forge` · `heax-kooremapper_mcp` 가 끊긴다(dev 에는 16110 · 16120 · 16121 이 없다).

## 읽는 곳 지도 — `integrations/*/.portal/manifest.yaml`

줄 번호는 HH-16 커밋(`c33a9f6`) 기준이다 — 검토 후속 커밋 뒤에는 함수 이름(`_load_manifest` · `_process_dir` · `_reconcile_one` ·
`list_builtin_integrations`)으로 찾는다. 「로더」 는 `integration_manifest.load_file` / `load_file_with_override` 를 쓴다는 뜻이다.
로컬 파일이 있는데 얹지 못하면 `load_file` 은 `LocalOverrideError` 를 올리고(도는 쪽은 그 앱을 보류), `load_file_with_override` 는 추적 값과
`held` 를 돌려준다(보여 주는 쪽) — 아래 D-13.

| 읽는 곳 | 파일:줄 | 무엇에 쓰나 | 바꾼 것 |
|---|---|---|---|
| 스캐너 | `backend/app/services/integrations_scanner.py:152` `_load_manifest` → `:159`, 부르는 곳 `:854-855` · 존재 확인 `:1110` | App · AppVersion 행, `manifest_snapshot`(`:1016` · `:1046`), SIF 빌드(`:414` → `integration_sif_builder.py:336` `_hash_inputs`), 기동(`:530-535`) | 로더 · 보류면 `skipped`(검토) |
| reconcile(45초 · 기동 때 한 번) | `backend/app/workers/integration_tasks.py:100-106` | 라우트 · 기동(`:142` launch()), 모드 거르기 `:114` | 로더 · HH-16 · 검토 뒤 `_reconcile_one`(앱별로 견딤 · 보류면 `held` · 명시된 비라우트 모드만 retire) |
| 런처 | `backend/app/services/integration_launcher.py:1748` `read_manifest`(부르는 곳 없음) | — | 로더(일관성) |
| 런처 기동 지문 | `integration_launcher.py:144` `_launch_fingerprint`, `:209` | 되풀이 실패 늦추기 — 받은 매니페스트를 해시 | 읽지 않는다(효과 매니페스트를 받는다) |
| 상태 화면 | `backend/app/api/v1/admin.py:406` `list_builtin_integrations` → `:438` | 앱별 상태 표 | 로더 · `local_override`(검토 뒤 `held`) · 뿌리 바로잡기(`:426`) · 매핑이 아닌 build · launch 를 견딘다(검토) |
| 앱 상세 · 매니페스트 API 폴백 | `backend/app/api/v1/apps.py:176-185` · `:213-221` | 스냅숏이 없을 때만 | 로더(integrations 쪽만). 이 경로는 `parents[3]` 이 `backend/integrations` 라 실제로는 닿지 않는다 — 고치지 않았다 |
| redeploy-app.sh | `deploy/apptainer/redeploy-app.sh:48` | 손 재기동(stop → launch) · `--rebuild` | 로더 · 보류면 멈추기 전에 rc 2(검토) |
| diagnose-app.sh | `scripts/diagnose-app.sh:15-20`(추적 파일 grep) · `:24-50` | 운영자 진단 — 업스트림 두드리기 | 로컬 파일이 있으면 효과 값과 그 업스트림. 없으면 출력이 예전과 같다. 검토 뒤 — 보류면 그 까닭과 마지막으로 건 업스트림(상태 파일), id 줄의 따옴표 · 꼬리 주석을 받는다 |
| bundle.sh | `deploy/apptainer/bundle.sh:56-73` | 리포 통째 tar | 로컬 파일을 뺀다(`:72`) |
| backup.sh | `scripts/backup.sh:79` | `integrations` 통째 tar | 그대로 — 이 박스의 덮어쓰기도 백업에 들어가는 것이 맞다 |
| 외부 연계 앱 생성 | `deploy/apptainer/_external_apps.py:124-148` | `var/external-apps.yaml` → `integrations/ext_*/` 를 **쓴다**(읽지 않는다) · 빠진 것은 rmtree | 그대로 — ext_* 는 박스 파일에서 만들어지므로 덮어쓰기가 필요 없다 |
| 등록 스크립트 | `scripts/register-repo.sh` · `register-url.sh` · `register-from-csv.sh` | 매니페스트를 **쓴다** | 그대로 |
| deprovision | `deploy/apptainer/_deprovision_app.py:66-73` | 폴더가 남았는지만 본다 | 그대로 — `git rm` 뒤 무시된 로컬 파일이 폴더를 남길 수 있어 MANIFEST_SPEC 에 '같이 지운다' 를 적었다 |
| 스냅숏을 읽는 곳(간접) | `api/v1/mcp.py:70-75`(MCP 레지스트리) · `integration_launcher.py:933`(형제 주소 HH-09) · `runners/slurm_runner.py:105` · `runners/resource_hooks.py:48` · `runners/apptainer_runner.py:88` · `workers/build_tasks.py:43` · `services/service_manager.py:106` | 스캐너가 쓴 효과 스냅숏을 읽는다 | 그대로 — 스캐너가 효과 매니페스트를 적으므로 따라온다 |
| 다른 매니페스트(대상 아님) | `workers/sync_tasks.py:85-122` · `:189-216`, `services/overlay_synthesizer.py`, `scripts/provision_workspace.sh`, `backend/scripts/seed_streamlit_sample.py` | 사용자 등록 앱의 `app_workspaces/<id>/overlay/.portal/manifest.yaml` · 템플릿 | 손대지 않음 — integrations 가 아니다 |
| 포털 쪽(이 리포 밖) | HWAXPortal `deploy-all-from-drive.sh:120`(git stash -u) · `:353`(허브 재기동 지문) | 허브 리포를 당기고 재기동 여부를 정한다 | 손대지 않음 — 지문에 로컬 파일이 없어도 허브가 reconcile 로 스스로 따른다 |
| 포털 쪽 진단(이 리포 밖, 검토에서 찾음) | HWAXPortal `infra/scripts/check-mcp-registration.sh:102-103`(id grep — 꼬리 주석이 달린 step_forge 는 못 찾는다) · `:124-125`(mode · upstream grep → curl) · `infra/scripts/drive-drift.sh:130-136`(SIF 열쇠를 추적 매니페스트로 셈) | 등록 체인 점검 · Drive SIF 열쇠 대조 | 손대지 않음(다른 리포) — 덮어쓴 dev 에서 check-mcp-registration.sh 는 추적 upstream(16121 · 16120)을 두드려 멀쩡한 앱을 NG 로 읽는다. 아래 「포털에 알릴 것」 |

CI(`.github/workflows/*`) · `contracts/` · `infra/` 에는 integrations 매니페스트를 읽는 것이 없다.

## 결정

- **D-1 로더 하나 — `app/services/integration_manifest.py`.** 이름은 `integration_launcher` · `integration_sif_builder` 와 같은 줄에 둔다.
  추적 파일을 읽다 난 예외(파일 없음 · YAML 오류)는 그대로 올린다 — 부르는 쪽의 처리(스캐너 'manifest unreadable', reconcile 'load failed',
  상태 화면 `__error__`)가 예전 그대로 돌아야 '로컬 파일이 없으면 예전과 같다' 가 성립한다.
- **D-2 병합은 RFC 7386 그대로.** 부록 A 의 사례 열다섯을 시험에 그대로 넣었다. 목록은 이어 붙이지 않는다(tags · env 목록을 박스가 통째로 정한다).
- **D-3 깨진 로컬 파일은 통째로 무시.** 부분만 얹으면 어디까지 들어갔는지 알 수 없다. 예외로 올리면 오타 하나로 그 앱이 카탈로그에서 빠진다.
  대신 WARNING(경로 · 까닭) — 45초마다 남기면 HH-08 의 로그 소음이 되므로 같은 내용은 한 시간에 한 번만 다시 남긴다. 상태 화면에도 까닭이 보인다.
  **→ 10-31 검토로 뒤집었다(D-13).** 추적 값으로 돌면 그 값이 박스에 맞지 않아(dev 의 step_forge → 없는 16110) HH-16 이 도는 서비스를 내린다.
- **D-4 id 는 못 바꾼다.** `id` 는 `/apps/<id>` · 상태 파일 · `heax_app_<id>` · 게이트웨이 키(`heax-<id>`)의 원천이다. 다른 값 · null(지우기) ·
  빈 글자 모두 버리고 나머지만 얹는다. 같은 값을 적은 것은 바꾸기가 아니라 조용히 넘어간다.
- **D-5 효과 매니페스트에 표지를 넣지 않는다.** 표지 키를 넣으면 로컬 파일이 없는 박스에서도 스냅숏 · 빌드 해시가 바뀌어 모든 앱이 다시 빌드된다.
  어떤 앱이 덮어썼는지는 상태 화면 · 로그로 본다.
- **D-6 반영 시점.** 스캐너 · reconcile 이 매 패스 파일을 다시 읽으므로 로컬 파일을 더하거나 고치거나 지우면 다음 reconcile(45초 안)에 라우트 · 기동,
  다음 스캔(5분 안)에 스냅숏 · MCP 레지스트리가 따른다. 도는 서비스의 env 같은 값은 추적 파일을 고칠 때처럼 `redeploy-app.sh` 로 다시 띄워야 한다 —
  건강한 서비스는 다시 띄우지 않는 기존 규칙(`_launch_once` 의 already_running)을 그대로 둔다.
- **D-7 상태 화면은 `GET /admin/integrations/builtin`.** 새 창구를 만들지 않았다. 로컬 파일이 있는 앱의 행에만 `local_override` 를 붙여, 없는 박스의
  응답은 예전과 같다. 이 창구의 뿌리가 `Path(__file__).parents[3] / "integrations"` 였는데 `api/v1/` 에서 세 단계 위는 `backend/` 라
  2026-06-04(`bb2f3aa`)부터 늘 `exists: false` 였다 — 덮어쓰기를 보이려면 고쳐야 해서 스캐너의 `INTEGRATIONS_ROOT` 를 읽게 했다(값은 `<리포>/integrations`).
- **D-8 무시 목록 · 번들 · 받는 순서.** `.gitignore` 에 `integrations/*/.portal/manifest.local.yaml`. `git stash push -u` 는 무시된 파일을 건드리지 않는다
  (`-a` 만 가져간다) — 임시 리포에서 실제로 돌려 확인하는 시험을 넣었다. 그런데 deploy-all 의 stash 는 **당기기 전의** `.gitignore` 로 돈다. 그래서
  순서가 있다 — 이 브랜치가 들어간 판을 먼저 받고(무시 목록 · 로더), 로컬 파일을 두고, 그다음에 HH-14 를 받는다. 판보다 로컬 파일을 먼저 둬야 하면
  `.git/info/exclude` 에 같은 줄을 넣는다. `bundle.sh` 는 다른 박스로 가는 묶음이라 로컬 파일을 뺐다. `backup.sh` 는 이 박스의 복구용이라 그대로 싣는다.
- **D-9 diagnose-app.sh.** 추적 파일을 grep 해 업스트림을 두드리던 스크립트라, 덮어쓴 박스에서는 엉뚱한 곳을 두드린다. 로컬 파일이 있을 때만 로더로
  효과 값을 세어 보여 주고 그 업스트림을 쓴다. 없으면 출력이 예전과 한 글자도 같다(사본에서 diff 로 확인). `grep -q` 판정은 pipefail 아래서 뒤집혀
  (`case` 로 판정한다).
- **D-10 HH-16 — 새 라우트를 건 뒤 내린다.** 반대 순서면 인스턴스를 멈추는 몇 초 동안 `/apps/<id>` 요청이 죽어 가는 인스턴스로 간다(502).
  상태 파일이 '허브가 이 id 로 띄웠다' 고 적은 것만 내린다 — 인스턴스는 이름이 `_instance_name_for(id)` 와 같을 때만, 호스트 프로세스는 명령줄이
  기록과 맞을 때만(`stop()` 과 같은 PID 재사용 가드). `apptainer instance list` 로 '있다/없다' 를 판정하지 않는다 — 그 함수는 실패를 빈 목록으로 돌려줘
  '모름' 을 '없음' 으로 읽는다. 대신 `instance stop` 의 rc 와 'no instance found' 문구로 가른다.
- **D-11 HH-16 — 못 내리면 기록을 남긴다.** rc≠0 이면 서비스 기록이 든 상태 파일을 덮지 않아 다음 reconcile 이 다시 한다(라우트는 이미 새것). 이미 없으면
  (전환 창에서 손으로 먼저 멈췄거나 stop.sh 가 내렸다) 실패가 아니라 기록만 정리하고 INFO. 앱별 기동 잠금(HH-12) 안에서 상태 파일을 다시 읽고 한다 —
  두 워커가 같은 기록을 차례로 처리하면 같은 포트를 두 번 돌려주게 되고, 그 사이 그 포트를 받은 다른 앱의 것을 놓을 수 있다.
- **D-12 HH-16 — url · iframe · job_runner 도.** 이 모드들은 reconcile 이 launch() 를 부르지 않는다. 그래서 reconcile 이 `retire_unrouted` 를 따로 불러
  남은 서비스를 내리고 그 앱의 옛 라우트 · 상태 파일을 지운다(`retired` 로 센다). 남은 것이 없으면 아무것도 안 해 요약이 예전과 같다.

## HH-16 답 — 옛 코드(origin/main `5e48b25`)

**남겼다.** 그리고 **다시 띄우는 길은 없었다.**

- 남긴 까닭 — reconcile(`integration_tasks.py:111` 모드 거르기 → `:129` launch())이 부르는 `launch()` 는 service 가 아니면 잠금 없이 곧장
  `_launch_once` 로 간다(`integration_launcher.py:206-207`). proxy 갈래(`:304`)는 라우트를 다시 걸고 상태 파일을 proxy 기록(`"mode": "proxy"`, pid ·
  port 없음, `:339-348`)으로 덮는다. `heax_app_<id>` 를 멈추는 코드는 이 길에 없다 — 런처에서 인스턴스를 멈추는 곳은 `stop()`(`:1156`, 부르는 곳은
  `redeploy-app.sh:70` 뿐)과 기동 실패 정리 `_sif_fail`(`:605`)뿐이다. 덮인 뒤에는 상태 파일에 instance_name 이 없어 `stop()` 도 그 인스턴스를 모른다.
- 허브를 다시 띄우면 — `stop.sh:31-34` 가 `heax_app_*` 를 통째로 내린다. 이것을 부르는 길은 포털 deploy-all 의 허브 구획(`deploy-all-from-drive.sh:354-358`,
  지문이 바뀌었을 때 — HH-14 커밋이 들어오면 git HEAD 가 바뀌므로 돈다), `update.sh:41-48`, systemd 단위 `ExecStop`(`install_autostart.sh:83`)이다.
  `heal.sh`(`:55-81` — 인프라 넷과 uvicorn · celery 만), `start.sh`(`:224-244` — 백엔드 · celery 만), `scripts/watchdog.sh`(인프라 넷만)와 포털
  `services.sh heal`(heax-hub 의 `start: bash deploy/apptainer/heal.sh`)은 앱 인스턴스를 건드리지 않는다. 그래서 cae00 의 deploy-all 경로에서는
  내려가지만, dev 처럼 cron 의 `start.sh` · `watchdog.sh` · 포털 heal 로만 허브를 다시 띄우는 박스에서는 영영 남는다.
- 다시 띄우는 길 — 없다. 스캐너는 스택의 launch_mode 가 service 일 때만 launch() 를 부른다(`integrations_scanner.py:526`, `external_proxy` 스택은
  `config/stacks.yaml:382` `launch_mode: proxy`). launch() 는 매니페스트의 `launch.mode` 로 갈라 proxy · static · url · iframe 에서 프로세스를 띄우지
  않는다. `redeploy-app.sh` 와 포털 update-all §6(`update-all.sh:2330-2342`) · `update-forges.sh:126` · `:302` 도 같은 launch() 다(`--rebuild` 는
  `source` 블록이 없는 HH-14 판에서 'manifest.source 없음' 으로 멈춘다). `ops_tasks.recover_service_instances` · `service_tasks.service_health_loop` 는
  사용자 등록 앱의 ServiceInstance 행만 보는데, 네 앱의 행은 0 이다(dev DB 를 읽기로 확인).
- 바꾼 것 — 위 D-10 ~ D-12. 이제 다음 reconcile(45초 안, 허브를 다시 띄우면 기동 때 reconcile)이 새 라우트를 건 뒤 그 앱의 `heax_app_<id>` 만 내리고
  포트를 돌려준다. 그 뒤로는 상태 파일이 proxy 기록이라 다시 내리지도 다시 띄우지도 않는다(시험이 reconcile 을 거듭하고 SIF 를 넘긴 launch() 도 부른다).
- 남은 한계 — 이 브랜치가 들어가기 **전에** 이미 proxy 로 바뀌어 상태 파일이 덮인 박스는 기록이 없어 자동으로 못 내린다. 그런 인스턴스는
  `apptainer instance stop heax_app_<id>` 로 손으로 내리거나 stop.sh 가 내린다.
- **cae00 받는 순서(검토 #10).** 이 판과 HH-14 를 한 번의 당기기로 받으면, 새 허브가 뜨기 전 몇 분(미러 · 앱 데이터 · dist-from-drive) 동안
  아직 옛 코드인 celery reconcile 이 HH-14 매니페스트를 보고 라우트를 16110 으로 돌리며 상태 파일을 proxy 기록으로 덮는다(인스턴스는 내리지 않는다).
  그 뒤 뜬 새 코드는 기록이 없어 `heax_app_step_forge` 를 찾지 못한다 — 심의가 돌아 `_delib_hold` 가 재기동을 빼거나 `NO_RESTART=1` 이면 stop.sh 도
  돌지 않아 그 인스턴스(메모리 상한 16GB)가 다음 재기동까지 남는다(사본 재현 — 옛 코드 한 패스 뒤 새 코드 한 패스는 route_proxy 뿐 stop 없음).
  그래서 순서는 ① `wt1031/override` 를 main 에 합치고 cae00 update-all 을 돌린다 ② 허브가 그 커밋으로 다시 떴는지 `var/run/backend.rev` 가 그 커밋인지
  본다 ③ 그다음 전환 창에서 HH-14 를 합친다. 전환 창에서는 heax 재기동이 실제로 됐는지 보고, 빠졌으면 `apptainer instance stop heax_app_step_forge` 를
  손으로 한다.
- **B-1(당기기 전에 손으로 멈추기)은 하지 않는다(검토 #11).** 추적 매니페스트가 아직 service 인 동안 `heax_app_step_forge` 를 멈추면 45초 안에
  reconcile 이 상태 파일의 죽은 pid 를 보고 SIF 로 찬 기동해 되살린다(옛 코드도 이 판도 같다 — 사본 재현, allocate → instance start → exec).
  이 판이 떠 있으면 HH-14 를 당긴 뒤 reconcile 이 새 라우트를 건 다음 인스턴스를 내린다. 손으로 멈추려면 당긴 **뒤에** 한다. 요청서 HH-16 의
  '전환 창에서 우리가 먼저 멈추지만' 이라는 전제는 그대로는 서지 않는다.

## 받기 전 점검 — 첫 reconcile 이 무엇을 내릴지(읽기만)

이 판을 받은 박스의 첫 reconcile 은 '서비스 기록인데 효과 모드가 명시된 다른 모드(proxy · static · url · iframe · job_runner · installer)인' 상태 파일을 모두 정리한다. 받기 전에 리포 루트에서
아래로 미리 본다(로컬 파일을 얹지 않은 추적 값 기준). 10-30 dev 에서는 상태 파일 21 개 가운데 해당 0 이었다.

```bash
backend/.venv/bin/python - <<'EOF'
import json, yaml, pathlib
modes = {}
for mp in pathlib.Path("integrations").glob("*/.portal/manifest.yaml"):
    m = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
    modes[m.get("id") or mp.parent.parent.name.replace("-", "_")] = (m.get("launch") or {}).get("mode")
for f in sorted(pathlib.Path("var/integration_state").glob("*.json")):
    d = json.loads(f.read_text())
    # 명시된 모드로 바뀐 것만 내린다 — 모드가 없거나 모르는 값이면 WARNING 만 남긴다(검토 #9).
    if modes.get(f.stem) in ("proxy", "static", "url", "iframe", "job_runner", "installer") and d.get("mode") != "proxy" and (d.get("instance_name") or (d.get("pid") and d.get("argv"))):
        print("내린다:", f.stem, modes.get(f.stem), d.get("instance_name"), d.get("port"))
EOF
```

포트는 `stop()` 과 같이 상태 파일이 적은 것을 돌려준다. 상태 파일과 포트 표는 함께 바뀌므로(포트를 놓는 곳은 상태 파일을 지우는 `stop()` 뿐) 그 포트는 아직
이 앱의 것이다 — DB 만 새로 만든 박스처럼 둘이 어긋났으면 위 점검에서 걸러 손으로 정리한다.

## dev 덮어쓰기 넷(HH-15 — dev 박스에 둘 것)

이 브랜치가 dev 허브에 들어간 뒤, HH-14 를 받기 전에 아래 넷을 둔다. HH-14 판(`d2e85c2`)의 추적 매니페스트에 얹으면 지금(origin/main)의 추적
매니페스트와 사전으로 같고 SIF 빌드 해시도 같다 — 로더로 직접 확인했다. 그래서 HH-14 를 받아도 dev 는 다시 빌드 · 기동하지 않고 라우트도 그대로다.

`integrations/step_forge/.portal/manifest.local.yaml`

```yaml
execution_target: linux_runner
build:
  stack: fastapi
  type: python_venv
  python_version: "3.12"
launch:
  mode: service
  upstream: null
  strip_prefix: null
  health_check: { type: http, path: /health }
  restart_policy: { policy: on_failure, max_attempts: 3 }
  env: { STEPFORGE_DATA_DIR: /data }
resources:
  cpu: 2
  memory_gb: 16
  gpu: false
source:
  type: git
  url: https://github.com/squall321/StepForge.git
  ref: main
```

`integrations/kooremapper/.portal/manifest.local.yaml` 와 `integrations/kooremapper-api/.portal/manifest.local.yaml`

```yaml
launch:
  upstream: http://127.0.0.1:8700
```

`integrations/kooremapper-mcp/.portal/manifest.local.yaml`

```yaml
launch:
  upstream: http://127.0.0.1:8701
```

확인 — 두고 나서 `GET /api/v1/admin/integrations/builtin` 의 네 행에 `local_override.applied: true` 가 보이고, `worker.log` 에
'manifest.local.yaml 을 얹었다' 가 앱마다 한 줄씩 남는다. HH-14 를 받은 뒤에도 `heax_app_step_forge` 의 PID 가 그대로여야 한다.

두는 법(검토 뒤) — 같은 폴더에 임시 이름으로 다 쓴 뒤 `mv` 로 바꾼다. 쓰는 도중의 빈 파일이나 덜 쓰인 파일을 읽으면 그 앱은 보류되고(D-13) WARNING 이
남는다 — 추적 값으로 돌지는 않지만 그동안 라우트 재등록도 멈춘다. 넷은 검토 후속의 모양 규칙(D-14) 아래에서도 그대로 얹힌다 — 새 로더로 HH-14 판(`c1f8fbc`)에
얹으면 넷 모두 `applied` · `held` 아님이고 main(`5e48b25`)의 추적 매니페스트와 형까지 같으며 step_forge 의 SIF 열쇠도 같다(10-31 확인, 파일은 바꾸지 않았다).

## 검토 후속 — 2026-10-31(검토 열두 건)

검토 결과 열두 건을 사본에서 모두 재현한 뒤 처리했다. 새 시험은 커밋마다 고치기 전 사본(그 커밋의 부모 + 새 시험)에서 실패하는 것을 봤다.

| # | 무엇 | 처리 | 커밋 |
|---|---|---|---|
| 1 | YAML 로는 멀쩡한데 모양이 틀린 로컬 파일(`launch: service` · `launch: http://…` · 들여쓰기를 빠뜨린 `launch:`)이 reconcile 패스 전체 · 상태 화면 · 다른 앱의 찬 기동(HH-09)을 깨고, null 이 된 launch 는 HH-16 retire 로 갔다 | 고침 — 앱별로 견딘다(D-15), 모양 검사로 보류(D-14) | `cad8b2b` · `6b53981` |
| 2 · 7 | 깨지거나 비거나 못 읽히는 로컬 파일이 추적 값으로 떨어져(fail-open) HH-14 뒤 dev 에서 HH-16 이 건강한 `heax_app_step_forge` 를 내린다 | 고침 — 보류(D-13). 파일이 **사라진** 경우는 남는다(D-20) | `6b53981` |
| 3 | 호스트 프로세스 가드가 venv 콘솔 스크립트(shebang)를 남의 것으로 읽고, '건드리지 않는다' 두 갈래가 포트를 풀고 기록을 덮었다 | 고침(D-17) | `3d16b7a` |
| 4 · 12 | 포털 `check-mcp-registration.sh` · `drive-drift.sh` 가 추적 매니페스트를 직접 읽는다 | 미룸 — 다른 리포(HWAXPortal). 읽는 곳 지도에 넣고 아래 「포털에 알릴 것」 | — |
| 5 | `diagnose-app.sh` 의 id grep 이 꼬리 주석을 못 받아 step_forge 에서 HH-15 블록이 돌지 않는다 | 고침(D-18) | `d383e8b` |
| 6 · 8 | SIF 열쇠가 효과 매니페스트 전체를 해시 — 빌드 박스(dev) 덮어쓰기의 열쇠가 cae00 과 어긋나고, 전환 뒤 dev 가 짓는 `step_forge.sif` 가 cae00 허브 재기동을 부른다 | 고침 — dist-to-drive 가 기본으로 싣지 않는다 · MANIFEST_SPEC · cutover 주석(D-19) | `d4a6fe6` · cutover `c1f8fbc` |
| 9 | `retire_unrouted` 가 모드 없음 · 모르는 값 · 빈 매니페스트에도 돌아 도는 서비스를 내린다 | 고침(D-16) | `27d89fd` |
| 10 | cae00 이 이 판과 HH-14 를 한 번에 당기면 옛 허브가 상태 파일을 먼저 덮어 HH-16 이 인스턴스를 못 찾는다 | 문서 — 받는 순서(HH-16 답) | 문서 커밋 |
| 11 | B-1 처럼 당기기 전에 손으로 멈추면 reconcile 이 45초 안에 되살린다 | 문서 — HH-16 답 | 문서 커밋 |

- **D-13 로컬 파일이 있는데 못 얹으면 그 앱을 보류한다(D-3 뒤집기).** 덮어쓰기는 추적 값이 이 박스에 맞지 않아서 둔 것이라 마지막으로 좋았던 상태를
  지키는 쪽이 맞다. `load_file` 이 `LocalOverrideError` 를 올리고 도는 쪽이 그 앱을 건너뛴다 — reconcile 은 `held` 로 세고 라우트 · 인스턴스를 그대로,
  스캐너는 `skipped`(행 · 스냅숏 · 빌드 그대로 — 건너뛴 앱의 행을 지우지 않으므로 카탈로그 · MCP 레지스트리도 마지막 값), `redeploy-app.sh` 는 멈추기
  전에 rc 2. 보여 주는 쪽(상태 화면 · `diagnose-app.sh` · 앱 API 폴백)은 `load_file_with_override` 로 추적 값과 `held` 를 받는다. D-3 이 걱정한
  '카탈로그에서 빠진다' 는 스캐너가 행을 지우지 않아 일어나지 않는다. 대상이 없는 심링크도 보류다('파일 없음' 으로 치면 아무 말 없이 추적 값으로 갔다).
  한계 — 보류 중에는 그 앱의 라우트 재등록 · 죽은 인스턴스 재기동도 하지 않는다. 그 사이 Caddy 가 다시 뜨면 그 앱은 고칠 때까지 끊긴다. 스냅숏의 마지막
  값으로 도는 길은 reconcile 을 DB 에 묶어야 해서 하지 않았다(검토가 권한 대로 건너뛴다). WARNING 이 한 시간에 한 번 남는다.
- **D-14 모양 규칙 셋.** ① launch · build · permissions · resources · source · mcp 를 패치에 적으면 매핑이어야 한다. ② 추적에 있던 launch · build 블록을
  지우거나 launch.mode · build.stack 을 지우지 못한다. ③ 패치가 launch 나 build 를 건드리면 효과 매니페스트의 build.stack 과 launch.mode 가
  `config/stacks.yaml` 의 launch_mode 로 맞아야 한다 — 쓰는 도중에 읽혀 build 만 바뀐 파일(fastapi 스택에 proxy 모드)과 모드 오타를 잡는다.
  추적 매니페스트가 처음부터 어긋나 있으면 패치가 만든 어긋남만 본다(지금 리포의 서른 개는 모두 맞다). 정당한 덮어쓰기 넷(dev 의 step_forge ·
  kooremapper 셋)과 서비스 앱을 이 박스에서만 넘기는 꼴(`build: {stack: external_proxy}` 를 함께 적는다)은 그대로 얹힌다(시험). 첫 줄에서 끊겨
  `execution_target: linux_` 만 남은 파일은 못 잡는다 — 그래서 임시 이름에 쓰고 mv(MANIFEST_SPEC 10절 '고칠 때').
- **D-15 앱별로 견딘다.** reconcile 의 앱 하나를 `_reconcile_one` 으로 떼어 try 로 감싸고(그 앱만 failed), launch · mode 가 매핑 · 글자가 아니면 모드
  없음으로 친다. 상태 화면은 최상위 · build · launch 가 매핑이 아니면 빈 것으로, `_sibling_app_urls` 는 스냅숏 · launch 가 매핑이 아닌 앱만 건너뛴다.
  추적 매니페스트가 같은 모양이어도 같다 — 로컬 파일은 D-14 가 먼저 막는다.
- **D-16 비라우트 모드는 명시된 넷만.** `_UNROUTED_MODES` = url · iframe · job_runner · installer(= stacks.yaml 의 launch_mode − 라우트 모드, 시험이
  견준다)일 때만 `retire_unrouted`. 그 밖(모드 없음 · 오타 · 빈 매니페스트)은 남은 서비스 기록이 있을 때만 WARNING 을 한 시간에 한 번. 검토가 잰
  git checkout 중의 빈 읽기(읽기의 약 4.5%)도 이것으로 서비스를 내리지 않는다.
- **D-17 호스트 프로세스 가드.** 첫 토큰이 인터프리터(python* · node · nodejs · bash · sh · dash · perl · ruby)면 옵션을 건너뛴 다음 토큰을 기록의 첫
  토큰과 견준다. 이 앱의 것인지 확인하지 못한 두 갈래(인스턴스 이름이 다름 · 명령줄이 다름)는 False — 기록 · 포트를 그대로 두고 다음 패스가 다시 본다
  (경고는 한 시간에 한 번). 진짜 pid 재사용으로 남의 프로세스가 오래 살면 그 포트는 사람이 볼 때까지 묶인다 — 남의 것을 죽이거나 아직 쥐었을 수 있는
  포트를 다른 앱에 주는 것보다 낫다. `stop()`(`redeploy-app.sh`)도 같은 가드라 호스트 모드 앱을 이제 제대로 내린다(예전엔 'PID reuse' 로 거절하고 포트만 풀었다).
- **D-18 `diagnose-app.sh` id 정규식.** `^id:[[:space:]]*['"]?<id>['"]?[[:space:]]*(#.*)?$` — 따옴표 · 꼬리 주석을 받고 앞부분이 같은 다른 id 는 잡지 않는다.
  보류면 그 까닭과, 지금 걸린 업스트림은 상태 파일의 마지막 proxy 기록으로 보여 준다(추적 파일 값을 두드리지 않는다).
- **D-19 SIF 열쇠는 그대로, 싣는 쪽에서 거른다.** 열쇠에서 매니페스트 일부만 해시하면 모든 박스의 모든 앱 열쇠가 바뀌어 다시 빌드된다(D-5 와 같은 까닭).
  대신 `dist-to-drive.sh` 가 `manifest.local.yaml` 이 있는 앱의 SIF 를 기본으로 싣지 않고 '○ … 실으려면' 을 즉시 · 끝 요약에 찍는다.
  `HEAX_DRIVE_APP_SIFS` 로 이름을 대면 경고와 함께 싣는다. MANIFEST_SPEC 10절에 '다른 박스로 보내는 앱은 빌드 박스에서 덮어쓰지 않는다' 를 적었다.
  이번 넷은 해당하지 않는다(HH-14 판 step_forge 는 source 가 없어 cae00 이 짓지 않고, kooremapper 셋은 SIF 가 없다) — 걸리는 것은 (나) 전환 뒤 dev 의
  step_forge.sif 가 cae00 에 깔려 허브를 다시 띄우던 것이고, 이제 기본으로 싣지 않는다.
- **D-20 (요청자와 견줄 것) 극성 뒤집기.** 로컬 파일이 **사라진** 경우(지움 · `git clean -x` · 새 clone)는 지운 것과 구분할 수 없어 추적 값을 따른다 —
  HH-14 뒤 dev 에서는 그것이 곧 step_forge 가 16110 으로 가고 서비스가 내려가는 길이다(사본 재현 missing_file). 대안은 추적 매니페스트를 지금 값
  (dev 와 같은 서비스 · 8700 · 8701)으로 두고 cae00 이 `manifest.local.yaml` 로 운영 노드로 넘기는 것이다 — 합칠 시점을 맞출 필요도 되돌리는 커밋도
  없고, 새 박스는 안전한 기본값으로 뜬다. 다만 요청서(HH-14)가 추적 파일을 바꾸라고 했고 그 길이면 cae00 운영자가 박스 파일 넷을 둬야 하므로 요청자가
  정한다. 이 브랜치와 `cutover/prod-1031` 은 요청서대로 두었다.

### 포털에 알릴 것(HWAXPortal — 이 리포 밖, 검토 #4 · #12)

- `infra/scripts/check-mcp-registration.sh:102-103` 의 id grep 은 꼬리 주석이 달린 id(`id: step_forge   # …`)를 못 찾고, `:124-125` 는 추적 파일의
  `mode: proxy` · `upstream:` 을 grep 해 두드린다. 덮어쓴 dev 에서는 추적 값(16121 · 16120)을 두드려 멀쩡한 앱을 '업스트림 무응답' NG · rc 1 로 읽는다.
  고치는 길 — HEAXHub 로더(`backend/.venv/bin/python` 으로 `integration_manifest.load_file_with_override`, `held` 면 그 사실을 알린다)나 허브
  `GET /api/v1/apps/<id>/manifest`(효과 스냅숏)를 읽는다. `diagnose-app.sh` 의 HH-15 블록이 본보기다.
- `infra/scripts/drive-drift.sh:130-136` 은 SIF 열쇠를 추적 매니페스트로 센다 — 받는 박스 기준으로는 맞다. 덮어쓴 앱은 이제 dist-to-drive 가 싣지
  않으므로(D-19), 그런 앱에서 '열쇠가 맞지 않는다' 가 나오면 처방은 'dev 에서 다시 짓기' 가 아니라 '보내는 앱은 덮어쓰지 않기' 다.

## 시험 수

전체는 worktree 의 `backend` 에서 본 리포의 venv 로, 리포 밖 하네스(아래 「사고」)를 얹어 돌렸다.

| 판 | passed | failed | deselected |
|---|---|---|---|
| 기준선 origin/main `5e48b25` | 400 | 14 | 8 |
| HH-15 뒤 `0b02759` | 438(+38) | 14 | 8 |
| HH-16 뒤 `c33a9f6` | 450(+12) | 14 | 8 |
| 검토 후속 뒤 `d4a6fe6`(문서 커밋 앞) | 493(+43) | 14 | 8 |

실패 14 는 세 판 모두 같은 목록이다 — `test_launcher_reports` 9 · `test_launcher_agents::test_manifest_etag_then_304` 1(enroll 429, Redis 의 IP 당
10회/60초 창), `test_heavy_stacks::test_dotnet_launcher_argv` 1(.NET 인자 순서, 앞서부터), `test_updater_feed` 3(sqlalchemy 환경). 10-27 노트의 '13개' 와
하나 다른 것은 429 창에 걸리는 시험 수가 그때그때 달라서다.

새 시험이 실패할 수 있는지.

- 옛 코드 사본(origin/main + 새 시험) — HH-15 파일은 모듈이 없어 수집부터 실패한다. HH-16 파일은 12 중 11 이 실패한다(라우트만 다시 걸고 인스턴스를 안
  내린다 · 상태 파일이 proxy 기록으로 덮여 instance_name 이 사라진다). 남은 하나 'service 로 도는 앱은 그대로다' 는 옛 코드에서도 맞아야 하는 지킴 시험이다.
- 새 로더 + 옛 읽는 곳 사본 — 읽는 곳 시험 여섯(상태 화면 · reconcile 둘 · 스캐너 · gitignore · stash)이 실패한다.
- 변이(사본) — HH-15 여덟(null · 목록 이어 붙이기 · id · 깨진 파일 예외 · 되풀이 로그 · 표지 덧붙이기 · 바뀐 키 · 상태 화면 행), HH-16 열둘(순서 · 이름 확인 ·
  실패 기록 · 이미 없음 · 잠금 · service 앱도 내리기 · url 라우트 · static 상태 · 호스트 명령줄 · 포트 · 로그 · 잠금 안 다시 읽기)을 모두 잡는다.
  지킴 시험은 'service 앱도 내리기' 변이에서 깨진다.
- 검토 후속(10-31) — 새 시험 마흔셋을 커밋마다 그 부모 사본에서 돌렸다. #9 여섯 중 다섯 실패(`{'retired': 1}`, 하나는 '남은 기록이 없으면 말하지 않는다'
  지킴 시험), 앱별로 견디기 넷 모두 실패(AttributeError · RuntimeError), 보류 서른하나 실패(추적 값으로 `started` · stop · release, 스캐너 `unchanged`,
  redeploy 가 stop · launch 를 부름, diagnose 가 '무시됨' 과 추적 업스트림), 호스트 가드 다섯 실패(포트를 풀었다 · 자기 프로세스를 남의 것으로),
  diagnose id 셋 실패, dist-to-drive 둘 실패. 실패 14 는 기준선과 같은 목록이고, 전체 실행 앞뒤의 `heax_app_*` 이름 · PID 가 같았다.
  호스트 가드 시험은 진짜 프로세스(shebang 스크립트 · sleep · 다른 파이썬 스크립트)를 자기 세션으로 띄우고 끝에 그 묶음만 정리한다.

## 사고 — worktree 에서 전체 시험을 돌리다 dev 의 앱 인스턴스 다섯을 멈췄다(2026-10-30 22:21~22:26 UTC)

- 무엇이 — 첫 기준선 실행(하네스 없이)의 `test_api_smoke` 가 `with TestClient(app)` 로 앱 lifespan 을 그대로 돌렸다. lifespan 은 기동 스캔과 reconcile 을
  **이 worktree 의 integrations/ 와 dev 의 실제 DB · Caddy · apptainer** 로 돌린다. worktree 의 `var/` 가 비어 있어 upstream 을 받아 SIF 를 지었고(3.4GB),
  launch() 는 상태 파일이 없으니 찬 기동으로 갔다. 같은 이름의 dev 인스턴스가 떠 있어 instance start 를 건너뛰고 그 안에서 서버를 하나 더 exec 했고, 포트가
  겹쳐 곧 끝나자 `_sif_fail(..., instance_name=...)`(HH-06)이 그 **dev 인스턴스**를 멈췄다.
- 멈춘 것 — dev 의 `heax_app_doc_forge`(22:21:17) · `heax_app_hwax_risk`(22:22:09) · `heax_app_laminate_analyzer_mcp`(22:22:26) · `heax_app_paper_ingest`(22:25:05) ·
  `heax_app_step_forge`(22:26:31). dev 허브의 reconcile 이 각각 14초 · 9초 · 36초 · 12초 · 16초 뒤 다시 띄웠다(dev `worker.log` 의 'reconcile: recovered …').
  그 실행은 22:26:33 에 밖에서 SIGTERM 을 받아 끝났고(rc 143), worktree 의 `var/` 는 22:29 쯤 누군가 지웠다. dev DB 의 App.workspace_path ·
  AppVersion.sif_path 가 잠시 worktree 를 가리켰다가 dev 스캐너가 되돌렸다(읽기로 확인 — 남은 행 0). 그 창(22:14~22:30)의 감사 행은 `integration.build.failed` 24 ·
  `integration.launch.failed` 6 인데, 22:19 의 doc_forge 재기동과 일부 행은 이 실행의 첫 기동보다 앞서 다른 실행의 것으로 보인다(확인 못 함).
- 그 뒤 — 리포 밖 하네스(`pytest -p heax_lifespan_guard`, 스크래치패드)로 `integrations_scanner.INTEGRATIONS_ROOT` · `integration_tasks._INTEGRATIONS_ROOT` 를
  빈 임시 폴더로 돌려 lifespan 이 아무 앱도 보지 않게 하고 모든 실행을 다시 했다. 실행마다 `heax_app_*` 이름 · PID 를 앞뒤로 대조했다 — 바뀐 것 없음.
- 따로 고칠 것(이 브랜치 밖, 알림) — **HH-06 의 되돌림.** `_sif_fail` 은 '이번 호출이 띄운 인스턴스' 만 멈춰야 하는데(그 독스트링의 뜻), exec 실패 · 조기 종료
  경로(`integration_launcher.py:808` · `:817`, origin/main 기준 `:797` · `:806`)는 instance start 를 건너뛰어 원래 떠 있던 인스턴스에도 `instance_name` 을 넘긴다.
  그래서 상태 파일을 잃은 채 떠 있는 건강한 인스턴스(상태 디렉터리를 비운 박스 · schema 판이 바뀐 상태 파일)를 reconcile 이 한 번 죽였다 살린다.
  고치는 길 — `instance_name not in running_instances` 로 이번에 띄운 경우에만 넘긴다. 운영에서도 날 수 있는 일이라 다음 판에 넣기를 권한다.
- 시험 쪽 함정 — `test_api_smoke` · `test_pat` · `test_publish_route` 는 lifespan 을 돈다. 본 체크아웃에서도 그 스캔은 dev 의 `var/integration_workspaces` 에서
  git fetch 를 하고, 새 커밋이 있으면 SIF 를 다시 짓는다(도는 인스턴스 밑의 SIF 를 바꾼다). conftest 에서 두 뿌리를 빈 폴더로 돌리는 것을 권한다 —
  이 브랜치에서는 하지 않았다(요청 밖).

## 확인하지 못한 것

- 새 코드로 dev 허브를 띄워 보지 않았다(이 박스의 서비스를 다시 띄우지 않는다는 규칙). 실제 apptainer `instance stop` 의 'no instance found' 문구는
  설치된 1.3.x 바이너리의 문자열로만 확인했다.
- dev 덮어쓰기 넷은 본 체크아웃에 두지 않았다(본 체크아웃은 손대지 않는다) — 내용만 위에 적었다.
- 스크래치패드 `heax-head` 사본의 SIF 로 10-17 부터 떠 있는 `heax_app_thermal_shock_mcp` 가 있다 — 이 작업 전부터 있던 것이라 건드리지 않았다.
- (검토 후속) 보류는 대역 위에서만 봤다 — dev 허브에 새 코드를 띄우지 않았다. 검토 실험 e6 의 틈(proxy 갈래가 잠금을 놓은 뒤 proxy 기록을 쓰기 전에 두 번째
  워커가 같은 서비스 기록으로 instance stop('no instance found') · release_port 를 한 번 더 할 수 있다)은 검토 결과 목록에 없어 고치지 않았다 — proxy 앱은
  reconcile 만 launch() 를 부르므로(스캐너는 service 스택만) 겹치는 것은 기동 때 reconcile 과 beat reconcile 이 같은 순간에 돌 때뿐이다.
