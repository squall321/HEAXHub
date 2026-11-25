<!-- cae00 요청 모음(request-1125) 중 HEAXHub 몫 구현 체크리스트 -->
# request-1125 — HEAXHub 몫 체크리스트

원문: `HWAXPortal/docs/request-1125/request.md` 1-7 절(HH-18 · IR-24 · SE-8) · 1-12 절(HP-71), 계획은 같은 폴더 `PLAN.md` §3-3.
보낸 곳: 포털 hwaxportal-71(2026-11-25). 결정과 까닭은 같은 폴더 `context-notes.md`.

- [ ] **보류(5차 cae00 라운드, 2026-11-24~)** — origin/main push · Drive 올리기 금지, 로컬 커밋까지만. 해제는 hwaxportal-87 의
      cae00 수신 알림, push 는 이 창 사용자 확인 뒤.

## 1. HH-18 — 자동 기동이 허브 기동 때의 옛 .env 값을 쓴다
- [x] `host_env.current()` — 허브 프로세스 env 위에 `$ROOT/.env` 를 새로 읽어 얹는다(python-dotenv). .env 에 없는 `APPTAINERENV_*` 는 뺀다.
- [x] 런처 호스트 경로 · SIF 경로(`_inherited_env` · 검색 정책 덮기)가 그 env 를 쓴다.
- [x] `apt_runner.instance_start` · `instance_exec` 의 하위 프로세스 env 가 그 env 를 쓴다.
- [x] 시험 — 따옴표 · export · 빈 값, 지운 APPTAINERENV_* 빠짐, 셸에서 온 프록시 유지, .env 없으면 그대로, 런처 · apt_runner 가 새 값을 쓴다.
- [x] conftest — 시험이 이 박스의 실제 .env 를 읽지 않게 막는다.

## 2. HP-71 — .env.example 에 코어 주소(포털 경로 꼴) · NO_PROXY 포털 도메인
- [x] 「앱에 넘기는 환경」 절에 두 줄. 값은 따옴표로 감싼다(start.sh 가 bash 로 source — `<…>` 가 리다이렉션으로 읽힌다).
      주석 줄로 두었고, 주석을 풀어도 bash source · python-dotenv 둘 다 같은 값으로 읽는 것을 확인했다. NO_PROXY 줄은 원래 없어 새로 넣었다.

## 3. SE-8 — Drive 받기 스크립트의 반복 줄이기
- [x] appdata-merge-from-drive.sh — tar 해시가 같으면 받기 · 백업 사본 · 병합을 건너뛴다, 백업 사본은 DB 마다 최근 N개.
- [x] models-from-drive.sh — 앱별 MANIFEST 비교로 같으면 건너뛴다, 비교는 --fast-list · --stats-one-line.
- [x] dist-from-drive.sh — sha256sum -c 는 옮긴 파일만(전체는 주 1회), frontend-dist 는 바뀐 때만, --stats-one-line.
- [x] 시험 — appdata 는 리포 시험 2개 추가(같은 tar 건너뛰기 · 사본 N개). dist · models 는 가짜 rclone 으로 가짜 루트에서 실측
      (dist 5경우 · models 4경우, context-notes 참고). 빌드 틀(.def)은 건드리지 않았다.

## 4. IR-24 — doc-forge 등록 사본에서 DOCFORGE_MAX_JOBS 빼기
- [x] ~~HEAXHub 몫~~ — 11-25 포털이 담당을 DocForge 배치로 옮겼다(DocForge docs/DEPLOY.md §7 의 2, release 를 옮긴 바로 뒤
      다음 스캔 전에 그 파일 하나만 커밋). **이쪽은 `integrations/doc-forge/.portal/manifest.yaml` 을 건드리지 않는다.**

## 마무리
- [ ] 백엔드 시험 통과(변경 전 실패 묶음과 비교) → 항목별 로컬 커밋 → hwaxportal-71 에 커밋 해시.
