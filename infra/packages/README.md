# 오프라인 패키지 캐시

`scripts/bootstrap-host.sh --offline` 또는 자동 감지가 OFFLINE 으로 결정되면
여기 디렉터리들이 우선 사용됩니다.

```
infra/packages/
├── deb/      # apt 가 설치할 .deb (apptainer, nodejs, postgresql-client, fuse2fs, ...)
├── npm/      # `npm install -g <tarball>` 용 pnpm-*.tgz
└── pip/      # `pip install --no-index --find-links` 용 *.whl
```

## .deb 수집 (온라인 머신에서)

```bash
# 1) 베이스 패키지
sudo apt-get update
mkdir -p infra/packages/deb && cd infra/packages/deb
sudo apt-get install -y --download-only --reinstall \
  git curl make build-essential ca-certificates gnupg lsb-release \
  python3.12 python3.12-venv python3-pip software-properties-common pipx \
  fuse2fs uidmap postgresql-client
sudo cp /var/cache/apt/archives/*.deb .
sudo chown -R "$USER":"$USER" .

# 2) 핀 apptainer .deb
curl -fL -O https://github.com/apptainer/apptainer/releases/download/v1.3.6/apptainer_1.3.6_amd64.deb

# 3) node 20 (옵션 1: NodeSource .deb)
#   미리 받은 nodejs_20.x_amd64.deb 를 넣어두기
# (옵션 2: 타르볼 — 더 안전)
curl -fL -O https://nodejs.org/dist/v20.18.1/node-v20.18.1-linux-x64.tar.xz
```

## pnpm 9 (npm/)

```bash
mkdir -p infra/packages/npm && cd infra/packages/npm
curl -fL -O https://registry.npmjs.org/pnpm/-/pnpm-9.15.0.tgz
```

## Python wheels (pip/)

`deploy/apptainer/bundle.sh --with-wheels` 가 자동으로 sidecar 를 만들지만,
수동으로 미리 받아두려면:

```bash
mkdir -p infra/packages/pip
cd backend && source .venv/bin/activate
pip freeze | sed -e '/^-e /d' > /tmp/req.txt
pip download -d ../infra/packages/pip -r /tmp/req.txt
```

## 타깃 서버 적용

```bash
# 번들과 함께 옮긴 후:
sudo bash scripts/bootstrap-host.sh --offline   # 또는 자동감지
bash deploy/apptainer/install_all.sh
```

`bootstrap-host.sh` 는 `find_cached_deb()` 헬퍼로 다음 위치를 모두 검색합니다:

1. `infra/packages/deb/`
2. `infra/deb/`
3. `infra/packages/`
4. 리포 루트
5. `$HEAXHUB_DEB_DIR` 환경변수가 가리키는 경로
