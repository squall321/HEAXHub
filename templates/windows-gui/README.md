# windows-gui 템플릿

HEAXHub에 등록할 **윈도우 GUI 응용** (예: HyperMesh, ANSA, 자체 EXE 등) 의 기본 양식이다.
실제 실행은 사내 **Windows Worker Agent** 에서 일어나고, 포탈은 신청·승인·메타데이터 관리·실행 트리거만 담당한다.

## 디렉터리 구조

```
windows-gui/
├─ README.md
├─ NOTES.md            # 3가지 운영 모드 설명
└─ .portal/
    ├─ manifest.yaml   # app_type=windows_gui, execution_target=windows_worker
    └─ run.sh          # 플레이스홀더 (포탈은 Agent 경유로 실행)
```

## 등록 흐름

1. 윈도우 PC 에 EXE / 설치 산출물이 준비되어 있어야 한다.
2. 사내 GitHub 에 윈도우 GUI 의 메타데이터 (이 템플릿 내용) 만 올린다.
3. HEAXHub `/submit` 에서 신청 → 운영자 승인 → `app_type=windows_gui` 로 등록.
4. 사용자가 카탈로그에서 실행하면 포탈이 **manifest 의 `launch.agent_pool`** 큐에
   작업을 적재하고, Windows Agent 가 받아서 실행한 뒤 결과를 회수한다.

## 운영자 체크리스트

- `launch.agent_pool` 이 사내 환경의 실제 큐 이름과 일치하는가
- `launch.protocol` 또는 `launch.installer_url` 이 채워져 있는가 (NOTES.md 의 3가지 모드 중 어느 쪽인지)
- `requirements.os: windows`, `requirements.license` (필요 시) 명시

## 자세한 운영 모드

[NOTES.md](NOTES.md) 참고.
