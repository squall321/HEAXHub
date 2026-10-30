<!-- 2026-10-31 운영 전환 요청서 두 편의 원문(HH-14 · HH-15 · HH-16) — 받은 그대로이고 메일 주소만 지웠다 -->
# 요청서 원문 — 2026-10-31 운영 전환

HWAX Portal 운영이 보낸 두 편을 받은 그대로 옮겼다. 이 리포는 GitHub 에 공개라 요청자 메일 주소 두 곳만 `(메일 주소 — 공개 리포라 지웠다)` 로 바꿨다. 이 브랜치가 맡은 것은 HH-15 · HH-16 이고, HH-14(매니페스트 넷)는 전환 창 바로 앞에 합칠 다른 브랜치 몫이다.

- 1편 `HEAXHUB-PROD-CUTOVER-REQUEST.md` — 매니페스트 넷의 바꿀 내용(HH-14)
- 2편 `06-HEAXHub-1027-with-1031.md` — 10-27 요청서에 10-31 행(HH-14 · HH-15 · HH-16)을 더한 판

---

<!-- 1편 원문 시작: HEAXHUB-PROD-CUTOVER-REQUEST.md -->

# HEAXHub 매니페스트 넷 — StepForge · DynaForge 경로를 운영 노드로 (2026-10-31)



요청일 2026-10-31 · 요청 (메일 주소 — 공개 리포라 지웠다)(HWAX Portal 운영) · 대상 **HEAXHub** `integrations/` 매니페스트 4개

근거 — HWAXProd `CUTOVER.md`(10-31 실측) · `PLAN.md` 결정 5 · 10장 HEAXHub 행



## 한 줄



- `/apps/step_forge` · `/apps/kooremapper` · `/apps/kooremapper_api` · `/apps/kooremapper_mcp` 가 cae00 앱 대신 운영 노드(STE)의 StepForge · DynaForge 로 가게 한다.

- 길은 cae00 의 운영 터널 포트(사용자 유닛 `hwaxprod-tunnel`)다: 16110 = StepForge, 16120 = DynaForge API, 16121 = DynaForge MCP.

- 앱 id · 경로 · 게이트웨이 키(`heax-step_forge` · `heax-kooremapper_mcp`) · 도구 이름은 그대로다. 리스크 심사 · 심의 · 챗 · 포털 STEP 업로드는 이 경로와 키를 따라가므로 따로 고칠 것이 없다.



## 왜 매니페스트인가



- 게이트웨이의 `heax-<id>` 백엔드 주소는 HEAX 레지스트리가 주는 `/apps/<id>/mcp` 다(HWAXMcpGateway `gateway.py` `_discover_heax`). 경로가 바뀌면 게이트웨이도 따라간다.

- HEAX 는 `integrations/*/.portal/manifest.yaml` 만 읽고 박스 전용 덮어쓰기 장치가 없다. 그래서 박스에서 고치면 다음 pull 에 되돌아간다.



## 바꿀 것



### 1. `integrations/step_forge/.portal/manifest.yaml` — 서비스 앱을 proxy 로



지금은 HEAX 가 빌드해 cae00 에서 띄운다(`execution_target: linux_runner`, `launch.mode: service`, 8행 · 14~25행 · 32~46행). 아래처럼 바꾼다. `id` · `name` · `description` · `tags` · `permissions` · `mcp` 는 그대로 둔다.



```yaml

execution_target: external_url

build:

  stack: external_proxy

launch:

  mode: proxy

  upstream: http://127.0.0.1:16110     # cae00 hwaxprod-tunnel → 운영 노드 StepForge(--root-path /apps/step_forge)

  strip_prefix: true

  portal_auth: true                    # 포털 forward_auth + X-Heax-* 신원 · 허브 공용 비밀 전파(지금과 같다)

permissions:

  visibility: company

mcp:

  expose: true

  path: /mcp

  transport: streamable_http

  description: STEP 어셈블리 점검·관계 검출·메시·K파일 내보내기 도구

```



- `resources` · `source` 블록은 뺀다(proxy 앱은 빌드 · 실행하지 않는다).

- 모양은 cae00 박스에서 10-24 부터 도는 `stepforge_prod`(proxy · 16110 · portal_auth)와 같다.



### 2. `integrations/kooremapper/.portal/manifest.yaml`



```yaml

  upstream: http://127.0.0.1:16120     # 운영 노드 DynaForge API(SPA 도 서빙)

```



### 3. `integrations/kooremapper-api/.portal/manifest.yaml`



```yaml

  upstream: http://127.0.0.1:16120     # 운영 노드 DynaForge REST(웹앱과 같은 서버)

```



### 4. `integrations/kooremapper-mcp/.portal/manifest.yaml`



```yaml

  upstream: http://127.0.0.1:16121     # 운영 노드 DynaForge MCP(streamable-http)

```



- DynaForge MCP 는 HEAX Caddy 가 보내는 `Host: 127.0.0.1` 을 기본으로 받는다(`mcp_server/server.py` 의 기본 허용 목록). 운영 노드 쪽 설정은 그대로 둔다.



## 반영 시점



- **커밋 · push 는 전환 창 바로 앞에 한다.** 그 전에 cae00 이 당기면(update-all) 데이터를 합치기 전에 경로가 운영 노드로 바뀐다.

- cae00 반영은 전환 창 안에서 한다: `git pull` → HEAX 재기동(update-all §2 의 deploy-all heax와 같다, 도는 심의가 없을 때). 순서는 HWAXProd `CUTOVER.md` 5장 B-5 다.

- 되돌릴 때는 이 네 파일을 되돌리는 커밋 하나와 같은 반영 절차다.



## 함께 알아 둘 것



- **StepForge 빌드:** `step_forge` 가 proxy 가 되면 HEAX 가 StepForge 를 더 빌드하지 않는다(`var/sifs/step_forge.sif` 갱신이 멈춘다). 운영 노드 판 올리기는 StepForge 배포본(Drive 의 dist)에서 받는 쪽으로 바꾼다 — HWAXProd 몫이고 이 요청과는 따로다.

- **다른 박스:** HEAXHub 를 당기는 다른 박스가 있으면 그 박스에는 16110 · 16120 · 16121 이 없어 네 앱이 끊긴다. 그런 박스가 있으면 알려 주십시오 — 그때는 박스 전용 덮어쓰기(`.portal/manifest.local.yaml` 같은 것)를 먼저 요청한다.

- **게이트웨이 쪽**은 추적 변경이 없다. 심의 DynaForge 위임 주소와 REST 다리는 cae00 `provision.env` 의 `KOORM_BASE` 로 바꾼다(박스 값).



---



담당: HWAX Portal 운영 ((메일 주소 — 공개 리포라 지웠다))

<!-- 1편 원문 끝 -->

---

<!-- 2편 원문 시작: 06-HEAXHub-1027-with-1031.md -->

# HEAXHub — 수정 요청 (2026-10-27 · cae00 운영 기준)



요청 HWAX Portal 운영(cae00) · 대상 저장소 HEAXHub(`deploy/apptainer` 배포 스크립트 · 앱 실행기 · 헬스 점검)와 허브가 띄우는 DocForge(HEAXHub doc-forge) · MaterialTwinWeb(cae00 사본 `HEAXHub/var/integration_workspaces/materialtwin-web/upstream`) · 기준 판 HEAXHub `4e1954d`(update-all 안전 · 10-17 수정 목록의 파일:줄) · `973444a`(10-25 cae00 시험) · MaterialTwinWeb `6e45b40` · DocForge release `7b166ac` · update-all 로 받는 플랫폼: 예.



줄 번호는 각 행의 출처 문서가 적은 기준 판의 것이다. 10-27 에 cae00 HEAD 로 다시 본 행은 근거 칸에 그 결과를 적었다. 받는 곳은 셋이다 — HEAXHub 개발(HH-02 · HH-04~HH-09 · HH-12 · HH-14~HH-16), DocForge 개발(HH-03), MaterialTwinWeb 개발 · 데이터 담당(HH-01 · HH-10 · HH-11 · HH-13). StepForge 와 리스크 앱(hwax-risk)은 따로 보낸다(05-StepForge.md · 07-hwax-risk.md). 항목 번호 가운데 제품 코드처럼 읽힐 수 있는 것(S · A · M · X · P 로 시작하는 짧은 번호)은 원문 번호에 '-' 를 넣어 적었다(예 10-25 시험 M-14).



**한 줄**

- (10-31 추가) StepForge · DynaForge 운영 전환 — 매니페스트 넷의 upstream 을 운영 노드 터널 포트로 바꾼다. **우리가 알리는 전환 창 바로 앞에만 main 에 합친다**(HH-14).

- MaterialTwinWeb 이 재료 카드를 허브로 낼 때 본문을 6만 자에서 자른다. 그래서 챗과 전문가 화면에서 카드 뒤쪽을 볼 길이 없다. 가장 먼저 고칠 것이다(HH-01).

- HEAXHub 가 새 SIF 를 도는 인스턴스가 물고 있는 파일 위에 제자리로 덮어쓴다. 재기동이 빠지면 앱이 깨진 마운트로 돈다(HH-02).

- DocForge 의 코어 거울은 아직 구현 전이다. 코어(자주검증 Datahub) 연결은 다른 앱에서 10-27 에 살아났다(HH-03).

- 부분 배포가 `latest/SHA256SUMS` 를 덮어 SIF 가 대조 없이 깔리고, app-data 배포가 박스 전용 `knobs.env` 를 심을 수 있다(HH-04 · HH-05).

- 나머지는 데모 앱 기동 실패 · 헬스 404 · 로그 소음이다. cae00 의 데모 두 개는 우리가 내린다.



**건수**



| 높음 | 중간 | 낮음 · 선택 | 질문 | 결정 필요 |

|---|---|---|---|---|

| 2 | 2 | 8 | 4 | 0 |



## 1. 높음 — 먼저 고칠 것



### HEAXHub (10-31 추가)



| # | 무엇이 | 근거 (파일:줄 · 실측 숫자) | 할 일 | 출처 |

|---|---|---|---|---|

| HH-14 | StepForge · DynaForge 운영 전환 — `/apps/step_forge` · `/apps/kooremapper` · `/apps/kooremapper_api` · `/apps/kooremapper_mcp` 가 cae00 앱 대신 운영 노드로 가야 한다. 반영 시점이 정해져 있다. | 게이트웨이의 `heax-<id>` 백엔드 주소는 HEAX 레지스트리가 주는 `/apps/<id>/mcp` 다(HWAXMcpGateway `gateway.py` `_discover_heax`). 리스크 앱은 키 `heax-step_forge` · `heax-kooremapper_mcp` 를 고정해 쓴다(hwax-risk `adapters/registry.py:30-31` · `mcad.py:338-347`). HEAX 는 `integrations/*/.portal/manifest.yaml` 만 읽고 박스 덮어쓰기가 없다. 10-31 HEAX Caddy 설정: step_forge → :9115(서비스 앱), kooremapper · kooremapper_api → :8700, kooremapper_mcp → :8701. 같은 꼴의 운영 경로 `stepforge_prod`(proxy · 16110 · portal_auth)가 10-24 부터 박스에서 돈다. | 매니페스트 넷의 바꿀 내용은 `../HEAXHUB-PROD-CUTOVER-REQUEST.md` 다 — step_forge 는 proxy(upstream `http://127.0.0.1:16110`, strip_prefix, portal_auth, mcp 블록 그대로)로, kooremapper · kooremapper_api 는 16120, kooremapper_mcp 는 16121. **main 에는 우리가 알리는 전환 창 바로 앞에 합친다.** 먼저 합치면 update-all 이 데이터를 합치기 전에 경로를 운영 노드로 바꾼다. 브랜치로 만들어 두고 합치는 때를 알려 받는 꼴을 권한다. step_forge 를 proxy 로 바꾸면 HEAX 가 StepForge 를 더 빌드하지 않는다(`var/sifs/step_forge.sif` 갱신이 멈춘다 — 운영 판의 원천을 Drive 배포본으로 옮기는 일은 우리 몫이다). | HWAXProd `CUTOVER.md`(10-31 실측) · `PLAN.md` 결정 5 · 10장 |



### MaterialTwinWeb



| # | 무엇이 | 근거 (파일:줄 · 실측 숫자) | 할 일 | 출처 |

|---|---|---|---|---|

| HH-01 | 재료 카드 본문을 허브로 낼 때 6만 자에서 자른다. 잘린 뒤쪽은 화면 어디에도 나오지 않는다. | 기준 `6e45b40` · `backend/app/routers/hub_export.py:28` `_BODY_MAX_CHARS = 60_000` · `:79-80` · `:27` 주석. 10-27 에 cae00 사본 HEAD `6e45b40` 에서 `:28` · `:79-80` 이 그대로임을 다시 봤다. material-twin-analyst 앞 300장(전체 2,729장) 중 52장이 절 하나 60,038자이고 끝에 「본문 절단 — 전체는 content.properties 참조」 표지가 붙는다. 전체 값 `content.properties`(72,047~807,029자)는 읽기 칸이 버린다. 고친 커밋 `a3563b6` 은 10-15 에 로컬에만 있고 올리지 않았다는 기록이 있다. | 본문을 자르지 말고 6만 자 이하 절 여러 개(`sections`)로 내보낸다. 「(가) 원천에서 고친다」로 이미 정해져 있다. 절 크기 한도는 MaterialTwinWeb 담당이 정한다. `a3563b6` 이 이 고침이면 올려서 배포한다. | 지식카드 K3 요청 9 · 운영 쪽 13 |



## 2. 중간



### HEAXHub



| # | 무엇이 | 근거 (파일:줄 · 실측 숫자) | 할 일 | 출처 |

|---|---|---|---|---|

| HH-02 | 앱 SIF 를 도는 인스턴스가 물고 있는 파일 위에 제자리로 덮어쓴다. | 기준 `4e1954d` · `deploy/apptainer/dist-from-drive.sh:40`(`_install_if_changed` 의 `cp -p`) · 호출 `:82-85`(HEAD `973444a` 기준 `:40` 의 `cp -p` 가 그대로이고 호출은 `:84-85`. 실패하면 rc 2 를 내는 것만 들어 있다, 10-27 확인). 같은 inode 라 squashfuse 가 문 파일이 그 자리에서 바뀐다. 재기동이 빠지면(`NO_RESTART=1`, 또는 `.hash` 복사 실패로 빠져나가면) 깨진 마운트로 계속 돈다. `stop.sh:27-30` 이 이 고장(libc.so.6 로드 실패 · 앱 404)을 이미 적어 두었다. | 같은 폴더의 임시 이름(끝이 `.sif` 가 아닌 것)으로 복사한 뒤 `mv -f` 로 바꾼다. 실패하면 임시 파일을 지우고 rc 2 로 끝낸다. 본보기는 포털 `infra/scripts/lib/change-detect.sh` 의 `hwax_install_if_changed` 다. | update-all 안전 U7 |



### DocForge



| # | 무엇이 | 근거 (파일:줄 · 실측 숫자) | 할 일 | 출처 |

|---|---|---|---|---|

| HH-03 | SP 거울(코어 받기)이 구현 전이다. 명세에 적힌 관계가 코어 정본에 없다. | release `7b166ac` · `/app/docforge/ontology/sp_mirror.py:17-18`(NotImplementedError) · `:6-7`(명세) · `config.py:25` · `:98` · `:156`(설정 자리는 있다). 명세의 `plm_derived_from` 과 작업 코어 관계 `dept_in_charge` · `responsible` · `participates` 가 rootdesign 에 없다. | 거울(M-4)을 구현한다. 명세를 실제 공개에 맞춘다 — `plm_task` key 는 과제명이고 과제코드는 `properties.task_code` 에 있다. 관계는 `plm_project` 의 `compare_baseline` · `predecessor` · `predecessor_by_name` 셋뿐이다. | 코어 요청 D-1 |



## 3. 낮음 · 선택



### HEAXHub



| # | 무엇이 | 근거 (파일:줄 · 실측 숫자) | 할 일 | 출처 |

|---|---|---|---|---|

| HH-04 | 부분 배포가 Drive `latest/SHA256SUMS` 를 통째로 덮는다. 그래서 목록에서 빠진 SIF 는 sha256 대조 없이 깔린다. | 기준 `4e1954d` · `deploy/apptainer/dist-to-drive.sh:112-116` · 받는 쪽 `dist-from-drive.sh:44`(목록에 있는 파일만 대조). 10-15 step_forge 부분 배포 뒤 리스크 앱 SIF(204MB) 줄이 없었다. 10-17 hwax-risk 부분 배포 뒤에는 `step_forge.sif` 줄이 없었다. | 부분 배포 때 옛 목록에서 이번에 올린 이름의 줄만 바꿔 합친다. 아니면 latest 전체를 `rclone hashsum sha256` 으로 다시 만든다. 줄 꼴(`./이름` 이든 이름만이든)을 하나로 통일한다. | update-all 안전 U8 · 10-17 수정 목록 X-6 |

| HH-05 | app-data 배포가 박스마다 정하는 `knobs.env` 를 싣고 운영 박스에 심을 수 있다(잠재). | 기준 `4e1954d` · 올리는 쪽 `appdata-to-drive.sh:45` 는 `cred.key` 와 `secrets.env` 만 뺀다. 받는 쪽 `appdata-merge-from-drive.sh:44-54` 는 박스에 없는 파일을 심는다. 리스크 앱 검토 스위치 값이 틀리면 앱이 기동하지 않는다. 지금 Drive app-data 에는 없다. | 올리는 쪽은 빼는 목록에 `-o -name 'knobs.env'` 를 더한다. 받는 쪽 심기 단계는 `knobs.env` 를 결코 심지 않는다. | update-all 안전 U9 |

| HH-06 | 데모 앱 `heax_demo_nextjs` 가 기동 실패를 되풀이하고, 실패 뒤 앱 없는 인스턴스 셸이 남는다. | corepack 이 사내 인증서 때문에 pnpm 을 못 받는다. 10-17 에 'launch failed … code=1' 48,600여 회 · 통합 로그 392MB 였다. 10-25 에는 failures=160 · appinit 만 있는 인스턴스 · 9105 무응답이었다. | SIF 에 pnpm 을 동봉하거나 corepack 을 오프라인으로 돌린다. integration_launcher 가 실패하면 인스턴스 셸까지 정리하게 한다. | 10-25 피드백 HX-2 · 10-17 수정 목록 X-4 |

| HH-07 | `--root-path` 앱의 헬스 점검이 경로 접두를 두 번 붙여 404 가 쌓인다. | `/apps/<id>/apps/<id>/health` 404 가 앱마다 약 45초에 한 번 쌓인다. upstream 을 직접 부르면 200 이다. 500 미만을 정상으로 치는 설계라 상태 판정은 맞고 로그만 쌓인다(10-17 수정 목록 5절, 10여 개 앱). | 헬스 점검 URL 을 만들 때 base_path 를 두 번 붙이지 않게 고친다. | 10-25 피드백 HX-1 · 10-17 수정 목록 5절 |

| HH-08 | 데모 · 원본 저장소 관련 로그 소음(mkdocs-static 실패 · build failed 메일 dry-run)이 되풀이된다. | worker.log 에 10-25 16:30 이후 ×35 · ×12, 업데이트 전 173건. 실제 오류를 찾기 어렵게 한다. | 실패 메일은 하루 한 번으로 묶는다. 데모를 계속 둔다면 mkdocs-static 에 site 를 넣는다. cae00 에서 데모를 내리는 일은 우리 몫이다. | 10-25 피드백 HX-3 |

| HH-09 | (선택) 허브가 띄운 형제 앱의 주소를 앱에 넣어 주지 않는다. 그래서 StepForge 의 MaterialTwin 기본 주소가 cae00 에 없는 포트를 가리킨다. | StepForge `core/materials.py:41` `TWIN_DEFAULT = "http://127.0.0.1:9233"`(기준 `5c2d11a`). cae00 MaterialTwin 은 허브가 배정한 앱 포트(10-18 에 9107)나 허브 프록시 `http://127.0.0.1:4180/apps/materialtwin_web` 에 있다. | 형제 앱 주소를 `HEAX_APP_URL_<slug>` 꼴로 넣어 준다. StepForge 가 기본값을 허브 프록시로 유도하는 길(05-StepForge.md ST-07)과 둘 중 하나만 하면 된다. 어느 쪽이 맡을지 StepForge 와 맞춰 알려 달라(05-StepForge.md ST-11). | 10-18 제안 S-1 |



### MaterialTwinWeb



| # | 무엇이 | 근거 (파일:줄 · 실측 숫자) | 할 일 | 출처 |

|---|---|---|---|---|

| HH-10 | 재료 데이터에 중복과 병합 어긋남이 있다(MaterialTwin 데이터 담당). | 출처 1534 와 3184 가 갈려 있다. 물성값 67907~67910(재료 254)이 겹친다. DB 전체에 같은 값 묶음이 약 1,100개다(서로 다른 출처 691 · 같은 출처 446). 10-18 app-data 병합에서 `correction_misses` 11 · `verdict_misses` 1 이 나왔다(10-17 아침 4건, 새 자연키 `28154f1` 로 처음 돈 병합). | 두 출처를 합치고 중복을 정리한다. 병합 요약 JSON 의 두 misses 를 보고 같은 측정이 두 행이 되었는지 확인한다. | 10-17 수정 목록 4절 · 10-18 제안 4-b |

| HH-11 | (선택) MaterialTwin 에 코어를 받는 코드가 없다. | 작업 폴더 · SIF 에 코어 받기 코드가 없다. 코어의 물성 이름표는 `material_property` 278 · `material_property_group` 53 · `material_property_domain` 12 건이다. 이름표의 정본은 MatNexus 다. | 물성 이름표에 물성 정의를 걸 일이 생기면 코어를 받는 코드를 붙인다. | 코어 요청 O-3 |



## 4. 질문 · 확인 요청



| # | 묻는 것 | 왜 | 출처 |

|---|---|---|---|

| HH-12 | HEAXHub 의 reconcile 과 scan 이 같은 앱을 동시에 띄울 수 있는가. | 데모 앱 실패 뒤 앱 없는 인스턴스 셸이 남는다(HH-06). 겹쳐 띄우는 길이 있는지 알아야 원인을 가른다. | 10-25 피드백 HX-2 |

| HH-13 | MaterialTwinWeb upstream 최신판이 cae00 사본 `6e45b40`(2026-09-05)과 같은가. | HH-01 의 줄 번호는 사본 기준이다. 최신판에서 이미 고쳤는지 우리는 확인하지 못했다. | 지식카드 K3 미확인 항목 |

| HH-15 | HH-14 의 매니페스트 넷을 당기는 박스가 cae00 말고도 있는가. | 그 박스에는 16110 · 16120 · 16121 이 없어 네 앱이 끊긴다. 있으면 박스 덮어쓰기 장치(`.portal/manifest.local.yaml` 같은 것)를 먼저 넣어야 한다. | HWAXProd `CUTOVER.md` 7절 |

| HH-16 | step_forge 를 service 에서 proxy 로 바꾼 뒤 허브를 다시 띄우면 도는 서비스 인스턴스(`heax_app_step_forge`, :9115)를 내리는가, 남기는가. | 전환 창에서 우리가 먼저 멈추지만, 남는다면 update-all 마다 다시 뜨는지 알아야 한다. | HWAXProd `CUTOVER.md` 5장 B-1 |



## 5. 결정 필요 (사용자)



없음. HH-01 의 효과 확인은 AI 데이터 허브 정기 동기화를 다시 돌릴지에 걸린다. 그 결정은 AIDataHub 요청서(10-AIDataHub.md AD-17)에 있다.



## 6. 다른 플랫폼과 맞물린 것



| 맞물린 것 | 이 플랫폼 몫 | 다른 쪽 몫 (같은 폴더의 요청서) | 출처 |

|---|---|---|---|

| 재료 카드 재반입 | HH-01 — 절로 나눠 내보낸다. | AIDataHub 요청서(10-AIDataHub.md AD-11 · AD-17) — materialtwin 규칙(`sections_field: sections`, `57397d9`)은 반영됐다. 6시간 동기화가 이미 있는 레코드의 `content` 를 덮어쓰는지(AD-11)와 동기화 재개(AD-17)를 그쪽에서 정한다. 정기 동기화가 10-19~10-26 주소 가드에 막혀 재반입 확인은 그 결정에 걸린다. | 지식카드 K3 요청 9 · K6-4 요청 0 · 10-25 피드백 AIDH-2 |

| MaterialTwin 주소 | HH-09(선택). | StepForge 요청서(05-StepForge.md ST-07 · ST-11) — 기본값을 허브 프록시로 유도하고 `field_check` 가 닿지 않음을 알린다. 둘 중 하나만. 우리 몫 문서(90-OURS-box-todo.md OB-82) — 박스는 `HEAXHub/.env` 의 `APPTAINERENV_STEPFORGE_MATERIALTWIN_URL` 로 우회 중이다. | 10-18 제안 S-1 · 4절 |

| SIF 설치 무결성 | HH-02 · HH-04. | 리스크 앱 요청서(07-hwax-risk.md) · StepForge 요청서(05-StepForge.md) — 할 일 없음. 둘 다 이 문제로 대조 없이 깔린 적이 있다. | update-all 안전 U7 · U8 · 10-17 수정 목록 X-6 |

| 운영 전환 | HH-14 — 매니페스트 넷, 전환 창 바로 앞에 합친다. | HWAXMcpGateway — 추적 변경 없음(심의 DynaForge 위임 · REST 다리는 박스 `provision.env` 의 `KOORM_BASE`, 우리 몫). 리스크 앱 · 에이전트 서버 · 포털 — 할 일 없음(키 · 도구 이름 그대로). 우리 몫 — 데이터 합치기 · 새 판 · 스냅숏(HWAXProd `CUTOVER.md`). | HWAXProd `CUTOVER.md`(10-31) |

| 리스크 앱 `knobs.env` | HH-05. | 리스크 앱 요청서(07-hwax-risk.md) — 할 일 없음. 우리 몫 문서(90-OURS-box-todo.md OB-77) — 박스는 `knobs.env`(`HWAXRISK_REVIEW_ENABLED=0`)를 운영 값으로 먼저 두었다. | update-all 안전 U9 |

| 리스크 앱 백업 알림 | 없음. | 리스크 앱 요청서(07-hwax-risk.md) RK-07 — 알림을 끄는 방법을 문서화한다. 백업 키를 넣을지는 우리 몫 결정이다(90-OURS-box-todo.md OB-17). | 10-25 피드백 HX-4 · 10-17 수정 목록 D5 |

| heax 게이트웨이 PAT | 없음(알림). 발급이 실패하면 heax 앱 9개(리스크 · StepForge · DynaForge · 재료)가 챗에서 한꺼번에 빠진다. | HWAXMcpGateway 요청서(02-HWAXMcpGateway.md HG-08) — `provision-config.sh:155` 가 같은 이름 PAT 를 먼저 지운다. 새것을 받은 뒤에 지우게 한다. 우리 몫 문서(90-OURS-box-todo.md OB-76) — 박스는 `provision.env` 에 `HEAX_MCP_TOKEN` 을 고정해 두었다. | update-all 안전 U6-1 |

| DocForge 코어 받기 | HH-03. | Datahub-platform 요청서(12-Datahub-platform.md) — 이 건으로 허브에 보내는 요청은 없다. 공개 계약은 기준 주소 `http://<hwax-batch>:3030/rootdesign/api` 와 `Authorization: Bearer <core:read 토큰>` 이다. | 코어 요청 1절 · D-1 |

| 물성 이름표 | HH-11(선택). | FMEA · MatNexus 요청서(13-FMEA-MatNexus.md) — 할 일 없음. 이름표의 정본은 MatNexus 다. | 코어 요청 O-3 |

| 데모 앱 | HH-06 · HH-08. | 우리 몫 문서(90-OURS-box-todo.md OB-37) — cae00 에서 데모 두 개(heax_demo_nextjs · mkdocs-static)를 내린다. | 10-25 피드백 HX-2 · HX-3 |



## 7. 다시 볼 시험



| 시험 | 언제 | 통과 기준 |

|---|---|---|

| 재료 카드 끝까지 보기 | HH-01 이 들어오고 materialtwin 동기화가 한 번 돈 뒤. | AIDataHub 원본에 「본문 절단」 표지가 없다. cae00 `~/kc-check/kc_sweep.py` 로 허브 목록 앞 300장 중 표지 52장이 0장이 되고, 절이 여러 개인 카드가 생긴다. |

| SIF 교체 | HH-02 가 들어온 뒤 첫 HEAX 반입. | 반입 중에도 도는 앱이 404 를 내지 않는다. 설치에 실패하면 옛 SIF 가 그대로 있고 rc 2 로 끝난다. |

| 체크섬 목록 | HH-04 가 들어온 뒤 첫 부분 배포. | latest 의 `SHA256SUMS` 에 latest 의 SIF 가 모두 있고, 받는 쪽 `sha256sum -c` 가 리스크 앱 · StepForge SIF 를 대조한다. |

| 운영 전환 | HH-14 를 전환 창에서 당기고 허브를 다시 띄운 뒤. | 게이트웨이 `heaxkooremapper_mcp_system_status` 가 운영 노드 API(19700)를 답하고, `heaxstep_forge_system_capabilities` 의 판이 운영 판이다. 리스크 심사 캡처 1건 · 심의 DynaForge 위임 1건 · 포털 STEP 업로드 1건 · 웹 타일 둘이 열린다. |

| app-data | HH-05 가 들어온 뒤. | Drive app-data 에 `knobs.env` 가 없고, 받는 쪽이 심지 않는다. |

| 헬스 · 데모 로그 | HH-06 · HH-07 이 들어온 뒤 update-all. | `/apps/<id>/apps/<id>/health` 404 가 더 쌓이지 않는다. 데모를 둔다면 `var/logs/integration_heax_demo_nextjs.log` 의 'launch failed' 가 늘지 않는다. |



## 8. 이 문서에서 뺀 것



- 10-17 수정 목록 3-5절의 앞선 HEAXHub 항목 넷 — 10-18 반입(`4e1954d..231363b`)으로 반영됐다.

- 데모 앱 되풀이 실패 지연 · 통합 로그 덧붙임 · 회전 — `9e670de` · `36a8b1d` 로 반영됐다. pnpm 몫만 HH-06 에 남겼다.

- app-data 받는 쪽 `*.lock` 건너뛰기 — `cf6d5f3` 으로 반영됐다. `knobs.env` 몫만 HH-05 에 남겼다.

- 재료 카드 이어 읽기 · 「다음 부분 보기」 · AIDataHub materialtwin 규칙 — 에이전트 · 포털 · AIDataHub 에서 반영됐다(`ae73f44` · `520aadf` · `2e51b95` · `57397d9`).

- MaterialTwin 도구 이름이 MatNexus 와 겹치는 일(`get_material` · `how_to_measure` · `measurement_gaps`) — 게이트웨이에서 반영됐다(FMEA 연결 GW19).

- StepForge 코어 주소 · 토큰 · 프록시 예외 · 재기동 — 10-27 에 cae00 · 운영 모두 박스에서 끝났다(plm_hub.configured=true · last_sync ok).

- 운영 StepForge 를 새 판으로 올리는 일(운영 `424a022`, cae00 `b8481c4`) — 우리 릴리스 일이다(90-OURS-box-todo.md OB-46).

- StepForge 앱 항목(part_history 스키마 · MaterialTwin 기본 주소 · 실행기 계약) — StepForge 요청서(05-StepForge.md)로 보냈다.

- `HEAXHub/.env` 권한 600 · MaterialTwin 주소 우회 — 10-18 에 박스에서 했다. 우회를 걷는 때는 90-OURS-box-todo.md OB-82 다.

- cae00 데모 두 개 내리기 · heax 게이트웨이 PAT 고정 — 우리 몫이다(90-OURS-box-todo.md OB-37 · OB-76).

<!-- 2편 원문 끝 -->
