# cpp-cli 템플릿

HEAXHub에 등록할 **C++ CLI 도구** 의 기본 양식이다. 빌드는 **Apptainer (SIF)** 컨테이너 안에서 수행되며, 실행도 `apptainer exec` 으로 격리된다.

## 디렉터리 구조

```
cpp-cli/
├─ README.md
├─ CMakeLists.txt
├─ src/main.cpp
├─ .portal/
│   ├─ manifest.yaml      # app_type=cli_tool, execution_target=apptainer
│   ├─ Apptainer.def      # gcc:13 베이스 빌드 정의
│   └─ run.sh             # apptainer exec wrapper
└─ .gitignore
```

## 로컬 개발 흐름

```bash
# 1) 일반 빌드 (시스템 gcc 사용)
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j

./build/mytool --help

# 2) Apptainer 이미지 빌드
apptainer build app.sif .portal/Apptainer.def

# 3) 포탈과 동일한 진입점
APP_SIF=./app.sif bash .portal/run.sh /tmp/input /tmp/output /tmp/params.json
```

## 빌드 흐름 (HEAXHub)

1. `scripts/build_apptainer_sif.sh {app_id}` 가 호출되어
   `app_workspaces/{app_id}/upstream/.portal/Apptainer.def` 로부터 SIF 를 만든다.
2. 생성물은 `app_workspaces/{app_id}/sif/app.sif` 에 저장된다.
3. 실행 시 `run.sh` 가 환경변수 `APP_SIF` (LocalRunner / ApptainerRunner 가 주입) 을 사용해 컨테이너를 호출한다.

## params.json 사용

`run.sh` 는 `params.json` 의 `args` 배열을 컨테이너 안 `mytool` 의 추가 인자로 전달한다. 예:

```json
{
  "args": ["--mode", "fast", "--threshold", "0.5"]
}
```
