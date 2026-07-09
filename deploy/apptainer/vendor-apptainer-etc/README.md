<!-- apptainer 1.3.6 stock usr/etc/apptainer — 미러링으로 etc 빠진 서버 복구용 오프라인 폴백 -->
# vendor-apptainer-etc — apptainer 1.3.6 stock `usr/etc/apptainer`

핀 버전 apptainer 1.3.6 의 기본 런타임 설정(`usr/etc/apptainer/*`) 사본이다.

## 왜 레포에 두나
드라이브 미러링/부분추출로 `.tools/apptainer-<ver>/usr/etc/apptainer/` 가 통째로 빠진 서버(cae00)에서
`apptainer.conf`·`capability.json`·`ecl.toml`·`seccomp-profiles/` 등이 없어 모든 exec/instance 가
`no such file` → `starter exit 255` 로 즉사한다(→ pg/redis/caddy 전멸 → heal.sh "일부 컴포넌트 미복구").

`_common.sh` 의 `ensure_apptainer_runtime()` 가 복구 순서로 쓴다:
1. 캐시된 `.deb`(`deploy/apptainer/cache/apptainer_*.deb`) 재추출 — 있으면 이걸로 정확 복구.
2. 없으면 **이 디렉토리를 그대로 복사** — 다운로드 없이 오프라인 복구 보장.

`confgen` 은 `apptainer.conf` 하나만 만들 수 있어 부족하므로, 나머지 stock 파일을 여기 둔다.
apptainer 핀 버전을 올리면 이 트리도 새 버전의 `usr/etc/apptainer` 로 갱신할 것.
