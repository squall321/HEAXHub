# [최종 채택안 · 단일 진실] HWAX Agent — 트레이 상주형 모듈 배포/관리 에이전트 (v2)

> **이 문서가 HWAXAgent 의 단일 진실 (Source of Truth)** 입니다.
>
> 스택: **Tauri 2 (Rust core) + React 18 + TypeScript + Vite + Tailwind**.
> WinUI 3 / WPF / .NET 8 / C# / XAML 안은 **명시적으로 탈락**했습니다
> ([hwax-launcher-plan-winui3.md](hwax-launcher-plan-winui3.md) 참고 — 결정 이력 보존용).
> 1차 안 [hwax-launcher-plan.md](hwax-launcher-plan.md) 는 "패널 카탈로그" 방향이라
> v2 로 superseded 되었습니다. v2 와 충돌 시 v2 우선.

HEAXHub 자매 프로젝트. 개발실 내부 사용자가 윈도우 데스크탑에서 사내 CAE/전처리/플러그인 모듈을 **트레이 상주 에이전트**를 통해 받고, 검증하고, 실행하는 단일 진입점.

| 항목 | 내용 |
|---|---|
| 문서 버전 | v2 (Tauri 2 최종, 트레이 상주 에이전트 색깔로 재초점화) |
| 대상 OS | Windows 10 21H2+ / Windows 11 22H2+ (x64) · mac/Linux 가능성 부록 |
| 모기지 시스템 | HEAXHub (FastAPI :4040, Caddy :4180, React 18) |
| 추천 스택 | **Tauri 2 + React 18 + TypeScript + Rust (tracing, reqwest, sha2, zip)** |
| 배포 형식 | per-user `.msi` (Tauri bundler) + 자체 updater (Ed25519) |
| 권한 모델 | 기본 standard user (asInvoker), 관리자 권한 자동 요청 없음 |
| 한 줄 요약 | "HWAX Agent = Tauri 2 + React 트레이 상주형 모듈 배포/관리 에이전트" |
| 작성자 | HEAXHub Platform Team |

핵심 의사결정 한 줄: **"예쁜 윈도우 전용 앱"이 아니라 "백그라운드에서 안 깨지고 자동으로 모듈을 받아오는 에이전트"가 본질이다.** 1차 Tauri 안과 v2의 차이 한 줄: **카탈로그·MSI 풀스크린 UI 중심 → 트레이 상주 + 모듈 버전 매니저 중심.**

---

## ◇ 0. v2가 1차 안과 다른 점 (한눈에)

```
┌──────────────────────────────────────────────────────────────┐
│ v2 핵심 변화                                                  │
│                                                              │
│  1. "패널 카탈로그" 중심  →  "트레이 상주 + 모듈 버전 매니저"   │
│  2. 우선순위 6가지 명시 (개발속도/업데이트/안정성/             │
│      로그·복구/백신 오탐/확장성)                              │
│  3. 관리자 권한 회피를 1급 원칙으로 격상                       │
│  4. 모듈 다단 버전 + current.json 원자적 swap 패턴             │
│  5. HEAXHub `installer_packages` ↔ `programs.json` 스키마      │
│     명시적 매핑 표                                            │
│  6. 백신/EDR 오탐 회피 가이드 (allow-list + sha256 + 사인)     │
│  7. 피해야 할 안티 패턴 8개 체크리스트 화                      │
└──────────────────────────────────────────────────────────────┘
```

1차 안은 "윈도우 데스크탑 카탈로그 런처"였다. v2는 "**개발실 내부 자동화 배포/관리 에이전트**"다. UI 표면이 줄고 시스템 동작 표면이 늘어난다.

---

## ◇ 1. 사용자 결정 근거 — 6 우선순위 × 후보 스택

사용자가 직접 정리한 6 우선순위(보안팀 승인 용이성보다 위인 것들):

1. 빨리 만들 수 있는가
2. 업데이트/배포가 쉬운가
3. 사용자 PC에서 안 깨지는가
4. 로그/복구가 되는가
5. 백신/보안 솔루션에 오탐 안 나는가
6. 나중에 확장 가능한가 (mac/Linux 포함)

| 우선순위 | Tauri 2 | WPF | WinUI 3 |
|---|---|---|---|
| ① 개발 속도 | ★★★★★ (React/TS 친숙도) | ★★★ (XAML 학습) | ★★ (불안정 + 학습) |
| ② 업데이트/배포 | ★★★★★ (내장 updater + Ed25519) | ★★ (Squirrel/MSI 직접) | ★★ (MSIX 정책 마찰) |
| ③ 안 깨짐 | ★★★★ (Rust 메모리 안전) | ★★★★ (.NET 안정) | ★★ (런타임 이슈) |
| ④ 로그/복구 | ★★★★ (tracing, JSON) | ★★★★ (Serilog) | ★★★ |
| ⑤ 백신 오탐 | ★★★★ (단일 exe + 사인) | ★★★★ (단일 exe + 사인) | ★★ (MSIX 패키지 평판) |
| ⑥ 확장 (mac/Linux) | ★★★★★ | ★ | ★ |
| **종합 추천** | **85%** | 65% | 50% |

→ **Tauri 2 최종 결정.** WPF는 안정성/생태계는 강하나 ⑥ 확장 측면과 ① 개발 속도(React 자산 재사용 불가)에서 빠진다. WinUI 3는 ②/③ 양쪽 모두 현장 보고가 들쑥날쑥.

---

## ◇ 2. 핵심 시나리오 (5종)

### 시나리오 A: 트레이 부팅 → 첫 사용자 페어링

```
[유저가 HWAXAgentSetup.msi 더블클릭]
        │
        ▼
[%LocalAppData%\HWAXAgent\ 에 설치, 트레이 자동 실행 (per-user)]
        │
        ▼
[트레이 풍선: "HEAXHub와 페어링하세요"]
        │
        ▼ (트레이 메뉴 → 페어링)
[웹브라우저로 https://heaxhub/.../device 열림 → 6자리 코드 입력]
        │
        ▼
[HEAXHub: enrollment_token 발급 → Agent가 pair API 호출]
        │
        ▼
[device_jwt (90일) 발급 → Windows Credential Manager 저장]
        │
        ▼
[manifest 동기화 → "준비 완료" 토스트, 트레이 아이콘 녹색 dot]
```

페어링은 한 번만. 이후 모든 호출은 device JWT로.

### 시나리오 B: manifest 동기화 → 새 버전 감지 알림

```
[타이머: 매 30분 또는 트레이 "지금 동기화"]
        │
        ▼
[GET /api/v1/agents/manifest  ─ ETag/If-None-Match]
        │   ▲
        │   └─ 304: 변화 없음 → 트레이 툴팁 갱신
        ▼
[200: programs.json 수신 → cache/manifest.json 저장]
        │
        ▼
[로컬 modules/<id>/current.json 과 비교 → diff 계산]
        │
        ▼
[outdated 있음 → 트레이 토스트 "Koo Preprocessor 1.2.0 사용 가능"]
        │
        ▼
[auto_update=true 면 바로 다운로드 → 아니면 사용자 클릭 대기]
```

unreachable 시 마지막 캐시로 동작. 5회 연속 실패 → 트레이 아이콘 황색 dot.

### 시나리오 C: 모듈 다운로드 → sha256 검증 → swap → 실행

```
[유저가 트레이에서 "Koo Preprocessor 1.2.0 업데이트" 클릭]
        │
        ▼
[GET presigned URL → cache/downloads/koo_preprocessor-1.2.0.zip.partial]
        │
        ▼
[sha256 계산 → manifest sha256 비교 → 불일치면 즉시 삭제 + 실패 로그]
        │
        ▼
[zip slip 방어하며 modules/koo_preprocessor/1.2.0.staging/ 에 압축 해제]
        │
        ▼
[post_install_check 실행: "KooPreprocessor.exe --version" stdout 정규식 매칭]
        │
        ▼
[OK → rename 1.2.0.staging → 1.2.0]
        │
        ▼
[current.json 원자적 swap: {"version":"1.2.0","previous_version":"1.1.0"}]
        │
        ▼
[GC: 최근 3 버전만 유지, 나머지 디렉토리 삭제]
        │
        ▼
[트레이 토스트: "Koo Preprocessor 1.2.0 설치 완료. 실행하시겠습니까?"]
```

### 시나리오 D: 업데이트 실패 → 자동 롤백

```
[1.2.0 다운로드 OK → sha256 OK → 압축 해제 OK]
        │
        ▼
[post_install_check 실패 (예: --version 비정상 종료)]
        │
        ▼
[staging 디렉토리 rm_rf]
        │
        ▼
[current.json 손대지 않음 (1.1.0 그대로)]
        │
        ▼
[install-koo_preprocessor-1.2.0.log 에 stdout/stderr/exit code 기록]
        │
        ▼
[HEAXHub로 audit 이벤트 POST (실패 원인 + 환경)]
        │
        ▼
[트레이 토스트: "업데이트 실패. 이전 버전(1.1.0) 유지." + "자세히 보기"]
```

이미 swap 된 후 런타임 오류 발생 시: 사용자가 트레이에서 "이전 버전으로 롤백" 클릭 → current.json 의 previous_version 으로 복원.

### 시나리오 E: 트레이에서 로그 보기 + 서버 주소 변경

```
[트레이 우클릭 → "로그 폴더 열기"]
        │
        ▼
[Explorer 가 %LocalAppData%\HWAXAgent\logs\ 열림]
        │
        ▼
[유저: agent-2026-06-05.log, install-*.log, run-*.log 확인]

[트레이 우클릭 → "설정"]
        │
        ▼
[작은 패널 윈도우: 서버 주소(읽기전용), 자동 업데이트 토글,
 시작시 자동 실행 토글, 로그 레벨 선택, 진단 dump 만들기 버튼]
        │
        ▼
[서버 주소 변경은 "다시 페어링"을 통해서만 가능 (자유 입력 금지)]
```

자유 URL 입력을 막는 것이 백신 오탐 회피의 핵심 중 하나.

---

## ◇ 3. 시스템 아키텍처

```
HEAXHub Server                       User PC (Win10/11)
┌──────────────────────────┐         ┌────────────────────────────────────┐
│ FastAPI :4040            │         │ HWAX Agent Tray                    │
│  /api/v1/agents/enroll   │         │ (Tauri 2, single process, per-user)│
│  /api/v1/agents/manifest │ ◄─HTTPS─┤  ├─ React UI                       │
│  /api/v1/agents/installs │         │  │   (트레이 메뉴 / 작은 패널)      │
│  /api/v1/agents/audit    │         │  │                                 │
│  /api/v1/agents/heartbeat│         │  ├─ Rust core (Tauri command)      │
│  /api/v1/installers/...  │         │  │   - reqwest (HTTPS + JWT)       │
│  /ws/agent/{id} (옵션)   │         │  │   - sha2 (SHA-256)              │
│                          │         │  │   - zip (zip-rs)                │
│ Postgres                 │         │  │   - tracing (JSON 로그)         │
│  apps / installer_pkg    │         │  │   - keyring (Cred Mgr)          │
│  windows_agents          │         │  │   - tauri-plugin-updater        │
│  audit_log               │         │  │                                 │
│                          │         │  └─ Local store                    │
│ Object storage (S3-like) │         │      %LocalAppData%\HWAXAgent      │
│  installer zips          │         │       ├ modules\<id>\<ver>\        │
└──────────────────────────┘         │       ├ modules\<id>\current.json  │
                                     │       ├ cache\manifest.json        │
                                     │       ├ cache\downloads\*.partial  │
                                     │       ├ config.json                │
                                     │       ├ logs\*.log                 │
                                     │       └ .lock                      │
                                     └────────────────────────────────────┘
```

핵심 비대칭: HEAXHub 는 카탈로그/인증/스토리지/감사 로그를 책임지고, HWAX Agent 는 **트레이 + 다운로드 + 검증 + swap + 실행** 만 책임진다. 이게 v2의 색깔.

---

## ◇ 4. 트레이 UX 설계 (와이어프레임 ASCII)

메인 동선이 트레이 메뉴이고, 풀 윈도우는 보조다. 풀 윈도우를 안 띄우고도 모든 일상 동작이 가능해야 한다.

### 4.1 트레이 메뉴 (우클릭)

```
┌──────────────────────────────────┐
│ HWAX Agent · 1.0.0 · 동기화 30초 전│
├──────────────────────────────────┤
│ ▶ Koo Preprocessor   1.2.0  실행 │
│ ▶ Mesh Modifier      2.1.0  실행 │
│ ▶ NX Plugin          0.3.0  설치 │
├──────────────────────────────────┤
│   모두 업데이트 확인                │
│   지금 동기화                      │
│   로그 폴더 열기                   │
│   설정...                          │
├──────────────────────────────────┤
│   페어링 다시 하기                  │
│   종료                            │
└──────────────────────────────────┘
```

상태 표시(좌측 색 dot): 녹(정상) / 황(unreachable 등 경고) / 적(에러). 좌클릭은 패널 토글.

### 4.2 프로그램 목록 (작은 패널 윈도우, 480×640)

```
┌────────────────────────────────────────────────────┐
│  HWAX Agent                          [─][×]        │
├────────────────────────────────────────────────────┤
│ 검색  [______________]                              │
│                                                    │
│ ┌────────────────────────────────────────────────┐ │
│ │ ◆ Koo Preprocessor                  v1.2.0     │ │
│ │   설치됨 · 마지막 실행 1시간 전                  │ │
│ │                       [실행]  [상세]  [로그]    │ │
│ └────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────────┐ │
│ │ ◆ Mesh Modifier                     v2.1.0     │ │
│ │   설치됨 · 업데이트 가능 (2.2.0)                 │ │
│ │       [업데이트]  [실행]  [상세]  [로그]        │ │
│ └────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────────┐ │
│ │ ◆ NX Plugin                         v0.3.0     │ │
│ │   미설치                                        │ │
│ │                            [설치]  [상세]      │ │
│ └────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────┘
```

### 4.3 모듈 상세

```
┌────────────────────────────────────────────────────┐
│  ← Koo Preprocessor                  [×]           │
├────────────────────────────────────────────────────┤
│  현재 버전: 1.2.0  (설치 2026-06-04)                │
│  서버 최신: 1.2.0  (released 2026-06-04)            │
│  카테고리: preprocessor                             │
│  요구 권한: 사용자 (관리자 X)                       │
│                                                    │
│  변경 이력                                          │
│  ◇ 1.2.0  (현재)   2026-06-04                       │
│  ◇ 1.1.0          2026-05-20  [롤백]               │
│  ◇ 1.0.0          2026-04-12                       │
│                                                    │
│  [실행]  [로그 보기]  [폴더 열기]  [제거]            │
└────────────────────────────────────────────────────┘
```

### 4.4 설정

```
┌────────────────────────────────────────────────────┐
│  설정                                   [×]         │
├────────────────────────────────────────────────────┤
│  서버 주소 :  https://heaxhub.internal  (잠금)      │
│  Agent ID  :  ag_8e3b...                            │
│  토큰 만료 :  2026-09-03                            │
│                                                    │
│  [✓] 자동으로 업데이트 다운로드                     │
│  [✓] Windows 시작 시 자동 실행                      │
│  [ ] 익명 사용 통계 전송                            │
│                                                    │
│  로그 레벨  : ( ) trace ( ) debug (●) info ( ) warn │
│                                                    │
│  [진단 dump 만들기]  [다시 페어링]  [캐시 비우기]   │
└────────────────────────────────────────────────────┘
```

서버 주소는 자유 입력 금지(잠금) — 변경하려면 "다시 페어링"을 통해서만. 이게 백신/보안 정책 친화성을 결정한다.

---

## ◇ 5. 로컬 폴더 구조 상세

```
%LocalAppData%\HWAXAgent\
 ├─ modules\
 │   ├─ KooPreprocessor\
 │   │   ├─ 1.1.0\
 │   │   │   ├─ KooPreprocessor.exe
 │   │   │   ├─ resources\
 │   │   │   └─ .install_meta.json   ← {"sha256":"...","installed_at":"..."}
 │   │   ├─ 1.2.0\
 │   │   │   └─ KooPreprocessor.exe
 │   │   └─ current.json
 │   │        {
 │   │          "version": "1.2.0",
 │   │          "installed_at": "2026-06-04T13:22:11Z",
 │   │          "sha256": "abc...",
 │   │          "previous_version": "1.1.0"
 │   │        }
 │   ├─ MeshModifier\
 │   │   ├─ 2.1.0\KooMeshModifier.exe
 │   │   └─ current.json
 │   └─ NXPlugin\
 │       ├─ 0.3.0\
 │       │   ├─ plugin.dll
 │       │   └─ manifest.xml
 │       └─ current.json
 ├─ cache\
 │   ├─ manifest.json           ← 마지막 동기화된 서버 manifest 스냅샷 + ETag
 │   └─ downloads\              ← 다운로드 중 임시 zip (.partial 접미사)
 ├─ config.json
 │    {
 │      "server": "https://heaxhub.internal",
 │      "agent_id": "ag_8e3b...",
 │      "auto_update": true,
 │      "start_on_boot": true,
 │      "log_level": "info",
 │      "allowed_origins": ["https://heaxhub.internal"],
 │      "keep_last_n_versions": 3,
 │      "sync_interval_min": 30
 │    }
 ├─ logs\
 │   ├─ agent-2026-06-05.log
 │   ├─ install-KooPreprocessor-1.2.0.log
 │   └─ run-KooPreprocessor-2026-06-05T14-12-00.log
 └─ .lock                       ← 다중 실행 방지 (pid + flock)
```

device JWT 는 파일에 두지 않는다. Credential Manager 에 `HWAXAgent:device_jwt` 키로 저장.

---

## ◇ 6. 모듈 라이프사이클 State Machine

```
                ┌──────┐
                │ idle │ ◄────────────────────┐
                └──┬───┘                       │
                   │ tick / user click          │
                   ▼                            │
              ┌──────────┐                      │
              │ checking │                      │
              └────┬─────┘                      │
                   │                            │
       ┌───────────┼─────────────┐              │
       │ same      │ newer        │ missing     │
       ▼           ▼              ▼              │
 ┌──────────┐ ┌────────────┐ ┌────────────┐     │
 │installed │ │ outdated   │ │not_installed│    │
 └────┬─────┘ └─────┬──────┘ └─────┬──────┘     │
      │             │              │             │
      │ run         │ update       │ install     │
      ▼             ▼              ▼             │
 ┌──────────┐  ┌─────────────┐                  │
 │ running  │  │ downloading │                  │
 └────┬─────┘  └─────┬───────┘                  │
      │ exit         │                          │
      ▼              ▼                          │
 ┌──────────┐  ┌─────────────┐                  │
 │ stopped  │  │  verifying  │                  │
 └────┬─────┘  └─────┬───────┘                  │
      │              │ sha256 ok                │
      └──────────►   ▼                          │
                ┌─────────────┐                 │
                │ extracting  │                 │
                └─────┬───────┘                 │
                      ▼                          │
                ┌─────────────┐                 │
                │  swapping   │                 │
                │ (atomic)    │                 │
                └─────┬───────┘                 │
                      ▼                          │
                ┌─────────────┐                 │
                │  installed  │ ────────────────┘
                └─────────────┘
```

실패 분기:

```
verifying / extracting / post_install_check 실패
            │
            ▼
       ┌─────────┐      manual ?     ┌────────────┐
       │ failed  │ ─────────────────►│rolling_back│
       └─────────┘                   └─────┬──────┘
                                            ▼
                                     ┌────────────┐
                                     │ rolled_back│ → idle
                                     └────────────┘
```

11 상태 + 실패 상태 3개. 각 전이에는 로그 1줄과 (필요 시) 토스트.

---

## ◇ 7. 모듈 매니페스트 스키마 (programs.json)

서버 → 클라이언트로 내려오는 정식 스키마. 사용자가 제시한 단순형을 lifecycle/ui/visibility 까지 확장.

```json
{
  "schema_version": 1,
  "generated_at": "2026-06-05T10:00:00Z",
  "programs": [
    {
      "id": "koo_preprocessor",
      "name": "Koo Preprocessor",
      "description": "사내 전처리 도구. Abaqus/LS-DYNA 입력 카드 생성.",
      "category": "preprocessor",
      "version": "1.2.0",
      "released_at": "2026-06-04T12:00:00Z",
      "package": {
        "type": "zip",
        "url": "https://heaxhub.internal/installers/koo_preprocessor_1.2.0.zip",
        "sha256": "abc123def456...",
        "size_bytes": 1234567
      },
      "entry": {
        "executable": "KooPreprocessor.exe",
        "args_template": ["--config", "${USER_DIR}/config.json"],
        "working_dir": "${MODULE_DIR}"
      },
      "requirements": {
        "requires_admin": false,
        "min_windows": "10.0.19041",
        "depends_on": ["KooLicense>=1.0"]
      },
      "lifecycle": {
        "post_install_check": {
          "executable": "KooPreprocessor.exe",
          "args": ["--version"],
          "expected_stdout_regex": "^Koo Preprocessor 1\\.2\\.0",
          "timeout_sec": 10
        },
        "rollback_on_failure": true
      },
      "ui": {
        "icon_url": "https://heaxhub.internal/static/icons/koo_preprocessor.png",
        "color_accent": "#f59e0b",
        "show_in_tray": true
      },
      "tags": ["cae", "internal"],
      "visibility": "team:digital_twin_ai"
    }
  ]
}
```

치환 변수:

| 토큰 | 의미 |
|---|---|
| `${MODULE_DIR}` | `%LocalAppData%\HWAXAgent\modules\<id>\<version>` |
| `${USER_DIR}` | `%LocalAppData%\HWAXAgent` |
| `${AGENT_ID}` | 페어링된 agent_id |

### 7.1 HEAXHub `installer_packages` ↔ programs.json 필드 매핑

| programs.json 필드 | HEAXHub 출처 | 비고 |
|---|---|---|
| `id` | `apps.slug` | 카탈로그 PK 슬러그 |
| `name` | `apps.name` | |
| `description` | `apps.description` | |
| `category` | `apps.category` | |
| `version` | `installer_packages.version` | 최신 `published=true` 1건 |
| `released_at` | `installer_packages.uploaded_at` | |
| `package.url` | `installer_packages.download_url` | presigned URL 생성 시점 발급 |
| `package.sha256` | `installer_packages.sha256` | 업로드 시점 계산 |
| `package.size_bytes` | `installer_packages.size_bytes` | |
| `package.type` | `installer_packages.format` | `zip` / `msi` / `exe` |
| `entry.*` | `apps.extra.windows_install.entry` | App.extra JSON 신규 블록 |
| `requirements.*` | `apps.extra.windows_install.requirements` | |
| `lifecycle.*` | `apps.extra.windows_install.lifecycle` | |
| `ui.icon_url` | `apps.icon_url` | |
| `ui.color_accent` | `apps.extra.color_accent` | 없으면 amber 기본 |
| `tags` | `apps.tags` | |
| `visibility` | `apps.visibility` | `public` / `team:<slug>` / `user:<id>` |

→ DB 신규 컬럼은 **0개**. `apps.extra` 의 `windows_install` 블록만 표준화하면 끝.

---

## ◇ 8. 다운로드 + 검증 + Swap 알고리즘 (Rust pseudocode)

```rust
// src-tauri/src/installer/install.rs
use anyhow::{bail, Result};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use tokio::fs;

#[tauri::command]
pub async fn install_module(id: String, version: String, pkg: Package) -> Result<()> {
    let _guard = ModuleLock::acquire(&id)?;       // .lock 파일 기반 mutex

    // 1) 임시 다운로드 (.partial)
    let tmp = APP_DIR
        .join("cache/downloads")
        .join(format!("{id}-{version}.zip.partial"));
    ensure_origin_allowed(&pkg.url)?;             // allow-list 검증
    download_to(&pkg.url, &tmp, &auth_header()?).await?;

    // 2) sha256
    let actual = sha256_file(&tmp)?;
    if actual != pkg.sha256 {
        let _ = fs::remove_file(&tmp).await;
        audit("sha256_mismatch", &id, &version).await;
        bail!("sha256 mismatch (expected {}, got {})", pkg.sha256, actual);
    }

    // 3) 압축 해제 (staging) — zip slip 방어
    let staging = APP_DIR
        .join("modules")
        .join(&id)
        .join(format!("{version}.staging"));
    extract_zip_safe(&tmp, &staging)?;            // 각 entry path canonicalize

    // 4) post_install_check
    if let Some(check) = &pkg.lifecycle.post_install_check {
        let ok = run_check(&staging, check).await?;
        if !ok {
            rm_rf(&staging).await?;
            audit("post_install_check_failed", &id, &version).await;
            bail!("post_install_check failed");
        }
    }

    // 5) staging → final (rename: Windows 동일 볼륨 atomic)
    let final_dir = APP_DIR.join("modules").join(&id).join(&version);
    if final_dir.exists() {
        rm_rf(&final_dir).await?;                 // 같은 버전 재설치 시
    }
    fs::rename(&staging, &final_dir).await?;

    // 6) current.json 원자적 swap (tempfile + rename)
    let current = APP_DIR.join("modules").join(&id).join("current.json");
    let prev = load_current(&id).await.ok().map(|c| c.version);
    write_atomic_json(&current, &serde_json::json!({
        "version": version,
        "installed_at": chrono::Utc::now(),
        "sha256": pkg.sha256,
        "previous_version": prev,
    })).await?;

    // 7) 이전 버전 보존 GC
    gc_old_versions(&id, KEEP_LAST_N).await?;

    // 8) 정리
    let _ = fs::remove_file(&tmp).await;
    audit("installed", &id, &version).await;
    Ok(())
}

fn write_atomic_json(path: &Path, v: &serde_json::Value) -> Result<()> {
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(v)?)?;
    std::fs::rename(&tmp, path)?;                 // same-volume atomic
    Ok(())
}

fn extract_zip_safe(zip_path: &Path, dst: &Path) -> Result<()> {
    let f = std::fs::File::open(zip_path)?;
    let mut zip = zip::ZipArchive::new(f)?;
    let dst_c = dst.canonicalize().unwrap_or_else(|_| dst.to_path_buf());
    for i in 0..zip.len() {
        let mut e = zip.by_index(i)?;
        let out_path = dst.join(e.mangled_name());
        // zip slip 방어: 정규화 후 dst 하위인지 확인
        let parent = out_path.parent().unwrap_or(dst);
        std::fs::create_dir_all(parent)?;
        let canon = out_path.canonicalize().unwrap_or(out_path.clone());
        if !canon.starts_with(&dst_c) && !out_path.starts_with(dst) {
            bail!("zip entry escapes destination: {:?}", e.name());
        }
        if e.is_dir() {
            std::fs::create_dir_all(&out_path)?;
        } else {
            let mut o = std::fs::File::create(&out_path)?;
            std::io::copy(&mut e, &mut o)?;
        }
    }
    Ok(())
}
```

핵심:

- `write_atomic` = tempfile + rename. Windows 의 rename 은 동일 볼륨에서 atomic.
- staging → final 도 rename. 즉 "갑자기 전원 꺼져도 current.json 이 가리키는 버전 디렉토리가 항상 완전체"가 보장된다.
- zip slip 방어: entry path 가 dst 디렉토리를 벗어나면 즉시 abort.
- `.lock` 파일 + pid 로 동시 install 방지.

---

## ◇ 9. 롤백 메커니즘 상세

핵심 발상: **`current.json` 만 swap 한다. 디렉토리는 안 지운다.**

```
모듈 디렉토리 구조 (롤백 가능 상태)
modules\KooPreprocessor\
  ├─ 1.0.0\    (남아 있음, GC 대상)
  ├─ 1.1.0\    (이전 버전, 롤백 후보)
  ├─ 1.2.0\    (현재)
  └─ current.json  → version:1.2.0, previous_version:1.1.0
```

롤백 동작:

```rust
#[tauri::command]
pub async fn rollback_module(id: String, target: Option<String>) -> Result<()> {
    let current = load_current(&id).await?;
    let target_version = match target {
        Some(v) => v,
        None => current.previous_version
            .clone()
            .ok_or_else(|| anyhow!("no previous_version recorded"))?,
    };

    let target_dir = APP_DIR.join("modules").join(&id).join(&target_version);
    if !target_dir.exists() {
        bail!("target version {} no longer on disk (GC'd)", target_version);
    }

    write_atomic_json(
        &APP_DIR.join("modules").join(&id).join("current.json"),
        &serde_json::json!({
            "version": target_version,
            "installed_at": chrono::Utc::now(),
            "sha256": load_install_meta(&id, &target_version)?.sha256,
            "previous_version": current.version,    // 다시 앞으로 갈 수 있게
            "rolled_back_from": current.version,
        })
    ).await?;

    audit("rolled_back", &id, &target_version).await;
    Ok(())
}
```

GC 정책: `keep_last_n_versions=3` (config.json 으로 변경 가능). current 와 previous 는 무조건 보존.

---

## ◇ 10. Tauri Command API 카탈로그

JS↔Rust IPC 전체 목록. UI 가 호출할 수 있는 표면을 의도적으로 좁힌다.

| Command | 설명 | 호출 예 (JS) |
|---|---|---|
| `agent_status` | 에이전트 전체 상태 (last_sync, agent_id, 모듈 수, 에러 카운트) | `await invoke('agent_status')` |
| `start_pairing` | 페어링 URL 생성 → 브라우저 열기 | `await invoke('start_pairing')` |
| `complete_pairing` | 6자리 코드 검증 후 device JWT 저장 | `await invoke('complete_pairing', { code })` |
| `sync_manifest` | 서버 manifest 강제 동기화 → diff 반환 | `await invoke('sync_manifest')` |
| `list_modules` | 모든 모듈 상태 (id, current, latest, state) | `await invoke('list_modules')` |
| `module_detail` | 단일 모듈 상세 (history, install_meta, last_run) | `await invoke('module_detail', { id })` |
| `install_module` | 설치/업데이트 시작 (이벤트 스트림으로 진행률) | `await invoke('install_module', { id, version })` |
| `cancel_install` | 진행 중 설치 취소 → staging cleanup | `await invoke('cancel_install', { id })` |
| `run_module` | 모듈 실행, handle 반환 | `await invoke('run_module', { id, args })` |
| `stop_module` | 실행 중 모듈 종료 | `await invoke('stop_module', { handle })` |
| `rollback_module` | 이전 버전으로 current.json swap | `await invoke('rollback_module', { id })` |
| `uninstall_module` | 모듈 전체 제거 | `await invoke('uninstall_module', { id })` |
| `open_log` | 로그 폴더 또는 특정 로그 파일 열기 | `await invoke('open_log', { id?: })` |
| `tail_log` | 로그 마지막 N줄 읽어 반환 | `await invoke('tail_log', { id, lines })` |
| `update_config` | config.json 부분 패치 | `await invoke('update_config', { patch })` |
| `get_config` | 현재 config 전체 반환 | `await invoke('get_config')` |
| `health_check` | 서버 reachability + 로컬 디스크 free + 권한 | `await invoke('health_check')` |
| `make_dump` | 진단 zip 생성 (logs + config 익명화) | `await invoke('make_dump')` |
| `clear_cache` | downloads 임시 파일 비우기 | `await invoke('clear_cache')` |
| `quit` | 트레이 종료 | `await invoke('quit')` |

진행률은 Tauri 이벤트(`emit`/`listen`)로 별도 채널:

```ts
const unlisten = await listen<InstallProgress>('install:progress', (e) => {
  // { id, phase: 'download'|'verify'|'extract'|'check'|'swap', percent }
});
```

---

## ◇ 11. 트레이 + 자동 시작

### 11.1 트레이 빌드

```rust
use tauri::tray::{TrayIconBuilder, MouseButton};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};

fn build_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let modules = Submenu::new(app, "프로그램", true)?;
    // modules 항목은 runtime 에 동기화 시 다시 build
    let sync = MenuItem::with_id(app, "sync", "지금 동기화", true, None)?;
    let logs = MenuItem::with_id(app, "logs", "로그 폴더 열기", true, None)?;
    let settings = MenuItem::with_id(app, "settings", "설정...", true, None)?;
    let repair = MenuItem::with_id(app, "repair", "페어링 다시 하기", true, None)?;
    let quit = MenuItem::with_id(app, "quit", "종료", true, None)?;

    let menu = Menu::with_items(app, &[
        &modules,
        &PredefinedMenuItem::separator(app)?,
        &sync, &logs, &settings,
        &PredefinedMenuItem::separator(app)?,
        &repair, &quit,
    ])?;

    TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .on_menu_event(|app, ev| handle_tray_event(app, ev))
        .on_tray_icon_event(|tray, ev| {
            if let tauri::tray::TrayIconEvent::Click { button: MouseButton::Left, .. } = ev {
                if let Some(w) = tray.app_handle().get_webview_window("main") {
                    let _ = w.show(); let _ = w.set_focus();
                }
            }
        })
        .tooltip("HWAX Agent · 정상")
        .build(app)?;
    Ok(())
}
```

아이콘 dot: `icon_green.ico` / `icon_yellow.ico` / `icon_red.ico` 3종 미리 번들. 상태 변경 시 `tray.set_icon(...)`.

### 11.2 시작 시 자동 실행

레지스트리 Run 키는 사용자 동의 토글로만 등록. 기본 OFF 옵션도 검토. Tauri 공식 플러그인:

```rust
// Cargo.toml: tauri-plugin-autostart
use tauri_plugin_autostart::MacosLauncher;

tauri::Builder::default()
    .plugin(tauri_plugin_autostart::init(
        MacosLauncher::LaunchAgent, Some(vec!["--minimized"])
    ))
```

UI 에서 설정 토글이 켜질 때만 `enable()`, 꺼지면 `disable()`. **자동 등록 금지** — 사용자가 명시적으로 토글해야만 레지스트리에 키가 들어간다.

---

## ◇ 12. 인증 & 페어링 흐름

```
[Agent 첫 실행]
  │
  ▼ 트레이 → "페어링"
[Agent: start_pairing → 임시 device_pub 생성, 6자리 코드 표시]
  │
  ▼ 브라우저 자동 오픈: https://heaxhub/.../enroll?code=XXXXXX
[유저: HEAXHub 웹에 로그인 (회사 계정), 6자리 코드 확인 → 승인]
  │
  ▼
[HEAXHub: enrollment_token (1회용, 5분 유효) 발급 → 응답 페이지에 표시]
  │
  ▼ 유저가 토큰을 Agent UI 에 붙여넣기 (또는 redirect URI 처리)
[Agent: POST /api/v1/agents/enroll  { enrollment_token, device_pub, hostname, os }]
  │
  ▼
[HEAXHub: 검증 → windows_agents 행 생성 → device_jwt (90일) 발급]
  │
  ▼
[Agent: keyring("HWAXAgent", "device_jwt") 에 저장]
[Agent: config.json 에는 agent_id 만 기록 (JWT 평문 X)]
  │
  ▼
[이후 모든 호출: Authorization: Bearer <device_jwt>]
```

JWT 만료 흐름:

```
[401 받으면]
  │
  ▼
[POST /api/v1/agents/refresh  { agent_id, refresh_token (keyring) }]
  │
  ▼
[새 device_jwt 받아 keyring 갱신]
```

리프레시 실패 시 트레이 알림 + 재페어링 요구.

평문 저장 금지: device_jwt 든 refresh_token 이든 무조건 keyring (Windows Credential Manager) 경유.

---

## ◇ 13. Manifest 동기화 정책

- 주기: `config.json.sync_interval_min` (기본 30분)
- 트리거: 타이머 / 트레이 "지금 동기화" / 페어링 직후 / Agent 부팅 직후
- HTTP: `GET /api/v1/agents/manifest` + `If-None-Match: <last_etag>`
- 304 면 마지막 캐시 그대로 사용. 200 이면 `cache/manifest.json` 덮어쓰기.
- 마지막 동기화 시각은 트레이 툴팁에 표시 (`"동기화 12분 전"`).
- 서버 unreachable: 5회 연속 실패 → 트레이 황색 dot + 로그. 캐시로 계속 동작.

WebSocket 푸시(Phase 4 옵션):

```
[Agent]  WS connect  /ws/agent/{agent_id}?token=<jwt>
        ▼
[Server]  새 버전 publish 이벤트 발생 시 푸시
        ▼
[Agent]  즉시 sync_manifest 호출
```

WS 가 끊겨도 30분 polling 으로 fallback.

---

## ◇ 14. 실행 & 로그 스트리밍

```rust
use tokio::process::Command;

pub async fn run_module(id: &str) -> Result<RunHandle> {
    let current = load_current(id).await?;
    let module_dir = APP_DIR.join("modules").join(id).join(&current.version);
    let entry = manifest_entry_for(id).await?;
    let exe = module_dir.join(&entry.executable);

    // 화이트리스트: 실행 가능한 파일은 manifest.entry.executable 만 허용
    if !exe.exists() || !is_within(&exe, &module_dir) {
        bail!("entry executable not allowed: {:?}", exe);
    }

    let args = expand_template(&entry.args_template, id, &current.version);
    let log_path = APP_DIR.join("logs").join(format!(
        "run-{id}-{}.log",
        chrono::Utc::now().format("%Y-%m-%dT%H-%M-%S")
    ));
    let log_file = std::fs::File::create(&log_path)?;

    let child = Command::new(&exe)
        .args(&args)
        .current_dir(&module_dir)
        .stdout(log_file.try_clone()?)
        .stderr(log_file)
        .spawn()?;
    Ok(RunHandle { pid: child.id().unwrap_or(0), id: id.into() })
}
```

핵심: **manifest.entry.executable 외 임의 exe 실행 절대 금지.** 인자도 `args_template` 만 사용. UI 에서 사용자가 자유롭게 입력하는 인자 없음.

종료 코드 처리: 0 정상, 그 외 트레이 토스트 + 로그 링크.

---

## ◇ 15. 백신/EDR 오탐 회피 — 가이드라인 (사용자 강조)

이 절은 가장 중요. 사내 EDR(예: 카스퍼스키, MDE, SentinelOne) 가 Agent 또는 모듈을 의심 차단하면 Agent 가치가 0이 된다.

| 항목 | 정책 | 구현 위치 |
|---|---|---|
| ① 다운로드 URL 화이트리스트 | `config.allowed_origins` 와 정확 매칭만 허용 | `installer/download.rs::ensure_origin_allowed` |
| ② sha256 강제 | manifest에 sha256 없거나 mismatch → install reject | `installer/install.rs` |
| ③ 다운로드 경로 고정 | `%LocalAppData%\HWAXAgent\cache\downloads\` 외 금지 | `installer/download.rs` |
| ④ 코드 사인 | Agent 본체 + 모듈 zip 내부 exe 모두 사내 PKI/EV 사인 | 빌드 파이프라인 |
| ⑤ EDR 화이트리스트 | 운영팀과 협의해 폴더 + 프로세스명 + 사인 인증서 화이트리스트 등록 | 운영 문서 |
| ⑥ 의심 메타 로깅 | 파일 크기, 엔트로피, source URL, sha256 모두 audit | `audit_log` HEAXHub |
| ⑦ Tauri allowlist 최소화 | fs/shell/http scope 좁힘 | `tauri.conf.json` |
| ⑧ 자유 URL 입력 금지 | 서버 주소 잠금, 재페어링으로만 변경 | UI 설정 패널 |

### 15.1 `tauri.conf.json` allowlist 예시 (최소 권한)

```json
{
  "app": {
    "security": {
      "csp": "default-src 'self'; img-src 'self' data: https://heaxhub.internal; connect-src 'self' https://heaxhub.internal; script-src 'self'"
    }
  },
  "plugins": {
    "fs": {
      "scope": [
        { "path": "$LOCALDATA/HWAXAgent/**", "allow": true }
      ],
      "requireLiteralLeadingDot": false
    },
    "shell": {
      "scope": [
        { "name": "open-explorer", "command": "explorer.exe",
          "args": [ { "validator": "^[A-Za-z]:\\\\.+" } ] }
      ],
      "open": false
    },
    "http": {
      "scope": [
        { "url": "https://heaxhub.internal/**" }
      ]
    }
  }
}
```

`shell.open: false` 가 핵심. 임의 URL 또는 파일 실행 허용을 차단한다.

### 15.2 운영팀과 협의해야 할 항목

- HWAX Agent 설치 폴더 (`%LocalAppData%\Programs\HWAXAgent\` 또는 per-user MSI 의 디폴트)
- Agent 프로세스명 (`HWAXAgent.exe`)
- 사인 인증서 thumbprint
- 다운로드 도메인 (`heaxhub.internal`)

이 4개를 EDR 화이트리스트에 사전 등록하면 오탐 가능성이 크게 낮아진다.

---

## ◇ 16. 권한 모델 (관리자 권한 회피)

- 기본: standard user (`asInvoker`). 매니페스트에 `requestedExecutionLevel="asInvoker"` 명시.
- 쓰기 영역: `%LocalAppData%` 하위만. → UAC 불필요.
- `Program Files`, `Windows`, `System32` 절대 접근 안 함.
- 레지스트리: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 만 사용자 동의로 1회 등록. `HKLM` 접근 안 함.

만약 모듈이 `requires_admin=true` 라면:

```
[유저가 트레이에서 "관리자 권한으로 실행" 클릭]
  │
  ▼
[Agent: ShellExecuteW with "runas" verb]
  │
  ▼
[OS: UAC 프롬프트 표시 → 유저가 명시적 동의]
  │
  ▼
[새 elevated 프로세스로 모듈 실행]
```

Agent 자체는 elevated 상태로 가지 않는다. 자식 프로세스만 elevated.

### 16.1 Phase 3+ 옵션: Tray + Service 분리

진짜 관리자 권한이 필요한 작업(예: 드라이버 설치, HKLM 수정)이 일상화되면:

```
HWAX Agent Tray (per-user, asInvoker)
        │ IPC (Named Pipe \\.\pipe\hwax-agent)
        ▼
HWAX Agent Service (LocalSystem)
        │
        ▼
[관리자 권한 작업 수행]
```

- Tray 는 UI/상태만.
- Service 는 권한 필요 작업만. 매니페스트로 허용된 작업만 IPC 로 받음.
- 설치 시 Service 1회 등록(MSI per-machine 변형 필요). Phase 3 옵션.

Phase 1~2 에서는 절대 안 함. Tray 단일 프로세스로 충분.

---

## ◇ 17. 피해야 할 안티 패턴 (체크리스트)

| 안티 패턴 | 본 설계의 대응 |
|---|---|
| [ ] `C:\Program Files` 자동 설치 | `%LocalAppData%` 만 사용 |
| [ ] 관리자 권한 자동 요청 | `asInvoker` 기본, runas 는 사용자 클릭 시만 |
| [ ] 레지스트리 수정 | `HKCU\...\Run` 만 사용자 동의로, `HKLM` X |
| [ ] 서비스 자동 등록 | Phase 1~2 안 함, Phase 3+ 옵션 |
| [ ] 임의 exe 실행 | manifest.entry.executable 만 허용 |
| [ ] 사용자 입력 URL 다운로드 | `allowed_origins` 외 거부 |
| [ ] 서명 없는 exe 실행 | sha256 + (가능하면) 사인 검증 |
| [ ] 자동 업데이트 무검증 덮어쓰기 | staging + post_install_check + atomic swap |

이 8개는 코드 리뷰 체크리스트로도 사용한다. PR 에서 위 중 하나라도 위반하면 거부.

---

## ◇ 18. 자동 업데이트 (Agent 본체)

`tauri-plugin-updater` + Ed25519 사인. HEAXHub 가 업데이터 manifest 호스팅.

```json
GET /api/v1/installers/hwax-agent/latest
{
  "version": "1.0.3",
  "notes": "버그 수정 및 안정성 개선",
  "pub_date": "2026-06-05T09:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "BASE64_ED25519_SIG...",
      "url": "https://heaxhub.internal/installers/HWAXAgent_1.0.3_x64-setup.exe"
    }
  }
}
```

업데이트 정책:

- `auto_update=true` 면 백그라운드 다운로드 → 다음 부팅 시 적용 (즉시 강제 X)
- 사용자가 트레이 "지금 업데이트" 누르면 즉시 적용
- 서명 검증 실패 → 이전 빌드 유지 + 알림

Tauri updater 가 staging + rollback 을 내부적으로 처리하므로 별도 구현 없음.

---

## ◇ 19. 로깅 & 진단

- 라이브러리: `tracing` + `tracing-appender` (일별 롤링) + `tracing-subscriber` (JSON 포맷)
- 위치: `%LocalAppData%\HWAXAgent\logs\`
- 파일:
  - `agent-YYYY-MM-DD.log` — 에이전트 일반 로그
  - `install-<id>-<ver>.log` — 설치 한 건당 1파일
  - `run-<id>-<ts>.log` — 실행 한 건당 1파일
- 보존: 30일, 그 이상은 자동 삭제 (총량 1GB 캡)
- 트레이 → "로그 폴더 열기" 한 클릭

진단 dump 패키지:

```
[유저: 설정 → "진단 dump 만들기"]
  │
  ▼
[Agent: %temp%\hwax-dump-<ts>.zip 생성
   ├─ agent-*.log (최근 7일)
   ├─ install-*.log (최근 7일)
   ├─ config.json (agent_id, server 외 비식별)
   ├─ system.json (Windows 빌드, .NET 버전, 디스크 free)
   └─ manifest.json (마지막 캐시)
 ]
  │
  ▼
[탐색기로 해당 zip 위치 표시 → 유저가 운영팀에 첨부]
```

device_jwt 는 dump 에 포함 안 함. 익명화.

HEAXHub 로 audit 이벤트 전송:

```json
POST /api/v1/agents/audit
[
  { "kind": "installed", "module_id": "koo_preprocessor", "version": "1.2.0", "ts": "..." },
  { "kind": "rolled_back", "module_id": "koo_preprocessor", "to": "1.1.0", "reason": "user", "ts": "..." },
  { "kind": "sha256_mismatch", "module_id": "...", "expected": "...", "actual": "...", "ts": "..." },
  { "kind": "av_blocked_suspect", "module_id": "...", "evidence": {...}, "ts": "..." }
]
```

배치 전송 (5분 간격) + 즉시 전송 (실패/롤백/AV 의심).

---

## ◇ 20. HEAXHub 서버 측 변경 작업표

| 영역 | 변경 | 비고 |
|---|---|---|
| DB | `windows_agents.device_kind` 컬럼 추가 (`launcher` / `service`) | Alembic migration 1건 |
| DB | `installer_packages` 재사용 (`id`/`app_id`/`version`/`sha256`/`size_bytes`/`download_url`/`uploaded_at`/`format`) | 추가 컬럼 없음 |
| DB | `apps.extra.windows_install` 블록 표준화 (entry/requirements/lifecycle) | JSON 컬럼, 마이그레이션 불필요 |
| API | `POST /api/v1/agents/enroll` 1회용 토큰 → device JWT | 신규 |
| API | `POST /api/v1/agents/refresh` JWT 갱신 | 신규 |
| API | `GET /api/v1/agents/manifest` programs.json 발급 (ETag) | 신규 |
| API | `POST /api/v1/agents/installs` 설치 결과 보고 | 신규 |
| API | `POST /api/v1/agents/audit` audit 이벤트 (배치) | 신규 |
| API | `POST /api/v1/agents/heartbeat` 30분 주기 (선택) | 신규 |
| API | `GET /api/v1/installers/{id}/download` presigned URL | 신규 |
| API | `GET /api/v1/installers/hwax-agent/latest` updater manifest | 신규 |
| WS | `/ws/agent/{agent_id}` 실시간 알림 | Phase 4 (옵션) |
| UI | HEAXHub 웹에 "내 디바이스" 페이지: 페어링된 Agent 목록 + 토큰 회수 | Phase 2 |

신규 endpoint 합계: **9개** (Phase 4 WS 제외).

---

## ◇ 21. 빌드/배포 파이프라인

```
[개발자: feature 브랜치 → main 머지]
        │
        ▼
[GitHub Actions / 사내 Jenkins (windows-latest)]
        │
        ├─ pnpm install
        ├─ pnpm tauri build
        │     ↓ 산출물: .msi, NSIS .exe (시작 시 자동 실행 옵션 포함)
        ├─ signtool sign (EV 또는 사내 PKI)
        ├─ sha256 계산
        └─ HEAXHub installer_packages 업로드 (presigned PUT)
              │
              ▼
        [HEAXHub: app=hwax-agent / version 자동 등록]
              │
              ▼
        [updater manifest 갱신 → 클라이언트 polling 으로 발견]
```

릴리스 채널: `stable` / `beta` / `dev` (config.json 의 채널 선택). beta/dev 는 운영팀 PC 일부에만 배포.

Ed25519 사인 키:

- 사내 SMB 의 보호된 폴더 (`\\fileserver\secrets\hwax-agent\private.key`) — Phase 1
- Azure Key Vault 또는 사내 HSM — Phase 3+

---

## ◇ 22. 저장소 폴더 구조

```
HWAXAgent/
├─ package.json
├─ pnpm-workspace.yaml
├─ apps/
│  └─ agent/                    # Tauri 2 + React UI
│     ├─ index.html
│     ├─ src/                   # React (트레이 윈도우, 설정 패널)
│     │  ├─ main.tsx
│     │  ├─ App.tsx
│     │  ├─ panels/             # TrayMain / Detail / Settings
│     │  ├─ components/         # shadcn-ui 재포팅
│     │  ├─ hooks/              # useModules / useConfig / useSync
│     │  └─ ipc/                # invoke 래퍼 + 타입
│     ├─ src-tauri/             # Rust core
│     │  ├─ src/
│     │  │  ├─ main.rs
│     │  │  ├─ commands/        # install / run / config / log
│     │  │  ├─ installer/       # download / sha256 / unzip / swap / lock
│     │  │  ├─ store/           # config.json / current.json / manifest cache
│     │  │  ├─ tray/            # 메뉴 / 상태 dot / 토스트
│     │  │  ├─ auth/            # keyring / pairing / jwt refresh
│     │  │  ├─ sync/            # manifest polling / WS (옵션)
│     │  │  └─ telemetry/       # tracing + audit batch
│     │  ├─ icons/              # green/yellow/red dot
│     │  ├─ tauri.conf.json     # allowlist, CSP, updater
│     │  └─ Cargo.toml
│     └─ vite.config.ts
├─ packages/
│  ├─ design-tokens/            # HEAXHub 토큰 (다크 + amber accent, Pretendard Variable)
│  └─ schemas/                  # manifest JSON schema (Rust + TS 양쪽 검증)
├─ docs/                        # 운영 가이드, 트러블슈팅, EDR 화이트리스트 가이드
├─ scripts/
│  ├─ sign.ps1                  # signtool 래퍼
│  └─ publish.ps1               # HEAXHub 업로드
└─ .github/workflows/
   ├─ build-and-sign.yml
   └─ release.yml
```

---

## ◇ 23. 로드맵 (4 Phase)

| Phase | 기간 | 주요 산출물 |
|---|---|---|
| **Phase 1 — MVP** | 3주 | 트레이 상주, 페어링, manifest sync, zip 다운로드, sha256, swap, exe 실행, 로그 보기. 모든 모듈 user 권한. |
| **Phase 2 — 안정화** | 3주 | Agent 본체 자동 업데이트, `post_install_check`, 롤백 UI, 다중 버전 GC, 토스트 알림, 설정 패널 완성, audit 배치 전송. |
| **Phase 3 — 권한 확장 (옵션)** | 3주 | Tray + Service 분리, MSI 인스톨러 변형(per-machine), EDR 화이트리스트 운영 가이드, 사내 PKI 코드 사인 자동화. |
| **Phase 4 — 확장** | 3주 | WebSocket 실시간 알림, 정책(사용자 그룹별 allow/deny), 익명 telemetry, 다국어(한/영), mac/Linux 시범 빌드. |

Phase 1 의 "끝났다" 정의: 신규 사용자가 MSI 더블클릭 → 페어링 → 트레이에서 모듈 1개 다운로드 → 검증 → 실행까지 5분 이내 무중단으로 완료.

---

## ◇ 24. 리스크 & 완화 (사용자 6 우선순위 × 완화)

| 우선순위 | 리스크 | 완화 |
|---|---|---|
| ① 빨리 만들 수 있는가 | Tauri 2 Rust 학습 곡선 | React/TS 표면 95%, Rust 표면 5%. 핵심 Rust 코드는 본 문서 §8 의 패턴 외 거의 없음. |
| ② 업데이트/배포가 쉬운가 | 클라이언트 자동 업데이트 검증 누락 | Tauri updater 의 Ed25519 + 자체 sha256 이중 검증 |
| ③ 사용자 PC에서 안 깨지는가 | 다운로드 중 전원 차단 / 부분 압축 해제 | staging + atomic rename. current.json 이 가리키는 디렉토리는 항상 완전 |
| ④ 로그/복구가 되는가 | 사용자가 "안 됨" 만 보고 | 진단 dump zip 1클릭 + audit 자동 전송 |
| ⑤ 백신 오탐 | 사내 EDR 의 휴리스틱 차단 | allow-list + sha256 + 사인 + EDR 사전 화이트리스트 + 자유 URL 차단 |
| ⑥ 확장 가능 | Windows 외 OS 요구 발생 | Tauri 의 mac/Linux 빌드 가능. UI 100% 재사용. installer 전략만 OS별 추가 |

---

## ◇ 25. mac / Linux 확장 가능성 (Deferred)

Tauri 2 의 본래 강점. 본 문서 범위 외이지만 가능성은 열어둔다.

| 영역 | macOS | Linux |
|---|---|---|
| 배포 형식 | `.pkg` / `.dmg` / Homebrew Cask | `.AppImage` / `.deb` / Flatpak |
| 자동 실행 | LaunchAgent (`~/Library/LaunchAgents/`) | `~/.config/autostart/*.desktop` |
| 자격 증명 저장 | Keychain (`keyring` crate) | Secret Service (gnome-keyring) |
| 트레이 | macOS menubar | Linux StatusNotifierItem (KDE/GNOME ext) |
| 모듈 실행 권한 | 코드 사인 + notarization 필수 | execute 비트 + sha256 |

UI 코드, IPC 명세, manifest 스키마는 100% 재사용. installer 어댑터만 OS별 swap.

---

## ◇ 26. 부록

### 26.1 참고 링크

- Tauri 2 — https://v2.tauri.app/
- tauri-plugin-updater — https://v2.tauri.app/plugin/updater/
- tauri-plugin-autostart — https://v2.tauri.app/plugin/autostart/
- keyring crate (Windows Credential Manager) — https://docs.rs/keyring/
- zip slip 방어 — https://snyk.io/research/zip-slip-vulnerability
- HEAXHub backend — `/home/koopark/claude/HEAXHub/backend/`

### 26.2 용어집

| 용어 | 의미 |
|---|---|
| Tray | 트레이 (윈도우 우측 하단 알림 영역) |
| IPC | Inter-Process Communication. Tauri 의 invoke/listen 채널 |
| Atomic swap | tempfile + rename 으로 절대 부분 상태가 보이지 않도록 교체 |
| post_install_check | 설치 직후 동작 확인용 명령(예: `--version`) |
| allow-list | 명시 허용 목록(반대는 deny-list). 보안에서 항상 허용 목록이 안전 |
| current.json | 모듈의 "어느 버전이 활성인가" 를 가리키는 단일 진실 |
| staging | 설치 진행 중인 임시 디렉토리(`*.staging`) |

### 26.3 manifest 샘플 3종

**(a) PyInstaller 산출물 (zip 압축)**

```json
{
  "id": "koo_postprocessor",
  "name": "Koo Postprocessor",
  "version": "0.9.1",
  "package": {
    "type": "zip",
    "url": "https://heaxhub.internal/installers/koo_post_0.9.1.zip",
    "sha256": "...",
    "size_bytes": 32450000
  },
  "entry": {
    "executable": "KooPost\\KooPost.exe",
    "args_template": [],
    "working_dir": "${MODULE_DIR}\\KooPost"
  },
  "requirements": { "requires_admin": false }
}
```

**(b) 단독 exe**

```json
{
  "id": "koo_license_check",
  "name": "Koo License Check",
  "version": "1.0.0",
  "package": {
    "type": "zip",
    "url": "https://heaxhub.internal/installers/koo_license_1.0.0.zip",
    "sha256": "...",
    "size_bytes": 850000
  },
  "entry": {
    "executable": "KooLicense.exe",
    "args_template": ["--check"],
    "working_dir": "${MODULE_DIR}"
  },
  "lifecycle": {
    "post_install_check": {
      "executable": "KooLicense.exe",
      "args": ["--version"],
      "expected_stdout_regex": "^Koo License 1\\.0\\.0",
      "timeout_sec": 5
    },
    "rollback_on_failure": true
  }
}
```

**(c) 폴더형 portable (플러그인 type)**

```json
{
  "id": "nx_plugin",
  "name": "NX Plugin",
  "version": "0.3.0",
  "category": "plugin",
  "package": {
    "type": "zip",
    "url": "https://heaxhub.internal/installers/nx_plugin_0.3.0.zip",
    "sha256": "...",
    "size_bytes": 4500000
  },
  "entry": {
    "executable": "",
    "args_template": [],
    "working_dir": "${MODULE_DIR}"
  },
  "requirements": { "requires_admin": false },
  "ui": { "show_in_tray": false }
}
```

플러그인 타입은 실행 가능 entry 가 없을 수 있다. Agent 는 디렉토리만 배치하고 NX 가 알아서 로드.

### 26.4 `config.json` 전체 필드 표

| 필드 | 타입 | 기본 | 설명 |
|---|---|---|---|
| `server` | string | (페어링 시 설정) | HEAXHub base URL |
| `agent_id` | string | (페어링 시 설정) | 페어링된 agent ID |
| `auto_update` | bool | `true` | Agent 본체 + 모듈 자동 업데이트 |
| `start_on_boot` | bool | `false` | Windows 시작 시 자동 실행 |
| `log_level` | string | `"info"` | trace/debug/info/warn/error |
| `allowed_origins` | string[] | `[server]` | 다운로드 허용 도메인 |
| `keep_last_n_versions` | int | `3` | 모듈 이전 버전 보존 수 |
| `sync_interval_min` | int | `30` | manifest polling 주기 (분) |
| `channel` | string | `"stable"` | stable/beta/dev |
| `proxy` | string? | `null` | 사내 프록시 URL (선택) |
| `telemetry_anonymous` | bool | `false` | 익명 사용 통계 전송 |

---

## ◇ 27. 부록 B — Phase 1 작업 분해 (참고)

| 주차 | 작업 |
|---|---|
| W1 | 저장소 부트스트랩, Tauri 2 프로젝트, 트레이 메뉴, config.json, HEAXHub `agents/enroll` 엔드포인트 + 페어링 UI |
| W2 | manifest sync, sha256, zip 다운로드/추출/staging swap, current.json, 모듈 목록 패널(React), install:progress 이벤트 |
| W3 | 로그 폴더 열기, 진단 dump, audit 전송, 트레이 토스트, 통합 테스트, 사내 PC 3대 회귀, EDR 화이트리스트 1차 협의 |

---

## ◇ 28. 끝맺음

HWAX Agent v2 의 핵심은 짧다:

- **트레이에 살고**, manifest 를 받아오고, sha256 으로 검증하고, atomic 하게 swap 하고, 사용자가 부르면 실행한다.
- 그 외 모든 표면(자유 URL 입력, 임의 exe 실행, 관리자 권한, 레지스트리 수정)은 **고의로 좁힌다.**
- HEAXHub 의 카탈로그/인증/저장소를 그대로 재사용하고 신규 endpoint 9개만 추가한다.
- mac/Linux 확장 가능성은 열어두되 Phase 4 이전엔 안 한다.

이 문서가 v2 의 단일 진실이며, 이후 PR/코드 리뷰는 본 문서의 §17 안티 패턴 체크리스트와 §15 백신 회피 가이드를 기준선으로 사용한다.

— 끝.
