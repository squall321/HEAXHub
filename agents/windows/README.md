# HEAXHub Windows Worker Agent

사내 윈도우 PC 에서 HEAXHub 의 작업 큐를 폴링해 EXE 를 실행하고 결과를 회수하는 .NET 8 콘솔 / Windows Service 다. 단일 self-contained 실행파일로 배포한다.

## 빌드

`Windows PC` 또는 cross-publish 가 가능한 환경에서:

```powershell
cd agents\windows
dotnet publish -c Release -r win-x64 --self-contained=true `
  -p:PublishSingleFile=true -p:PublishTrimmed=false
```

산출물은 `bin\Release\net8.0\win-x64\publish\HeaxAgent.exe` 이다.

## 설치 (서비스)

1. 산출물 전체를 `C:\Program Files\HEAXHub\Agent\` 로 복사
2. 관리자 PowerShell 에서:

   ```powershell
   cd 'C:\Program Files\HEAXHub\Agent'
   .\install.ps1
   ```

3. 시스템 환경변수 등록 (한 번만):

   ```powershell
   [Environment]::SetEnvironmentVariable('HEAX_HUB_URL',     'https://hub.company.com', 'Machine')
   [Environment]::SetEnvironmentVariable('HEAX_AGENT_TOKEN', '<운영자가 1회 제공한 plaintext token>', 'Machine')
   [Environment]::SetEnvironmentVariable('HEAX_AGENT_POOL',  'windows-cae-tools', 'Machine')
   ```

4. 서비스 시작:

   ```powershell
   Start-Service HEAXHubAgent
   ```

5. 제거:

   ```powershell
   .\uninstall.ps1
   ```

## 설정 우선순위

`appsettings.json` < 환경변수 (`HEAX_*`)

| 키 | 환경변수 | 기본값 |
|---|---|---|
| Hub URL | `HEAX_HUB_URL` | `http://localhost:8000` |
| Token | `HEAX_AGENT_TOKEN` | (빈 값. 비어 있으면 동작 불가) |
| Pool | `HEAX_AGENT_POOL` | `default` |
| Poll interval (s) | `HEAX_AGENT_POLLINTERVALSECONDS` | 5 |
| Heartbeat interval (s) | `HEAX_AGENT_HEARTBEATINTERVALSECONDS` | 30 |

> 보안: 서비스로 동작할 때는 가능한 한 DPAPI (`ProtectedData`) 로 토큰을 봉인해서 별도 파일에 저장하는 방식도 권장한다. 현 버전은 시스템 환경변수에 평문 저장한다 (best-effort).

## 작동 순서

1. `HEAX_AGENT_TOKEN` 으로 `POST /api/v1/agents/heartbeat` 호출 → 등록 확인
2. 5초마다 `GET /api/v1/agents/poll?pool=...` 폴링
3. job 페이로드 수신 시:
   - `C:\ProgramData\HEAXHub\work\<job_id>\input` / `output` 생성
   - `params.json` 저장
   - 환경변수 `HEAX_APP_EXE_<APP_ID>` 또는 `HEAX_DEFAULT_EXE` 가 지정한 EXE 실행
   - stdout/stderr 를 2초 단위 배치로 `POST /agents/jobs/{job_id}/log`
   - 종료 시 `output` 디렉터리 ZIP + `result.json` 을 `POST /agents/jobs/{job_id}/files`
   - 결과 보고 `POST /agents/jobs/{job_id}/status`
4. 로그: `C:\ProgramData\HEAXHub\agent.log`

## 토큰 발급

운영자가 HEAXHub admin UI 에서 `POST /api/v1/admin/agents` 호출 시 응답에 평문 토큰이 한 번만 노출된다. 동일 토큰을 재발급할 수 없으므로 즉시 안전한 경로로 윈도우 PC 운영자에게 전달한다.

## 트러블슈팅

- **서비스 시작 직후 종료**: `HEAX_HUB_URL`/`HEAX_AGENT_TOKEN` 누락 가능. `C:\ProgramData\HEAXHub\agent.log` 확인.
- **401 Unauthorized**: 토큰 오타 또는 운영자가 agent 를 `disable` 한 상태.
- **EXE 가 못 뜸**: `HEAX_APP_EXE_<APP_ID>` 환경변수가 가리키는 경로가 존재하는지 확인.
