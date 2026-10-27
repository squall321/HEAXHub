<!-- cae00 운영 수정요청서(2026-10-27) 의 HEAXHub 담당 8항목 구현 체크리스트 -->
# cae00 운영 수정요청 — HEAXHub 담당 항목 체크리스트

원문: 2026-10-27 HWAX Portal 운영(cae00) 수정요청서. 이 저장소 담당 — HH-02, HH-04~HH-09, HH-12.
(HH-01·10·11·13 은 MaterialTwinWeb, HH-03 은 DocForge 몫 — 여기서 다루지 않음.)

전부 사전검토(파일:줄 실측)로 현재 HEAD(973444a 이후)에서 재현 확인됨. 순서는 의존관계 기준
(공유 패턴 먼저 → 그 패턴을 쓰는 항목).

- [x] HH-12 — `launch()` 앱별 잠금(reconcile 45초 · scan 5분 겹침 방지). 628400d 의 SIF 빌드
      flock 패턴 재사용. **다른 항목(HH-06)이 이 위에 쌓이므로 먼저.**
- [x] HH-06 — SIF 기동 실패 시(`_sif_fail` 경로 + 헬스 영구실패 경로) `apt_runner.instance_stop()`
      호출 — 인스턴스 셸 잔존 제거.
- [x] HH-07 — `_is_healthy` 프로브 순서를 prefix-aware 여부에 따라 조정 — 구조적으로 보장된 404
      시도를 없앤다(판정은 기존과 동일, 로그만 줄어듦).
- [x] HH-02 — `dist-from-drive.sh:40` `_install_if_changed` 를 `cp -p` 제자리 덮어쓰기 →
      temp 파일 + `mv -f` 로 교체(HWAXPortal `hwax_install_if_changed` 패턴).
- [ ] HH-04 — `dist-to-drive.sh` `SHA256SUMS` 를 부분배포 시 통째로 덮지 않고, 기존
      `latest/SHA256SUMS` 와 병합(이번 배포분만 갱신, 나머지 줄 보존).
- [ ] HH-05 — `appdata-to-drive.sh` 제외목록에 `knobs.env` 추가 + `appdata-merge-from-drive.sh`
      시드 단계에서 `knobs.env` 제외.
- [ ] HH-08 — `_notify_build_failed` 에 쿨다운 추가(기존 `_LAUNCH_FAIL_COOLDOWN_S` 패턴 재사용·공유).
- [ ] HH-09 — 형제 앱 주소를 `HEAX_APP_URL_<SLUG>` 로 각 앱의 `env_in_container` 에 주입.

각 항목: 수정 → 가능하면 실측(또는 기존 테스트) 검증 → 해당 항목만 커밋(의미단위, CLAUDE.md #9).
