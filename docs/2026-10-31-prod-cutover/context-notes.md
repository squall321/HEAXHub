<!-- 2026-10-31 운영 전환 HEAXHub 몫(HH-15 · HH-16)을 하며 내린 결정 · 근거 · 읽는 곳 지도 · 시험 수를 적어 두는 메모 -->
# 컨텍스트 노트 — 2026-10-31 운영 전환(HH-15 · HH-16)

브랜치 `wt1031/override`(origin/main `5e48b25` 에서). 10-27 의 HH-02 · HH-04~HH-09 · HH-12 는 이미 main 에 있어(`1dab32a..42d3b94`)
다시 하지 않았다. HH-14(매니페스트 넷)는 `cutover/prod-1031`(`d2e85c2`) 몫이라 이 브랜치에 없다.

## 전제 — dev 도 이 매니페스트를 쓰는 박스다(HH-15 의 답)

포털 세션이 확인한 사실이다. dev 는 `heax_app_step_forge` 를 허브가 빌드한 서비스 앱으로 띄우고, `kooremapper` · `kooremapper_api` ·
`kooremapper_mcp` 를 `127.0.0.1:8700` · `:8701` 로 넘긴다. 그래서 HH-14 가 합쳐지기 **전에** 박스 덮어쓰기가 있어야 한다 — 없으면
dev 의 네 앱과 게이트웨이 백엔드 `heax-step_forge` · `heax-kooremapper_mcp` 가 끊긴다(dev 에는 16110 · 16120 · 16121 이 없다).

## 읽는 곳 지도 — `integrations/*/.portal/manifest.yaml`

줄 번호는 이 브랜치 끝(HH-16 커밋 뒤) 기준이다. 「로더」 는 `integration_manifest.load_file` / `load_file_with_override` 를 쓴다는 뜻이다.

| 읽는 곳 | 파일:줄 | 무엇에 쓰나 | 바꾼 것 |
|---|---|---|---|
| 스캐너 | `backend/app/services/integrations_scanner.py:152` `_load_manifest` → `:159`, 부르는 곳 `:854-855` · 존재 확인 `:1110` | App · AppVersion 행, `manifest_snapshot`(`:1016` · `:1046`), SIF 빌드(`:414` → `integration_sif_builder.py:336` `_hash_inputs`), 기동(`:530-535`) | 로더 |
| reconcile(45초 · 기동 때 한 번) | `backend/app/workers/integration_tasks.py:100-106` | 라우트 · 기동(`:142` launch()), 모드 거르기 `:114` | 로더 · HH-16 |
| 런처 | `backend/app/services/integration_launcher.py:1748` `read_manifest`(부르는 곳 없음) | — | 로더(일관성) |
| 런처 기동 지문 | `integration_launcher.py:144` `_launch_fingerprint`, `:209` | 되풀이 실패 늦추기 — 받은 매니페스트를 해시 | 읽지 않는다(효과 매니페스트를 받는다) |
| 상태 화면 | `backend/app/api/v1/admin.py:406` `list_builtin_integrations` → `:438` | 앱별 상태 표 | 로더 · `local_override` · 뿌리 바로잡기(`:426`) |
| 앱 상세 · 매니페스트 API 폴백 | `backend/app/api/v1/apps.py:176-185` · `:213-221` | 스냅숏이 없을 때만 | 로더(integrations 쪽만). 이 경로는 `parents[3]` 이 `backend/integrations` 라 실제로는 닿지 않는다 — 고치지 않았다 |
| redeploy-app.sh | `deploy/apptainer/redeploy-app.sh:48` | 손 재기동(stop → launch) · `--rebuild` | 로더 |
| diagnose-app.sh | `scripts/diagnose-app.sh:15-20`(추적 파일 grep) · `:24-50` | 운영자 진단 — 업스트림 두드리기 | 로컬 파일이 있으면 효과 값과 그 업스트림. 없으면 출력이 예전과 같다 |
| bundle.sh | `deploy/apptainer/bundle.sh:56-73` | 리포 통째 tar | 로컬 파일을 뺀다(`:72`) |
| backup.sh | `scripts/backup.sh:79` | `integrations` 통째 tar | 그대로 — 이 박스의 덮어쓰기도 백업에 들어가는 것이 맞다 |
| 외부 연계 앱 생성 | `deploy/apptainer/_external_apps.py:124-148` | `var/external-apps.yaml` → `integrations/ext_*/` 를 **쓴다**(읽지 않는다) · 빠진 것은 rmtree | 그대로 — ext_* 는 박스 파일에서 만들어지므로 덮어쓰기가 필요 없다 |
| 등록 스크립트 | `scripts/register-repo.sh` · `register-url.sh` · `register-from-csv.sh` | 매니페스트를 **쓴다** | 그대로 |
| deprovision | `deploy/apptainer/_deprovision_app.py:66-73` | 폴더가 남았는지만 본다 | 그대로 — `git rm` 뒤 무시된 로컬 파일이 폴더를 남길 수 있어 MANIFEST_SPEC 에 '같이 지운다' 를 적었다 |
| 스냅숏을 읽는 곳(간접) | `api/v1/mcp.py:70-75`(MCP 레지스트리) · `integration_launcher.py:933`(형제 주소 HH-09) · `runners/slurm_runner.py:105` · `runners/resource_hooks.py:48` · `runners/apptainer_runner.py:88` · `workers/build_tasks.py:43` · `services/service_manager.py:106` | 스캐너가 쓴 효과 스냅숏을 읽는다 | 그대로 — 스캐너가 효과 매니페스트를 적으므로 따라온다 |
| 다른 매니페스트(대상 아님) | `workers/sync_tasks.py:85-122` · `:189-216`, `services/overlay_synthesizer.py`, `scripts/provision_workspace.sh`, `backend/scripts/seed_streamlit_sample.py` | 사용자 등록 앱의 `app_workspaces/<id>/overlay/.portal/manifest.yaml` · 템플릿 | 손대지 않음 — integrations 가 아니다 |
| 포털 쪽(이 리포 밖) | HWAXPortal `deploy-all-from-drive.sh:120`(git stash -u) · `:353`(허브 재기동 지문) | 허브 리포를 당기고 재기동 여부를 정한다 | 손대지 않음 — 지문에 로컬 파일이 없어도 허브가 reconcile 로 스스로 따른다 |

CI(`.github/workflows/*`) · `contracts/` · `infra/` 에는 integrations 매니페스트를 읽는 것이 없다.

## 결정

- **D-1 로더 하나 — `app/services/integration_manifest.py`.** 이름은 `integration_launcher` · `integration_sif_builder` 와 같은 줄에 둔다.
  추적 파일을 읽다 난 예외(파일 없음 · YAML 오류)는 그대로 올린다 — 부르는 쪽의 처리(스캐너 'manifest unreadable', reconcile 'load failed',
  상태 화면 `__error__`)가 예전 그대로 돌아야 '로컬 파일이 없으면 예전과 같다' 가 성립한다.
- **D-2 병합은 RFC 7386 그대로.** 부록 A 의 사례 열다섯을 시험에 그대로 넣었다. 목록은 이어 붙이지 않는다(tags · env 목록을 박스가 통째로 정한다).
- **D-3 깨진 로컬 파일은 통째로 무시.** 부분만 얹으면 어디까지 들어갔는지 알 수 없다. 예외로 올리면 오타 하나로 그 앱이 카탈로그에서 빠진다.
  대신 WARNING(경로 · 까닭) — 45초마다 남기면 HH-08 의 로그 소음이 되므로 같은 내용은 한 시간에 한 번만 다시 남긴다. 상태 화면에도 까닭이 보인다.
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

## 받기 전 점검 — 첫 reconcile 이 무엇을 내릴지(읽기만)

이 판을 받은 박스의 첫 reconcile 은 '서비스 기록인데 효과 모드가 service 가 아닌' 상태 파일을 모두 정리한다. 받기 전에 리포 루트에서
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
    if f.stem in modes and modes[f.stem] != "service" and d.get("mode") != "proxy" and (d.get("instance_name") or (d.get("pid") and d.get("argv"))):
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

## 시험 수

전체는 worktree 의 `backend` 에서 본 리포의 venv 로, 리포 밖 하네스(아래 「사고」)를 얹어 돌렸다.

| 판 | passed | failed | deselected |
|---|---|---|---|
| 기준선 origin/main `5e48b25` | 400 | 14 | 8 |
| HH-15 뒤 `0b02759` | 438(+38) | 14 | 8 |
| HH-16 뒤 `c33a9f6` | 450(+12) | 14 | 8 |

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
