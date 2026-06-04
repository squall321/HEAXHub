# Toolchain SIFs — 운영자 가이드

HEAXHub 의 integration builder 는 stack 별로 별도 toolchain SIF 안에서 빌드한다.
4 종 SIF 가 있고, 셋은 단일 언어용, 하나는 polyglot:

| key         | SIF 파일                              | 대략 크기 |
|-------------|---------------------------------------|-----------|
| nodejs20    | `heaxhub_toolchain_nodejs20.sif`      | ~250 MB   |
| python312   | `heaxhub_toolchain_python312.sif`     | ~150 MB   |
| go122       | `heaxhub_toolchain_go122.sif`         | ~250 MB   |
| polyglot    | `heaxhub_toolchain_polyglot.sif`      | ~1.5 GB   |

`*.def` 와 stack 별 상세는 `deploy/apptainer/toolchain_*.def`,
`deploy/apptainer/toolchain_*.README.md` 참고.

## 배치 위치 (HEAXHub 가 SIF 를 찾는 순서)

빌드한 SIF 는 **git 에 커밋하지 않는다** (.sif 는 .gitignore 대상).
대신 운영자가 둘 중 하나로 배치:

1. 전용 디렉터리를 정해서 환경변수로 알려주기 — 권장
   ```bash
   export HEAXHUB_TOOLCHAIN_SIF_DIR=/srv/heaxhub/toolchains
   # 그 디렉터리에 heaxhub_toolchain_*.sif 4개 배치
   ```
2. 기존 서비스 SIF 와 같은 디렉터리에 두기
   ```bash
   # deploy/apptainer/heaxhub_toolchain_*.sif
   # 또는 ~/serviceApptainers/heaxhub_toolchain_*.sif
   ```

탐색 순서 (`backend/app/config.py` 참고):
1. `$HEAXHUB_TOOLCHAIN_SIF_DIR/heaxhub_toolchain_<key>.sif`
2. `$SIF_DIR/heaxhub_toolchain_<key>.sif`
3. `deploy/apptainer/heaxhub_toolchain_<key>.sif`
4. `$HOME/serviceApptainers/heaxhub_toolchain_<key>.sif`

## 빌드 (온라인 staging 머신에서)

```bash
# 4종 모두
bash deploy/apptainer/build-toolchains.sh

# 한 개만
bash deploy/apptainer/build-toolchains.sh --only nodejs20

# 강제 재빌드
bash deploy/apptainer/build-toolchains.sh --force
```

빌드는 `apptainer build --force <sif> <def>` 로 수행되며, fakeroot/namespace 가
막혀 있으면 `sudo` 또는 `--remote` 가 필요할 수 있다. 실패 시 안내가 출력된다.

## 오프라인 타깃으로 이송

1. **번들에 포함하기** — `prepare_offline_bundle.sh --with-toolchains`
   ```bash
   bash scripts/prepare_offline_bundle.sh --with-toolchains
   ```
   `deploy/apptainer/heaxhub_toolchain_*.sif` 가 bundle 의 `sifs/` 에 같이 들어간다.
   기본값은 **미포함** — 4개 합쳐 약 2 GB 라서 매번 번들에 넣으면 비싸다.

2. **개별 scp** (이미 다른 SIF 가 설치된 타깃을 업데이트할 때)
   ```bash
   scp deploy/apptainer/heaxhub_toolchain_*.sif \
       user@offline-host:${HEAXHUB_TOOLCHAIN_SIF_DIR}/
   ```
   integration_builder 는 매 빌드마다 SIF 경로를 다시 확인하므로 워커 재시작 불필요.

## 디스크 예산

| SIF                                  | 빌드 후 | tar.gz 안에서 |
|--------------------------------------|---------|---------------|
| heaxhub_toolchain_nodejs20.sif       | ~250 MB | ~120 MB       |
| heaxhub_toolchain_python312.sif      | ~150 MB | ~70 MB        |
| heaxhub_toolchain_go122.sif          | ~250 MB | ~120 MB       |
| heaxhub_toolchain_polyglot.sif       | ~1.5 GB | ~700 MB       |
| **합계**                             | ~2.2 GB | ~1.0 GB       |

오프라인 타깃은 `HEAXHUB_TOOLCHAIN_SIF_DIR` 경로에 최소 **3 GB 여유 공간**을 확보할 것
(재빌드 시 임시 파일 포함).

## 점검

```bash
apptainer inspect deploy/apptainer/heaxhub_toolchain_nodejs20.sif
apptainer run-help deploy/apptainer/heaxhub_toolchain_polyglot.sif
```

`apptainer inspect --labels` 로 `org.heaxhub.toolchain` 라벨이 보이면 정상 빌드.
