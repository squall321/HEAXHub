# AI 자동화 프로그램 통합 포탈 운영 표준안

## 1. 배경

AI를 활용해 자동화 프로그램을 만들다 보면 Python, JavaScript, C++, Shell, Windows GUI, 웹앱, Electron 앱, 해석 프로그램, 배치 스크립트 등 다양한 기술 스택이 섞이게 된다.

이때 중요한 것은 모든 프로그램을 하나의 기술로 통일하는 것이 아니다.  
핵심은 **등록 방식, 실행 방식, 입력/출력 구조, 이력 관리, 권한 관리, 결과 회수 방식**을 표준화하는 것이다.

즉, 각 프로그램은 자유롭게 만들되, 포탈에 등록될 때는 동일한 규칙을 따르게 하는 것이 보편적이고 확장성 있는 방법이다.

---

## 2. 기본 원칙

### 2.1 기술 스택은 자유롭게 둔다

자동화 프로그램은 다음과 같이 다양한 형태일 수 있다.

- Python CLI 프로그램
- Node.js / JavaScript 프로그램
- React + Flask/FastAPI 웹앱
- C++ 실행 파일
- Windows GUI 앱
- PyQt / Qt / WPF / WinForms 앱
- Electron 앱
- Shell / Batch 스크립트
- Apptainer / Docker 기반 컨테이너 앱
- Slurm 기반 해석 Job
- 외부 URL 기반 사내 웹서비스

이들을 억지로 하나의 기술로 재작성하지 않는다.

### 2.2 포탈은 통합 실행기가 아니라 통합 관리 허브다

포탈의 역할은 모든 앱을 직접 내부에 포함하는 것이 아니라 다음을 공통 관리하는 것이다.

- 앱 등록
- 앱 설명
- 담당자
- 실행 방식
- 권한
- 입력/출력
- 버전
- 실행 이력
- 로그
- 결과 파일
- 문서
- 상태

---

## 3. 전체 아키텍처

데이터를 불러오고 관리하는 중앙 허브는 Linux 서버에 두는 것이 자연스럽다.

```text
사용자 브라우저
   ↓
Linux Portal Server
   ├─ 웹 포탈
   ├─ App Registry DB
   ├─ 파일 저장소 / NAS / MinIO
   ├─ 실행 이력 DB
   ├─ Job Runner
   ├─ Slurm / Apptainer 연동
   ├─ 웹앱 링크 또는 iframe 관리
   └─ Windows 앱 실행 요청 관리
          ↓
      Windows Worker / Windows App Server
          ↓
      Windows GUI 앱 / Windows 전용 EXE 실행
          ↓
      결과 파일을 Linux Hub로 반환
```

---

## 4. Linux Hub Server의 역할

Linux 허브 서버는 전체 시스템의 중심이다.

```text
Linux Hub Server
 ├─ Portal Web Server
 ├─ Backend API
 ├─ App Registry Database
 ├─ Job History Database
 ├─ File Storage
 ├─ Job Runner
 ├─ Slurm Integration
 ├─ Apptainer Integration
 ├─ Authentication / Authorization
 └─ Windows Worker 관리
```

Linux 허브는 다음 기능을 담당한다.

- 앱 목록 관리
- 앱별 manifest 관리
- 실행 요청 생성
- job_id 발급
- 입력 파일 저장
- 실행 상태 조회
- 로그 수집
- 결과 파일 저장
- 결과 다운로드
- 사용자 권한 관리
- 실행 이력 관리

---

## 5. 앱 유형 분류

포탈에 등록되는 앱은 `app_type`으로 분류한다.

```text
app_type:
- cli_tool
- web_app
- windows_gui
- remote_app
- external_link
- slurm_job
- container_app
```

### 5.1 CLI Tool

예시:

- Python script
- C++ executable
- Shell script
- Batch file

실행 방식:

```text
./run.sh input/ output/ params.json
```

### 5.2 Web App

예시:

- Flask 앱
- FastAPI 앱
- React + Backend 앱
- Node.js 서비스

실행 방식:

```text
URL로 열기
iframe으로 삽입
새 탭으로 열기
```

### 5.3 Windows GUI App

예시:

- PyQt exe
- WPF 앱
- WinForms 앱
- Qt C++ 앱
- Electron 앱

실행 방식:

```text
로컬 PC 실행
Windows Server 원격 실행
Windows Worker Agent 실행
RemoteApp / RDP / VNC 실행
```

### 5.4 Remote App

예시:

- Windows App Server
- Jupyter Server
- Grafana
- Remote CAE Tool

### 5.5 External Link App

예시:

- 기존 사내 시스템
- 문서 시스템
- 위키
- 파일 서버
- 대시보드 URL

---

## 6. 실행 대상 분류

앱이 어디서 실행되는지를 `execution_target`으로 명시한다.

```text
execution_target:
- linux_runner
- slurm
- apptainer
- windows_worker
- external_url
- local_pc
```

### 6.1 linux_runner

Linux 서버에서 직접 실행 가능한 프로그램이다.

예:

```text
python main.py
node index.js
./solver
./run.sh
```

### 6.2 slurm

HPC 클러스터에 job을 제출하는 방식이다.

예:

```text
sbatch job.sh
squeue
sacct
```

### 6.3 apptainer

컨테이너로 실행하는 방식이다.

예:

```text
apptainer exec app.sif ./run.sh input output params.json
```

### 6.4 windows_worker

Windows 전용 GUI 또는 EXE를 Windows Agent가 실행한다.

```text
Linux Portal
   ↓
Windows Worker Agent
   ↓
Windows EXE 실행
   ↓
결과 생성
   ↓
Linux Portal로 업로드
```

### 6.5 external_url

이미 별도 서버에서 운영 중인 웹앱 또는 서비스다.

### 6.6 local_pc

사용자 PC에 설치된 앱을 실행하는 방식이다.

예:

```text
custom-protocol://open?job_id=1234
```

---

## 7. Manifest 기반 등록 규칙

모든 앱은 `manifest.yaml` 또는 `tool.json`을 가진다.

### 7.1 기본 예시

```yaml
id: lsdyna_kfile_checker
name: LS-DYNA K File Checker
version: 1.2.0
owner: CAE Automation Part
status: stable
app_type: cli_tool
execution_target: linux_runner

description: >
  LS-DYNA k 파일의 part, contact, material, timestep 위험 요소를 검사한다.

launch:
  mode: job_runner
  command: ./run.sh input output params.json

inputs:
  - name: k_file
    type: file
    required: true
    extensions: [".k", ".key"]

  - name: check_contact
    type: boolean
    default: true

outputs:
  - name: report_html
    type: file
    path: output/report.html

  - name: summary_json
    type: file
    path: output/result.json

permissions:
  visibility: team
  executable_by: ["cae_engineer", "admin"]

resources:
  cpu: 4
  memory_gb: 8
  gpu: false

tags:
  - lsdyna
  - preprocessor
  - validation
```

---

## 8. Windows GUI 앱 등록 예시

Windows GUI 앱은 Linux 서버에서 직접 실행하지 않고 Windows Worker 또는 사용자 PC에서 실행하도록 등록한다.

```yaml
id: koo_mesh_modifier
name: Koo Mesh Modifier
version: 1.4.2
owner: CAE Automation Part
status: beta
app_type: windows_gui
execution_target: windows_worker

description: >
  Windows GUI 기반 mesh modifier 프로그램.
  입력 k 파일을 수정하고 결과 파일과 리포트를 생성한다.

launch:
  mode: remote_agent
  agent_pool: windows-cae-tools
  command: KooMeshModifier.exe --job-dir "{job_dir}"

requirements:
  os: windows
  gpu: false
  license: internal

inputs:
  - name: k_file
    type: file
    required: true
    extensions: [".k", ".key"]

outputs:
  - name: modified_k_file
    type: file
    path: output/model_modified.k

  - name: report
    type: file
    path: output/report.html

  - name: result_json
    type: file
    path: result.json
```

---

## 9. 웹앱 등록 예시

```yaml
id: drop_dashboard
name: Drop Simulation Dashboard
version: 2.0.0
owner: CAE Automation Part
status: stable
app_type: web_app
execution_target: external_url

description: >
  낙하 해석 결과를 시각화하고 조건별 결과를 비교하는 웹 대시보드.

launch:
  mode: url
  url: https://internal-server/drop-dashboard
  open_in: new_tab
  auth_mode: sso

tags:
  - drop
  - dashboard
  - simulation
```

---

## 10. CLI / 컨테이너 앱 등록 예시

```yaml
id: drop_angle_generator
name: Drop Angle Generator
version: 1.0.0
owner: CAE Automation Part
status: stable
app_type: container_app
execution_target: apptainer

launch:
  mode: job_runner
  runtime: apptainer
  image: drop-angle-generator.sif
  command: apptainer exec drop-angle-generator.sif ./run.sh input output params.json

inputs:
  - name: base_k_file
    type: file
    required: true
    extensions: [".k", ".key"]

  - name: angle_count
    type: number
    default: 100

outputs:
  - name: generated_cases
    type: folder
    path: output/cases

  - name: summary
    type: file
    path: output/result.json
```

---

## 11. 표준 실행 인터페이스

가능한 모든 CLI/서버 실행 앱은 다음 형식을 따른다.

```text
./run.sh input/ output/ params.json
```

예:

```bash
#!/bin/bash

python src/main.py   --input-dir "$1"   --output-dir "$2"   --params "$3"
```

포탈은 내부 구현 언어를 알 필요 없이 `run.sh`만 호출한다.

---

## 12. 표준 Job 폴더 구조

모든 실행은 `job_id` 기준으로 관리한다.

```text
/storage/automation_jobs/
 ├─ job_20260527_0001/
 │   ├─ input/
 │   │   └─ uploaded_file.k
 │   ├─ work/
 │   │   └─ intermediate files
 │   ├─ output/
 │   │   ├─ result.json
 │   │   ├─ report.html
 │   │   ├─ plots/
 │   │   └─ output.zip
 │   ├─ logs/
 │   │   ├─ stdout.log
 │   │   └─ stderr.log
 │   └─ params.json
```

---

## 13. 표준 입력 구조

입력은 다음 세 가지로 구분한다.

```text
input/
 ├─ 업로드 파일
 ├─ 참조 파일
 └─ 옵션 파일

params.json
 └─ 사용자가 입력한 실행 파라미터
```

예:

```json
{
  "check_contact": true,
  "check_material": true,
  "risk_threshold": 0.8
}
```

---

## 14. 표준 출력 구조

모든 앱은 가능하면 다음 파일을 생성한다.

```text
output/
 ├─ result.json
 ├─ report.html
 ├─ output.zip
 ├─ plots/
 └─ generated_files/
```

### 14.1 result.json 예시

```json
{
  "job_id": "20260527_0001",
  "app_id": "koo_mesh_modifier",
  "status": "success",
  "summary": {
    "num_parts": 152,
    "num_contacts": 37,
    "risk_level": "medium"
  },
  "input_files": [
    "model.k"
  ],
  "outputs": {
    "modified_k": "output/model_modified.k",
    "report": "output/report.html",
    "zip": "output/output.zip"
  },
  "warnings": [
    "Part 103 has very small elements.",
    "Contact 12 uses high stiffness scale."
  ],
  "errors": []
}
```

---

## 15. 로그 관리 규칙

모든 앱은 실행 로그를 남긴다.

```text
logs/
 ├─ stdout.log
 ├─ stderr.log
 ├─ app.log
 └─ runner.log
```

로그에는 다음 정보가 포함되어야 한다.

- 실행 시작 시간
- 실행 종료 시간
- 실행 명령
- 실행 사용자
- 앱 버전
- 에러 메시지
- 경고 메시지
- 주요 처리 단계
- 결과 파일 경로

---

## 16. 실행 이력 관리

각 job마다 다음 정보를 저장한다.

```text
job_id
app_id
app_version
git_commit_hash
executed_by
executed_at
finished_at
execution_target
input_files
params
status
result_path
stdout_path
stderr_path
runtime_environment
```

특히 CAE/해석 자동화에서는 다음 정보가 중요하다.

- 어떤 입력 파일을 사용했는지
- 어떤 파라미터로 실행했는지
- 어떤 버전의 프로그램을 사용했는지
- 어떤 서버/컨테이너/Slurm 환경에서 실행했는지
- 결과가 어디에 저장되었는지

---

## 17. 권한 관리

포탈에서는 사용자 역할을 구분한다.

```text
Admin
- 앱 등록/삭제
- 권한 설정
- 전체 실행 이력 조회
- 시스템 설정

Owner
- 본인 앱 수정
- 버전 배포
- 실행 로그 확인
- 사용자 문의 대응

User
- 허용된 앱 실행
- 본인 실행 결과 조회
- 결과 다운로드

Viewer
- 앱 설명 조회
- 결과 조회
```

앱별 공개 범위도 구분한다.

```text
visibility:
- private
- team
- department
- company
```

---

## 18. 앱 상태 관리

앱은 생명주기 상태를 가진다.

```text
draft       : 개발 중
beta        : 일부 사용자 테스트 가능
stable      : 공식 사용 가능
deprecated  : 사용 비추천
archived    : 보관만 하고 실행 불가
```

포탈에서는 기본적으로 `stable` 앱을 우선 노출하고, `beta`, `deprecated`, `archived`는 필터로 확인하게 하는 것이 좋다.

---

## 19. Windows GUI 앱 운영 방식

Windows GUI 앱은 다음 방식 중 하나로 운영한다.

### 19.1 사용자 PC 로컬 실행

사용자 PC에 앱을 설치하고 포탈에서 실행 링크를 제공한다.

```yaml
launch:
  mode: local_protocol
  protocol: koomeshmodifier://open
  installer_url: /downloads/KooMeshModifierSetup.exe
```

예:

```text
koomeshmodifier://run?job_id=1234&input_file=model.k
```

장점:

- 사용자 PC 자원 사용
- 간단한 앱에 적합
- 기존 GUI 유지 가능

단점:

- 각 PC 설치 필요
- 버전 관리 어려움
- 결과 회수 자동화 필요

### 19.2 Windows Server 원격 실행

Windows 서버에 앱을 설치하고 RDP, RemoteApp, VNC, Guacamole 등으로 접속하게 한다.

```text
Portal
 → Windows App Server
 → RDP / RemoteApp / VNC
 → GUI 앱 실행
```

장점:

- 라이선스 및 설치 관리 용이
- 중앙 통제 가능
- 사용자 PC 환경 영향 감소

단점:

- 동시 접속 관리 필요
- GUI 자동화 한계
- 원격 세션 관리 필요

### 19.3 Windows Worker Agent 실행

Windows Agent가 Linux Portal의 job 요청을 받아 Windows 앱을 실행하고 결과를 반환한다.

```text
Linux Portal
   ↓ REST API / Queue
Windows Agent
   ↓
Windows GUI or EXE 실행
   ↓
결과 파일 생성
   ↓
Linux Portal로 업로드
```

장점:

- Linux Hub 중심의 통합 이력 관리 가능
- Windows 앱도 job처럼 관리 가능
- 결과 회수 자동화 가능

단점:

- Agent 개발 필요
- GUI 앱의 무인 실행 가능 여부 확인 필요

---

## 20. Windows Worker Agent 역할

Windows Agent는 다음을 수행한다.

```text
Windows Agent
 ├─ 실행 요청 수신
 ├─ job_id 확인
 ├─ 입력 파일 다운로드
 ├─ 작업 폴더 생성
 ├─ EXE 실행
 ├─ 로그 저장
 ├─ result.json 생성
 ├─ 결과 파일 압축
 └─ Linux Hub로 업로드
```

Windows Agent는 REST API 또는 Queue 방식으로 구현할 수 있다.

```text
REST Polling 방식:
Windows Agent가 주기적으로 Linux Portal에 pending job이 있는지 조회

Queue 방식:
Linux Portal이 Redis/RabbitMQ 등에 job을 넣고 Windows Agent가 consume

파일 감시 방식:
공유 폴더에 job 요청 파일을 두고 Windows Agent가 감시
```

---

## 21. 웹앱 운영 방식

웹기술로 만든 앱은 포탈에서 다음 방식으로 연결한다.

```text
1. 새 탭으로 열기
2. iframe으로 포탈 내부 삽입
3. SSO 기반 인증 통합
4. Reverse Proxy로 경로 통합
```

예:

```yaml
launch:
  mode: url
  url: https://internal-server/drop-dashboard
  open_in: iframe
  auth_mode: sso
```

단, iframe은 보안 정책에 따라 막힐 수 있다.

관련 설정:

```text
X-Frame-Options
Content-Security-Policy frame-ancestors
CORS
SameSite Cookie
SSO Session
```

안정성을 우선하면 새 탭 방식이 가장 쉽다.

---

## 22. Job Runner API 예시

포탈 백엔드는 다음 API를 제공할 수 있다.

```text
POST /api/apps/{app_id}/run
GET  /api/jobs/{job_id}/status
GET  /api/jobs/{job_id}/result
GET  /api/jobs/{job_id}/logs
GET  /api/jobs/{job_id}/files
POST /api/jobs/{job_id}/cancel
```

### 22.1 실행 요청 예시

```json
{
  "app_id": "lsdyna_kfile_checker",
  "params": {
    "check_contact": true,
    "check_material": true
  },
  "input_files": [
    "model.k"
  ]
}
```

### 22.2 상태 응답 예시

```json
{
  "job_id": "job_20260527_0001",
  "status": "running",
  "progress": 45,
  "message": "Checking contact definitions..."
}
```

---

## 23. 추천 DB 테이블 구조

### 23.1 apps

```text
id
name
description
owner
version
status
app_type
execution_target
manifest_path
created_at
updated_at
```

### 23.2 app_versions

```text
id
app_id
version
git_commit_hash
release_note
manifest_snapshot
created_at
created_by
```

### 23.3 jobs

```text
id
app_id
app_version
executed_by
status
execution_target
created_at
started_at
finished_at
input_path
output_path
log_path
params_json
result_json
```

### 23.4 permissions

```text
id
app_id
role
user_or_group
permission_type
```

---

## 24. 포탈 UI 구성

포탈 UI는 사용자가 내부 기술을 몰라도 사용할 수 있게 구성한다.

### 24.1 앱 목록 화면

표시 항목:

```text
앱 이름
설명
상태
유형
담당자
버전
태그
실행 버튼
문서 버튼
```

### 24.2 앱 상세 화면

표시 항목:

```text
앱 설명
입력 파라미터
출력 파일
실행 방법
최근 실행 이력
변경 이력
담당자
주의사항
권한
```

### 24.3 Job 상세 화면

표시 항목:

```text
실행 상태
실행 사용자
입력 파일
파라미터
로그
결과 요약
결과 파일 다운로드
HTML 리포트 보기
재실행 버튼
```

---

## 25. 점진적 도입 전략

처음부터 완전 통합을 목표로 하지 않는다.

### 25.1 1단계: App Registry Portal

먼저 앱 카탈로그를 만든다.

```text
앱 이름
설명
담당자
문서
상태
실행 링크
버전
```

효과:

- 자동화 프로그램이 어디 있는지 정리됨
- 담당자와 사용법이 명확해짐
- 중복 개발이 줄어듦

### 25.2 2단계: 공통 Job History

CLI, 서버 실행 앱부터 이력을 남긴다.

```text
누가
언제
무슨 앱을
어떤 입력으로
어떤 버전에서
실행했는지
```

### 25.3 3단계: 결과 회수 표준화

모든 앱이 `result.json`, `report.html`, `output.zip`을 남기도록 한다.

### 25.4 4단계: Windows GUI 앱 연동

Windows 앱은 우선 다운로드/원격실행으로 연결하고, 이후 Windows Agent를 붙인다.

### 25.5 5단계: 핵심 로직 엔진화

많이 쓰는 GUI 앱은 내부 로직을 분리한다.

```text
기존:
Windows GUI exe
 ├─ UI
 ├─ 파일 처리
 ├─ 계산 로직
 └─ 결과 저장

개선:
Core Engine
 ├─ CLI 실행 가능
 ├─ API 실행 가능
 └─ GUI와 Web이 모두 호출 가능
```

---

## 26. GUI 앱의 장기 리팩토링 방향

GUI 앱은 장기적으로 다음 구조로 분리하는 것이 좋다.

```text
core/
 ├─ parser.py
 ├─ solver.py
 ├─ exporter.py

gui/
 └─ pyqt_app.py

cli/
 └─ main.py

api/
 └─ server.py
```

이렇게 하면 기존 사용자는 GUI를 계속 쓰고, 포탈은 CLI/API를 통해 같은 기능을 실행할 수 있다.

---

## 27. 최소 표준 룰

초기에는 다음 10개 룰만 강제해도 충분하다.

```text
1. 모든 앱은 manifest.yaml을 가진다.
2. 모든 앱은 app_type을 가진다.
3. 모든 앱은 execution_target을 가진다.
4. 모든 앱은 owner, version, status를 가진다.
5. 가능한 앱은 run.sh input output params.json 구조를 따른다.
6. 모든 실행은 job_id 기준으로 관리한다.
7. 입력은 input/ 및 params.json으로 관리한다.
8. 출력은 output/result.json, report.html, output.zip으로 관리한다.
9. stdout/stderr 로그를 저장한다.
10. 실행 이력에 app_version, git_commit, 사용자, 시간, 파라미터를 저장한다.
```

---

## 28. 권장 기술 스택

사용자 환경을 고려하면 다음 구성이 적합하다.

```text
Frontend:
- React
- TypeScript
- Ant Design

Backend:
- Flask 또는 FastAPI

Database:
- PostgreSQL

Queue:
- Redis Queue
- Celery
- RabbitMQ

Storage:
- NAS
- NFS
- MinIO

Execution:
- Local Linux Runner
- Apptainer
- Slurm
- Windows Worker Agent

Authentication:
- 사내 SSO
- LDAP
- OAuth2 / OIDC
```

---

## 29. 최종 권장 구조

```text
React Portal
  ↓
Flask/FastAPI Backend
  ↓
App Registry DB
  ↓
Job Runner
  ↓
File Storage
  ↓
Execution Backend
    ├─ Local Linux Process
    ├─ Apptainer Container
    ├─ Slurm Cluster
    ├─ Windows Worker Agent
    ├─ External Web App
    └─ Local PC App
```

---

## 30. 핵심 결론

혼합 앱 환경에서는 하나의 기술로 다시 만드는 것이 정답이 아니다.

정답은 다음과 같다.

```text
앱의 구현 방식은 그대로 둔다.
대신 포탈에 등록되는 방식, 실행 방식, 결과 회수 방식, 이력 관리 방식을 표준화한다.
```

특히 Linux 서버를 중앙 허브로 두고 Windows GUI, 웹앱, CLI, Slurm, Apptainer 앱을 함께 관리하는 구조는 충분히 유효하다.

최종적으로는 다음 방향이 가장 현실적이다.

```text
1. Linux Hub를 중앙 포탈로 둔다.
2. 모든 앱은 manifest로 등록한다.
3. 앱마다 app_type과 execution_target을 명시한다.
4. 실행과 결과는 job_id 기준으로 관리한다.
5. Windows GUI 앱은 Windows Worker 또는 원격 실행으로 연결한다.
6. 자주 쓰는 GUI 앱은 core engine을 CLI/API로 분리한다.
7. 결과는 result.json, report.html, output.zip 형태로 포탈에 회수한다.
```

이렇게 하면 AI로 빠르게 만들어진 다양한 자동화 프로그램을 단순한 스크립트 더미가 아니라, 조직적으로 관리 가능한 **Automation Tool Registry / Internal App Portal**로 발전시킬 수 있다.
