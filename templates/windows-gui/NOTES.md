# Windows GUI 운영 모드

HEAXHub 운영 표준안 (PROJECT_PLAN.md §6 참조) 에서 windows_gui 앱은 다음 3가지 모드 중 하나로 동작한다. manifest 의 `launch` 블록을 모드에 맞게 채워 넣는다.

---

## 모드 1 — Remote App (서버에서 대리 실행)

- 사용자는 브라우저에서 카드 클릭
- 포탈이 Windows Agent 큐 (`agent_pool`) 에 작업 적재
- Agent 가 사내 Windows 서버에서 EXE 를 실행하고 결과를 회수
- 사용자에게는 결과 파일 / 스크린샷 만 전달, GUI 화면은 노출되지 않음

```yaml
app_type: windows_gui
execution_target: windows_worker
launch:
  mode: remote_agent
  agent_pool: windows-cae-tools
  runtime: windows_exe
```

**적합한 경우**: 백그라운드성 변환 / 후처리 도구, 사용자가 GUI 와 상호작용할 필요 없는 경우.

---

## 모드 2 — Local Protocol Launch (사용자 PC 에서 직접 실행)

- 카드 클릭 시 사용자의 윈도우 PC 에서 `heaxhub://launch?app=...` 같은 커스텀 프로토콜 핸들러가 호출
- 사용자 PC 에 사전 설치된 헬퍼 (또는 EXE 자체) 가 핸들러를 받아 실행
- 사용자는 평소처럼 GUI 를 직접 조작
- 입력/출력 파일은 사용자가 수동으로 관리 (또는 사내 공유폴더 경로 안내)

```yaml
app_type: windows_gui
execution_target: local_pc
launch:
  mode: local_protocol
  protocol: heaxhub-launch
  installer_url: https://hub.company.com/downloads/heaxhub-launcher-setup.exe
```

**적합한 경우**: HyperMesh, ANSA 등 사용자 인터랙션 중심 도구. 라이선스가 사용자 PC 에 고정된 경우.

---

## 모드 3 — External Link (메타데이터만 등록)

- 카탈로그에 카드로 표시되지만, 실행은 외부 시스템으로 위임
- "사용법" 탭에 설치/실행 안내를 적어두고, "열기" 버튼은 외부 URL 또는 사내 위키로 이동

```yaml
app_type: windows_gui          # 또는 external_link
execution_target: external_url
launch:
  mode: url
  url: https://wiki.company.com/cae/hypermesh-guide
  open_in: new_tab
```

**적합한 경우**: 사내 위키 / 매뉴얼 / 라이선스 정책상 포탈 통합이 불가한 도구.

---

## 모드 결정 가이드

| 질문 | 모드 1 | 모드 2 | 모드 3 |
|---|---|---|---|
| GUI 상호작용 필요? | 아니오 | 예 | 무관 |
| 결과 파일이 핵심? | 예 | 부분 | 아니오 |
| 라이선스가 서버에 있나? | 예 | 아니오 | 무관 |
| 자동 실행 가능? | 예 (CLI 모드 지원 시) | 아니오 | 무관 |

확신이 안 서면 운영자에게 신청 시점에 모드 1 로 신청하고, 운영자가 검토 후 조정한다.
