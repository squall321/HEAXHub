# HWAXLauncher — Windows Desktop Agent 기획서

> HEAXHub 자매 프로젝트. 사내 사용자가 윈도우 데스크탑에서 HEAXHub 카탈로그를 검색·설치·실행하는 단일 진입점.

| 항목 | 내용 |
|---|---|
| 문서 버전 | v0.1 (초안) |
| 대상 OS | Windows 10 21H2+ / Windows 11 22H2+ (x64, arm64는 Phase 4) |
| 모기지 시스템 | HEAXHub (FastAPI :4040, Caddy :4180, React 18) |
| 통신 프로토콜 | HTTPS REST + WebSocket (`/api/v1/...`, `/ws/...`) |
| 추천 스택 | **Tauri 2 + React 18 + TypeScript + TanStack Router/Query + Radix/Tailwind** |
| 배포 형식 | per-user MSI (WiX) + 자동 업데이트 (Tauri updater, Ed25519) |
| 코드 사인 | DigiCert EV (사내 PKI 병행 검토) |
| 작성자 | HEAXHub Platform Team |

---

## ◇ 1. 요약 (Executive Summary)

HWAXLauncher 는 HEAXHub 가 카탈로그·인증·매니페스트·인스톨러 저장소를 책임지고, 윈도우 워크스테이션에서는 데스크탑 런처가 **검색 → 설치 → 실행 → 보고**의 라이프사이클을 담당하는 분리된 두 컴포넌트 모델을 따른다.

핵심 의사결정 요지:

1. **UI 프레임워크**는 Tauri 2를 채택한다. 바이너리 크기(~10MB), 메모리 footprint, HEAXHub 프론트엔드와의 React 18 + TanStack Router 코드 공유, 내장 updater/시그너처가 의사결정 요소다. WPF/.NET 은 도메인 정책 친화성 측면에서 강력하지만 HEAXHub 와의 UI 스타일·디자인 토큰 공유 비용이 크고, Electron 은 메모리/배포 크기에서 사내 노트북(8GB RAM)을 압박한다.
2. **HEAXHub 측 모델 재사용**: `windows_agents`(enrollment + JWT), `installer_packages`(sha256, signed, version, os), `App.app_type=windows_gui` + `execution_target=local_pc` 가 이미 정의되어 있어 신규 테이블 추가는 최소화한다.
3. **설치 트랜잭션**은 State Machine 으로 모델링한다: `queued → downloading → verifying → installing → reporting → installed/failed/rolled_back`. 각 상태 전이에 audit_log 발송과 UI 토스트가 따른다.
4. **자동화 CLI 실행**은 옵션 두 가지를 명시 노출한다 — (a) HWAXLauncher 가 로컬에서 직접 실행 (b) HEAXHub Job Runner 에 위임. 매니페스트(`run.target`)가 우선, 사용자 override 가능.
5. **로드맵 4 Phase**: MVP (카탈로그·MSI 설치·인증) → CLI 실행·WebSocket → 자동 업데이트·정책 → 오프라인 캐시·텔레메트리.

리스크 / 트레이드오프:

- Tauri 2 는 1.x 대비 안정화 중이다. 마이그레이션 문서·플러그인 호환성을 Phase 1 KO 이전에 검증해야 한다.
- 사내 SmartScreen 평판 누적까지는 EV 코드 사인 + 정책 배포가 필요하다.
- MSI silent install 결과를 한 줄로 보고하기에는 ExitCode 외 정보가 부족해, vendor 별 install_log 파서를 별도로 만들어야 한다.

---

## ◇ 2. 시스템 컨텍스트 (Context Diagram)

```
                ┌─────────────────────────────────────────────┐
                │                HEAXHub Server                │
                │  ┌──────────────┐   ┌──────────────────┐    │
                │  │ FastAPI:4040 │   │ Celery beat/worker│    │
                │  └──────┬───────┘   └────────┬─────────┘    │
                │         │ JWT/REST           │ scan/sync    │
                │  ┌──────┴───────┐   ┌────────┴─────────┐    │
                │  │ Postgres     │   │ integrations/    │    │
                │  │ apps/users/  │   │ manifest.yaml    │    │
                │  │ installer_pkg│   └──────────────────┘    │
                │  │ windows_agts │                            │
                │  └──────────────┘                            │
                └──────────────┬──────────────────────────────┘
                               │ HTTPS  (REST + WS)
                               │ enrollment + device JWT
                ┌──────────────┴──────────────────────────────┐
                │            Windows Workstation               │
                │  ┌────────────────────────────────────────┐  │
                │  │  HWAXLauncher.exe (Tauri shell)        │  │
                │  │  ┌────────────┐  ┌──────────────────┐  │  │
                │  │  │ WebView2   │  │ Rust core        │  │  │
                │  │  │ React UI   │←→│ ipc / fs / proc  │  │  │
                │  │  └────────────┘  └─────┬────────────┘  │  │
                │  │        ▲               │               │  │
                │  │        │ tauri:invoke  │ spawn         │  │
                │  │  ┌─────┴───────┐  ┌────┴──────────┐    │  │
                │  │  │ Catalog UI  │  │ msiexec /     │    │  │
                │  │  │ Job Runner  │  │ python.exe /  │    │  │
                │  │  │ Settings    │  │ winget / pwsh │    │  │
                │  │  └─────────────┘  └───────────────┘    │  │
                │  └────────────────────────────────────────┘  │
                │  %LocalAppData%\HWAXLauncher\                │
                │   ├─ cache\         (msi/exe + sha256)       │
                │   ├─ logs\          (rolling json log)       │
                │   ├─ catalog.db     (sqlite snapshot)        │
                │   └─ keychain ref   (Cred Mgr alias)         │
                └──────────────────────────────────────────────┘
```

---

## ◇ 3. UI 프레임워크 비교 및 선택

### 3.1 비교표

| 항목 | Tauri 2 | WPF / WinUI 3 | Electron | MAUI | Flutter Desktop |
|---|---|---|---|---|---|
| 바이너리 크기 | ~10 MB | ~30 MB (FW dep) / ~80 MB (self-contained) | ~150 MB+ | ~40-60 MB | ~25-40 MB |
| 메모리 (idle) | 80-150 MB | 60-120 MB | 250-400 MB | 180-250 MB | 150-220 MB |
| HEAXHub 코드 공유 | ★★★ React 그대로 | ☆ XAML 재작성 | ★★★ React 그대로 | ☆ XAML/Razor | ☆ Dart 재작성 |
| 시스템 통합 (COM/레지스트리/AppX) | ★★ Rust crate / windows-rs | ★★★ 네이티브 | ★ node-ffi 우회 | ★★★ 네이티브 | ★ FFI/플러그인 |
| 자동 업데이트 | ★★★ 내장 (Ed25519) | ★★ ClickOnce / 커스텀 | ★★ electron-updater | ★ Store/MSIX | ★ 직접 구현 |
| 코드 사인 워크플로 | 성숙 | 가장 성숙 | 성숙 | 성숙 | 미성숙 |
| 도메인 정책(GPO) 친화도 | ★★ | ★★★ | ★★ | ★★★ | ★ |
| 개발자 생산성 (UI) | ★★★ | ★★ | ★★★ | ★★ | ★★★ |
| Rust/네이티브 학습 곡선 | ▲ Rust 일부 | — | — | — | — |
| 보안 기본값 | CSP·allowlist 기반 | 신뢰 가정 | 광범위 surface | 신뢰 가정 | 신뢰 가정 |

### 3.2 결정: **Tauri 2**

근거:

1. HEAXHub 프론트엔드(React 18 + Vite + TanStack Router/Query + Radix + Tailwind)의 UI 컴포넌트·디자인 토큰·라우터 정의를 그대로 재활용한다. pnpm workspace 하나로 묶으면 Storybook·shadcn/ui 카탈로그를 양쪽에서 본다.
2. Rust 코어가 `msiexec`, `Set-ExecutionPolicy`, Credential Manager (`wincred` crate), Mica/Acrylic (`window-vibrancy`) 와 같은 Win32 통합을 안전하게 격리한다. Node 런타임을 박지 않으므로 보안 surface 가 작다.
3. 내장 updater 가 Ed25519 서명을 강제하고, 업데이트 매니페스트 URL 을 HEAXHub `/api/v1/apps/hwax_launcher/installers/latest?os=windows-x64` 로 연결하면 별도 인프라가 필요 없다.

단점 인정:

- Tauri 2 는 GA 직후라 1.x 대비 플러그인(예: `tauri-plugin-store`, `-updater`, `-shell`) 버전 fragmentation 이 존재한다. **lockfile + 사내 미러 권장.**
- Rust 인력 부재 시 IPC 명세(`#[tauri::command]`) 가 진입 장벽이다. → 초기에는 ”얇은 Rust + 두꺼운 React“ 가이드라인을 둔다.
- 윈도우 GPO 가 Webview2 분배를 차단하는 환경이 있다. 사내 베이스 이미지에 Webview2 Evergreen Bootstrapper 를 사전 배포한다.

### 3.3 차선책

- **Plan B**: WPF (.NET 8 self-contained). 도메인 통합·코드 사인·로컬 정책 측면이 가장 깔끔. HEAXHub 디자인 시스템 일부 재작성 비용을 감수한다. Phase 4 이후 ”엄격한 정책 환경 전용 빌드“ 의 옵션으로 남긴다.
- **Plan C**: Electron. 채택 시 메모리/배포 크기 페널티를 8GB RAM 노트북 표준에 맞추기 위해 자식 프로세스 분리 + `partition` 정책으로 완화. 우선순위 낮음.

---

## ◇ 4. 프런트엔드 스택 상세 (Tauri WebView 내부)

| 영역 | 선택 | 이유 |
|---|---|---|
| UI 런타임 | React 18 + TypeScript 5 | HEAXHub 와 동일, 인력 풀 공유 |
| 빌드 | Vite 5 (`@tauri-apps/cli`) | HMR + esbuild, Tauri 공식 권장 |
| 라우팅 | TanStack Router | 파일/타입 안전, HEAXHub routeTree.gen.ts 패턴 일치 |
| 서버 상태 | TanStack Query | 캐시·재시도·낙관적 업데이트 |
| 컴포넌트 프리미티브 | Radix UI | a11y, 접근성 기본 |
| 컴포넌트 패턴 | shadcn/ui (사내 fork) | HEAXHub 와 토큰 공유 |
| 스타일 | Tailwind v4 + CSS variables | 다크 기본 + amber accent |
| 모션 | framer-motion (또는 motion one) | 패널 전환 / 인스톨 진행 morphing |
| 폼 | react-hook-form + zod | 매니페스트 inputs → 폼 자동 생성 |
| 아이콘 | lucide-react | 카테고리 아이콘 통일 |
| 상태(클라이언트) | Zustand (작게) | settings/UI flag 한정 |
| i18n | i18next (ko, en) | 사내 영문 사용자 대응 |

다크 모드는 기본값이고, `prefers-color-scheme` 변경 시 즉시 토글한다. **이모지는 카테고리 아이콘(■ ▶ ◇) 외 사용하지 않는다.**

---

## ◇ 5. 인증 / 통신 / 페어링

### 5.1 페어링 (1회용 enrollment)

```
[Admin Web UI]          [HEAXHub API]              [HWAXLauncher]
     │ create agent          │                            │
     │─────────────────────→ │                            │
     │ enrollment_token      │                            │
     │ (one-shot, TTL 10m)   │                            │
     │ (QR + 코드 표시)       │                            │
     │                       │ ←── /agents/enroll ──── ──│  (token + hostname)
     │                       │                            │
     │                       │ ─── device_jwt ─────────→ │  (Refresh JWT 발급)
     │                       │                            │
     │                       │ ←── /agents/heartbeat ────│  (Bearer device_jwt)
```

- 신규 엔드포인트 `POST /api/v1/agents/enroll` 추가: enrollment_token → 검증 → `WindowsAgent` 행 활성화 → device-scoped JWT(Access 1h / Refresh 30d) 반환. 기존 `windows_agents.auth_token_hash` 를 enrollment-token 해시 자리로 그대로 활용.
- 사용자 단위 JWT(HEAXHub 일반 사용자)와 디바이스 JWT 를 결합. 카탈로그 조회는 사용자 JWT, audit/heartbeat 는 디바이스 JWT 로 양분.

### 5.2 토큰 보관

- Windows Credential Manager (`wincred::CredentialBuilder`) 에 사용자 단위 자격으로 저장. 키 이름: `HWAXLauncher/<server-host>/<scope>`.
- 평문은 메모리에만, 디스크에는 OS 보호 storage 외 기록 금지.
- 로그아웃 시 즉시 `wincred::delete` + 서버에 토큰 폐기 요청.

### 5.3 통신

- **HTTPS** (사내 CA + Let's Encrypt 둘 다 trust store 등록). `mTLS` 는 옵션 (Settings → Advanced).
- HTTP 클라이언트: Rust 측 `reqwest` (cert pinning 옵션), 프런트 측 `ofetch` + TanStack Query.
- **WebSocket** `/ws/launcher/{device_id}` 에서 push 이벤트 수신: `app.published`, `installer.uploaded`, `policy.updated`, `job.completed`. 재접속은 expo backoff (1s → 2s → ... cap 30s).

### 5.4 오프라인 모드

- 마지막 성공 카탈로그 스냅샷을 `catalog.db`(SQLite, sqlx) 에 저장. UI 는 “오프라인 (마지막 동기화: 14분 전)” 배지 표시.
- 인증 만료된 상태에서는 캐시된 카탈로그 열람만 허용, 설치/실행은 차단.

---

## ◇ 6. UI / UX 컨셉

### 6.1 정보 구조

```
HWAXLauncher
├─ 카탈로그 (검색·필터·태그)
│   ├─ 앱 카드
│   ├─ 앱 상세 (스크린샷, 버전, 변경 이력, 설치/실행 CTA)
│   └─ 설치 큐 (진행 중 / 완료 / 실패)
├─ 내 PC
│   ├─ 설치된 앱 (제거, 업데이트, "서버에서 다시 보기")
│   └─ 최근 실행 (10건)
├─ 자동화 도구 (CLI)
│   ├─ 매니페스트 폼 (inputs)
│   ├─ 실행 위치 선택: 로컬 PC | 서버 Job Runner
│   └─ 결과 로그 (stream)
├─ 알림
└─ 설정
    ├─ 계정 / 디바이스 / 로그아웃
    ├─ 서버 주소 / mTLS
    ├─ 캐시 / 데이터 위치
    ├─ 정책 (조회만, 읽기 전용)
    └─ 정보 / 업데이트 확인
```

### 6.2 메인 셸 와이어프레임

```
┌──────────────────────────────────────────────────────────────────┐
│ HWAXLauncher                                ●online   user ▾  ⚙  │
├──────┬───────────────────────────────────────────────────────────┤
│      │  [ Search apps...                              ] [ 태그 ▾ ] │
│ ■    │ ───────────────────────────────────────────────────────── │
│ 카탈 │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐      │
│ 로그 │  │ HEAX Mesher  │ │ HEAX Plotter │ │ HEAX CMS Tool│      │
│      │  │ windows_gui  │ │ cli_tool     │ │ windows_gui  │      │
│ ▶    │  │ v3.4.1       │ │ v1.2.0       │ │ v0.9.0  β    │      │
│ 내PC │  │ [설치] [상세] │ │ [실행] [상세] │ │ [업데이트]    │      │
│      │  └──────────────┘ └──────────────┘ └──────────────┘      │
│ ◇    │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐      │
│ 자동 │  │ ...          │ │ ...          │ │ ...          │      │
│ 화   │  └──────────────┘ └──────────────┘ └──────────────┘      │
│      │                                                            │
│ ◆    │                                                            │
│ 알림 │                                                            │
│      │                                                            │
│ ⚙    │                                                            │
│ 설정 │                                                            │
├──────┴───────────────────────────────────────────────────────────┤
│ 카탈로그 동기화: 방금 전 · 설치 진행: 1건 · 서버: hub.heax.local  │
└──────────────────────────────────────────────────────────────────┘
```

### 6.3 설치 모달 (State Machine 가시화)

```
┌── 설치: HEAX Mesher v3.4.1 ──────────────────────────────────┐
│  버전     : 3.4.1 (서명됨, 사내 EV)                            │
│  크기     : 184 MB                                            │
│  대상     : C:\Users\<me>\AppData\Local\HEAX\Mesher           │
│  방식     : per-user MSI (silent)                              │
│                                                               │
│  ▸ 다운로드 ▸ 검증(SHA256) ▸ 설치 ▸ 보고                       │
│  ●─────────●─────────○─────────○                              │
│  [###############---------]  72%  (132 MB / 184 MB)            │
│                                                               │
│  로그 (최근 5줄)                                                │
│  ─────────────────────────────────────────                    │
│  10:01:03 cache miss, downloading ...                          │
│  10:01:07 connection: hub.heax.local                           │
│  10:01:14 progress 30% / 60% / 72%                             │
│                                                               │
│                                       [ 취소 ]  [ 백그라운드 ] │
└──────────────────────────────────────────────────────────────┘
```

### 6.4 자동화 도구 실행 와이어프레임

```
┌── 실행: HEAX CMS Batch Runner ──────────────────────────────┐
│  실행 위치   ◉ 로컬 PC      ○ 서버 Job Runner                │
│  ─────────────────────────────────────────                    │
│  입력 폴더   [ C:\runs\2026-06 ............ ] [찾아보기]      │
│  체크포인트  [ 1000 ............................ ]            │
│  GPU         ○ 사용  ◉ 사용 안 함                              │
│                                                               │
│  ─ 실행 후 로그 ─────────────────────────────                  │
│  [INFO ] launching cms_batch.exe ...                          │
│  [INFO ] loaded 14,221 keypoints                              │
│  [DEBUG] step 100 / 1000  loss=0.4321                         │
│                                                               │
│            [ 실행 ]  [ 결과 폴더 열기 ]  [ 서버에 결과 업로드 ] │
└──────────────────────────────────────────────────────────────┘
```

### 6.5 시각 톤

- Windows 11 Mica 효과 (`window-vibrancy` crate, Mica + Acrylic fallback). 윈도우 10 에서는 어두운 솔리드.
- 배경: `#0E0F12` 기본 + amber `#F5A524` accent (HEAXHub 동일 토큰).
- 폰트: Pretendard Variable (사내 라이선스 보유 가정), 코드/로그: JetBrains Mono.
- 모션: 350ms ease-out 페널 슬라이드, 진행 바는 spring(stiffness 220, damping 26).

---

## ◇ 7. 설치 워크플로우

### 7.1 상태 머신

```
        ┌──────────┐
        │  queued  │
        └────┬─────┘
             │ start()
             ▼
      ┌─────────────┐    download_failed
      │ downloading │─────────────────────┐
      └────┬────────┘                     │
           │ ok                            │
           ▼                               │
      ┌─────────────┐    hash_mismatch    │
      │  verifying  │─────────────────────┤
      └────┬────────┘                     │
           │ ok                            │
           ▼                               │
      ┌─────────────┐    msi_failed       │
      │ installing  │─────────────────────┤
      └────┬────────┘                     │
           │ exit=0                        │
           ▼                               │
      ┌─────────────┐                     │
      │  reporting  │                     │
      └────┬────────┘                     │
           │ ok                            │
           ▼                               │
      ┌─────────────┐                ┌────▼────────┐
      │  installed  │                │   failed    │
      └─────────────┘                └────┬────────┘
                                           │ rollback()
                                           ▼
                                     ┌────────────┐
                                     │ rolled_back│
                                     └────────────┘
```

### 7.2 단계별 동작

| 단계 | 동작 | 실패 처리 |
|---|---|---|
| download | `installer_url` HTTPS GET (Range 지원), `%LocalAppData%\HWAXLauncher\cache\<sha256>` 저장 | retry 3회 (backoff), 사용자에게 재시도 / 취소 |
| verify | SHA256 == DB 등록값 확인, `signed=true` 인 경우 Authenticode `WinVerifyTrust` | 실패 시 캐시 파기 + 서버 보고 |
| install | MSI: `msiexec /i <pkg> /quiet /norestart /l*v install.log MSIIINSTALLPERUSER=1` <br> EXE: 매니페스트 `installer_silent_args` 사용 <br> Script: `pwsh -ExecutionPolicy Bypass -File <ps1>` (서명 정책에 따라) | exit code != 0 / 1641(reboot needed) / 3010(success reboot) 분기 |
| report | `POST /api/v1/agents/installs` (status, exit_code, log_tail 100 lines, duration_ms) | 큐잉(재전송), 로컬 `audit-pending.json` |

### 7.3 매니페스트 확장 (Hub 측)

`installer_packages` 자체 외에 App 매니페스트에 다음 필드를 옵션 추가:

```yaml
windows_install:
  silent_args: "/quiet /norestart"        # MSI 외 EXE 패키지용
  scope: per_user                          # per_user | per_machine
  requires_uac: false
  post_install:
    - shortcut: "%USERPROFILE%\\Desktop\\HEAX Mesher.lnk"
  uninstall:
    product_code: "{8B7C...-...}"          # MSI 경우 자동 추출 가능
  policy:
    requires_signed: true
    minimum_launcher_version: "0.4.0"
```

### 7.4 권한 모델

- 기본은 standard user. **per-user MSI** 를 1차 권장 (`ALLUSERS=""` 또는 `MSIIINSTALLPERUSER=1`).
- per-machine 필요 시 UAC elevation prompt → Rust 측 `ShellExecuteW(verb: runas)` 로 escalated 자식 프로세스 호출. 메인 런처 자체는 standard 권한 유지.
- 정책 위반(서명 미충족 / 최소 버전 미달 / GPO 차단) 은 **사유 카드**로 즉시 거부 표시. 사유 코드는 audit_log 에 기록.

### 7.5 롤백

- Pre-install 시 `wmic product list` 또는 Windows Installer API 로 현재 ProductCode 캡처.
- 실패 시 `msiexec /x <previous>` 시도 + 사용자에게 "이전 버전 유지" 옵션 제공.

### 7.6 미래 대응

- MSIX/AppX 패키지: Phase 4. `Add-AppxPackage -Path ... -ForceApplicationShutdown`. 서명 체인은 사내 PKI.
- winget 통합: 카탈로그 항목이 winget id 만 가질 수 있도록 매니페스트 `winget_id` 필드 예약.

---

## ◇ 8. CLI / 자동화 도구 실행

### 8.1 매니페스트 → 폼 자동 생성

HEAXHub 의 `manifest.inputs` (이미 Job Runner 가 사용) 를 그대로 차용해, react-hook-form + zod 스키마로 변환한다.

```yaml
# manifest 예시
inputs:
  - id: input_dir
    label: 입력 폴더
    type: path
    must_exist: true
  - id: checkpoint
    label: 체크포인트
    type: int
    default: 1000
  - id: use_gpu
    label: GPU 사용
    type: bool
    default: false
```

```ts
// 런타임 zod 생성 (의사 코드)
const schema = z.object({
  input_dir: z.string().refine(fs.existsSync, "경로가 존재해야 합니다"),
  checkpoint: z.number().int().default(1000),
  use_gpu: z.boolean().default(false),
})
```

### 8.2 실행 위치 (라우팅)

| 시나리오 | 위치 | 근거 |
|---|---|---|
| `execution_target = local_pc` | HWAXLauncher (로컬) | 매니페스트가 명시 |
| `execution_target = windows_worker` | HEAXHub (서버 풀의 Windows agent) | 매니페스트가 명시 |
| `execution_target = linux_runner / slurm / apptainer` | HEAXHub Job Runner | 서버 위임 |
| 사용자 명시 토글 | 매니페스트 `local_override_allowed: true` 일 때만 토글 노출 |

### 8.3 실행 흐름

```
[UI 폼 submit]
    │
    ▼
[Command 객체 생성]   ── audit "job.requested"
    │
    ├── 로컬 분기 ──▶ Rust: spawn child (stdout/stderr 라인 → IPC 이벤트)
    │                  └── 종료 시 result.json 업로드 → /api/v1/jobs (post-hoc)
    │
    └── 서버 분기 ──▶ POST /api/v1/jobs → JobId 수령 → /ws/jobs/{id} 구독
                        └── 라인별 로그 스트림 → UI Log Panel
```

### 8.4 로컬 실행 보안

- 실행 가능한 인터프리터 목록은 `App.extra.interpreter` 화이트리스트 (python/pwsh/node/cmd).
- 사용자 입력은 **인자 배열**로만 전달 (셸 보간 금지). Rust 측 `tokio::process::Command::arg()`.
- 작업 디렉터리는 `%LocalAppData%\HWAXLauncher\runs\<job_id>\` sandbox.
- 환경 변수 노출은 매니페스트 `env_passthrough` 키 화이트리스트 외 차단.

---

## ◇ 9. 보안

| 영역 | 정책 |
|---|---|
| 코드 사인 | DigiCert EV (인증서 USB / Azure Key Vault HSM). 매 빌드 timestamp 서명. |
| SmartScreen | EV 인증서 평판 + 사내 도메인 그룹 정책으로 사전 허용. |
| 패키지 무결성 | SHA256 (DB 등록값) + optional Ed25519 signature (`installer_packages.signed=true`). |
| Authenticode | MSI/EXE 의 경우 `WinVerifyTrust` 호출. |
| Tauri allowlist | `fs`, `shell.open`, `process.spawn` 등 모두 화이트리스트 명시. `dangerousRemoteDomainIpcAccess` 비활성. |
| CSP | `default-src 'self'; connect-src 'self' https://hub.heax.local wss://hub.heax.local; img-src 'self' data:` |
| Tauri updater | Ed25519 공개키 빌드 시 임베드. 업데이트 매니페스트는 `installers/latest` JSON. |
| Audit | 모든 install/uninstall/run 이벤트 → `POST /api/v1/agents/audit`. 오프라인 시 큐잉. |
| 토큰 폐기 | 사용자 로그아웃, 디바이스 disable, 30일 무heartbeat → 자동 폐기. |
| 자격 저장 | Windows Credential Manager 외 사용 금지. JSON/.env 평문 저장 절대 금지. |
| 텔레메트리 | 옵트인 only. PII 0건 (machine name 해시화). |

CVE / 의존성:

- `cargo audit`, `pnpm audit` 을 CI 차단 게이트로 둠.
- WebView2 자동 업데이트 활성. Edge 채널 고정.

---

## ◇ 10. 디자인 패턴

### 10.1 Command Pattern

```rust
// Rust 의사 코드
pub trait Command: Send {
    fn id(&self) -> CommandId;
    fn execute(&mut self, ctx: &mut CommandContext) -> CommandResult;
    fn undo(&mut self, ctx: &mut CommandContext) -> CommandResult;
    fn name(&self) -> &str;
}

pub struct InstallCommand { pub package: InstallerPackage, ... }
pub struct RunLocalCommand { pub app: App, pub inputs: Value }
pub struct UninstallCommand { pub product_code: String }
```

- 모든 사용자 액션이 Command. 큐(`tokio::sync::mpsc`)에 들어가 단일 워커가 직렬 처리(설치 동시성 1).
- `undo()` 는 install → uninstall, run → cancel(SIGTERM).

### 10.2 Strategy Pattern (인스톨러 분기)

```rust
pub enum InstallerKind { Msi, Exe, AppX, Script, WingetId }

pub trait InstallStrategy {
    fn install(&self, pkg: &InstallerPackage, opts: &Options) -> InstallResult;
    fn uninstall(&self, key: &UninstallKey) -> InstallResult;
}
```

- `MsiStrategy`, `ExeSilentStrategy`, `AppxStrategy`, `ScriptStrategy`, `WingetStrategy` 등으로 분리.
- 매니페스트의 `installer_type` 로 디스패치.

### 10.3 Repository Pattern (영속화 격리)

```ts
// 프런트 측: Catalog Repository
interface CatalogRepository {
  list(query: CatalogQuery): Promise<App[]>
  get(id: string): Promise<AppDetail>
}

class HttpCatalogRepository implements CatalogRepository { ... }
class CachedCatalogRepository implements CatalogRepository { /* 오프라인 */ }
```

- `InstallHistoryRepository`, `SettingsRepository` 도 동일 패턴.
- 테스트에서는 `FakeRepository` 주입.

### 10.4 Observer Pattern (WebSocket → UI)

- WebSocket 메시지(`app.published` 등)를 단일 `EventBus` 에 게시. UI 는 TanStack Query `queryClient.invalidateQueries()` 와 `useEventBus()` 훅으로 구독.

### 10.5 State Machine (Install Lifecycle)

- XState (또는 ts-state-machines) 로 7.1 의 상태도 직접 구현. 상태가 곧 audit_log 액션이 된다.

### 10.6 그 외

- **Adapter** — `installer_silent_args` 가 vendor 별로 (Inno / NSIS / Squirrel) 다르므로 어댑터.
- **Decorator** — HTTP 클라이언트에 `LoggingMiddleware`, `RetryMiddleware`, `AuthMiddleware` 데코레이션.
- **Factory** — `CommandFactory::for_app(app, action)` 로 알맞은 Command 생성.

---

## ◇ 11. 폴더 구조

### 11.1 리포지토리 (pnpm workspace)

```
hwax-launcher/
├─ package.json                  # pnpm workspace root
├─ pnpm-workspace.yaml
├─ Cargo.toml                    # rust workspace
├─ Cargo.lock
├─ rustfmt.toml
├─ apps/
│  └─ launcher/                  # Tauri 앱
│     ├─ src-tauri/              # Rust core
│     │  ├─ Cargo.toml
│     │  ├─ tauri.conf.json
│     │  ├─ build.rs
│     │  ├─ icons/
│     │  ├─ resources/
│     │  └─ src/
│     │     ├─ main.rs
│     │     ├─ commands/
│     │     │  ├─ install.rs
│     │     │  ├─ run_local.rs
│     │     │  ├─ uninstall.rs
│     │     │  └─ catalog.rs
│     │     ├─ strategies/
│     │     │  ├─ msi.rs
│     │     │  ├─ exe.rs
│     │     │  ├─ appx.rs
│     │     │  └─ script.rs
│     │     ├─ repo/
│     │     │  ├─ catalog_sqlite.rs
│     │     │  └─ settings.rs
│     │     ├─ http.rs
│     │     ├─ ws.rs
│     │     ├─ keychain.rs
│     │     ├─ state_machine.rs
│     │     └─ updater.rs
│     └─ src/                    # React UI
│        ├─ main.tsx
│        ├─ App.tsx
│        ├─ routes/
│        │  ├─ __root.tsx
│        │  ├─ catalog/
│        │  ├─ my-pc/
│        │  ├─ automation/
│        │  └─ settings/
│        ├─ features/
│        │  ├─ install/
│        │  ├─ catalog/
│        │  └─ auth/
│        ├─ components/
│        ├─ lib/
│        │  ├─ ipc.ts            # tauri invoke wrappers
│        │  ├─ http.ts
│        │  ├─ ws.ts
│        │  └─ telemetry.ts
│        ├─ stores/
│        ├─ styles/
│        └─ types/
├─ packages/
│  ├─ ui/                        # shadcn/ui 사내 fork (HEAXHub 와 공유)
│  ├─ schemas/                   # zod 매니페스트 스키마
│  └─ icons/
├─ tools/
│  ├─ wix/                       # MSI 빌드 산출물
│  ├─ sign/                      # 코드 사인 스크립트
│  └─ release/
├─ tests/
│  ├─ e2e/                       # Playwright (Tauri webview)
│  └─ rust/                      # cargo test
├─ docs/
│  ├─ architecture.md
│  ├─ runbook.md
│  └─ adr/                       # Architecture Decision Records
└─ .github/  (또는 .jenkins/)
   └─ workflows/
```

### 11.2 런타임 디렉토리 (사용자 PC)

```
%LocalAppData%\HWAXLauncher\
├─ cache\
│  └─ <sha256>.msi               # 다운로드 캐시
├─ runs\
│  └─ <job_id>\                  # 자동화 도구 작업 폴더
├─ logs\
│  └─ launcher-YYYYMMDD.json     # 구조화 JSON, 30일 회전
├─ catalog.db                    # SQLite (apps, install_history)
├─ settings.json                 # 비민감 설정
└─ updates\                      # tauri updater staging
```

자격은 위에 두지 않고 Credential Manager 별도.

---

## ◇ 12. 빌드 / 배포 / 사인

### 12.1 빌드 매트릭스

| 단계 | 명령 | 산출물 |
|---|---|---|
| install | `pnpm install --frozen-lockfile` | node_modules / Cargo deps |
| lint | `pnpm lint && cargo clippy -- -D warnings` | 보고서 |
| test (unit) | `pnpm test && cargo test` | xml report |
| test (e2e) | `pnpm test:e2e` (Playwright) | trace / video |
| build | `pnpm tauri build --bundles msi,nsis,updater` | `.msi`, `.exe`, `.zip` + `.sig` |
| sign | `signtool sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 ...` | 서명된 산출물 |
| publish | `POST /api/v1/apps/hwax_launcher/installers` | HEAXHub installer_packages 레코드 |
| release | `git tag v0.x.y` + GitHub/Jenkins release | release notes |

### 12.2 CI (GitHub Actions 또는 사내 Jenkins)

```yaml
# .github/workflows/release.yml (요약)
jobs:
  build:
    runs-on: windows-2022
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
      - uses: pnpm/action-setup@v3
      - uses: dtolnay/rust-toolchain@stable
      - run: pnpm install --frozen-lockfile
      - run: pnpm tauri build --bundles msi,updater
      - name: Sign
        run: ./tools/sign/sign-msi.ps1
        env:
          AZ_KEY_VAULT: ${{ secrets.AZ_KEY_VAULT }}
      - name: Publish to HEAXHub
        run: pwsh ./tools/release/publish.ps1
        env:
          HEAX_TOKEN: ${{ secrets.HEAX_ADMIN_TOKEN }}
```

### 12.3 Tauri updater 매니페스트

```json
{
  "version": "0.4.0",
  "notes": "Job Runner 위임 지원, Mica 효과 추가",
  "pub_date": "2026-06-05T00:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "BASE64...",
      "url": "https://hub.heax.local/api/v1/apps/hwax_launcher/installers/windows-x64/0.4.0"
    }
  }
}
```

- 매니페스트 URL: `GET /api/v1/apps/hwax_launcher/updates/latest?os=windows-x64`.
- HEAXHub `installers.py` 에서 `RedirectResponse` 로 install URL 전달, 별도 매니페스트 작성기는 `installer_packages` 의 최신 버전을 조회해 JSON 생성.

### 12.4 사내 배포 채널

| 채널 | 목적 | 업데이트 정책 |
|---|---|---|
| dev | 내부 개발자 | 매 머지 |
| beta | 파일럿 부서 | 매주 금요일 cut |
| stable | 전사 | 격주 화요일 cut, 24h soak |

---

## ◇ 13. HEAXHub 서버 측 변경 작업

| 영역 | 변경 | 비고 |
|---|---|---|
| Schema | `windows_agents` 에 `device_kind`(worker | launcher) 컬럼 추가 | 기존 worker 와 분리 |
| Schema | `windows_agents` 에 `last_heartbeat_meta`(JSONB: os_build, launcher_version, mem_free, disk_free) | UI 가시화용 |
| Endpoint | `POST /api/v1/agents/enroll` | enrollment_token → device_jwt |
| Endpoint | `POST /api/v1/agents/heartbeat` 의 페이로드 확장 | metrics 수신 |
| Endpoint | `POST /api/v1/agents/installs` | 설치 결과 보고 (status, exit_code, log_tail) |
| Endpoint | `POST /api/v1/agents/audit` | 사용자 액션 audit (오프라인 큐 재전송 호환) |
| Endpoint | `GET  /api/v1/apps/{id}/updates/latest` | Tauri updater 매니페스트 JSON |
| WS | `/ws/launcher/{device_id}` | server-push 전용 채널 |
| Manifest | `app_type` 에 `desktop_launcher` 신설 검토 (또는 `windows_gui` 활용) | desktop_launcher 는 “HEAXHub 가 카탈로그하지만 설치 주체는 PC”라는 의미 분리 시 유용 |
| Manifest | `windows_install:` 블록 정식화 (silent_args, scope, requires_uac, post_install, uninstall, policy) | 7.3 참조 |
| Service | installer_packages 다운로드 endpoint 에 `Range` + `ETag` + `Content-MD5` 헤더 정식 지원 | resume / 캐싱 |
| Service | installer presigned URL 옵션 (S3/minio 백엔드 도입 시) | Phase 3+ |
| Audit | 신규 action: `launcher.install`, `launcher.uninstall`, `launcher.run.local`, `launcher.policy.deny` | 기존 audit_service 그대로 |
| Policy | App 매니페스트 `windows_install.policy.minimum_launcher_version` 강제 | 정책 위반 시 deny |

신규 테이블은 최소화한다. `install_history` 는 launcher 가 로컬 SQLite 에만 두고, 서버는 `audit_log` + `windows_agents.last_heartbeat_meta` 로 통합 조회한다.

---

## ◇ 14. 로드맵 (4 Phase)

| Phase | 기간 | 목표 | 산출 | 종료 조건 |
|---|---|---|---|---|
| **Phase 1 — MVP** | 4주 | 카탈로그 + 인증 + per-user MSI 사일런트 설치 | `0.1.x` MSI 배포, 단일 사일런트 인스톨 데모 (HEAX Mesher) | 5명 파일럿 사용, 인스톨 성공률 ≥95% |
| **Phase 2 — Live & CLI** | 4주 | WebSocket 실시간 push, 자동화 CLI 실행 (로컬/서버 라우팅), 매니페스트 폼 자동 생성 | `0.2.x`, CLI 데모 (CMS Batch Runner) | 3개 CLI 앱이 매니페스트만으로 동작 |
| **Phase 3 — Auto Update & Policy** | 3주 | Tauri Ed25519 자동 업데이트, 사내 정책 (서명 강제 / 최소 버전), Authenticode 검증, 우아한 롤백 | `0.3.x`, 정책 위반 거부 화면 | 정책 위반 케이스 100% 차단 + 텔레메트리 옵트인 도입 |
| **Phase 4 — Offline & Telemetry & MSIX** | 4주 | 오프라인 카탈로그 캐시, 익명 텔레메트리 (옵트인), MSIX 지원, mac/linux 베타 빌드 | `1.0.0`, 오프라인 환경 데모 | 사외 노트북에서 오프라인 카탈로그 열람 + MSIX 설치 1건 성공 |

이정표 회의는 Phase 종료 1주 전.

---

## ◇ 15. 미래 확장 (mac / linux)

| 항목 | macOS | Linux |
|---|---|---|
| 런타임 | 동일 (Tauri 2 universal) | 동일 |
| 인스톨러 | `.pkg` (productbuild / installer CLI) | `.deb` / `.rpm` / `.AppImage` |
| 사일런트 설치 | `installer -pkg <pkg> -target CurrentUserHomeDirectory` | `dpkg -i`, `rpm -i`, AppImage 자체 실행 |
| 코드 사인 | Apple Developer ID + notarization | sigstore / 사내 PGP |
| 자격 저장 | Keychain | libsecret (gnome-keyring / kwallet) |
| 자동 업데이트 | Tauri updater (동일) | Tauri updater (동일) |
| 카탈로그 매니페스트 | `installer_packages.os = macos-arm64 / macos-x64` | `linux-x64 / linux-arm64` |
| 우선순위 | Phase 4 | Phase 4 (베타) |

Tauri 의 universal 빌드 덕분에 UI 코드 변경은 거의 없으나, **인스톨 전략(Strategy 패턴 새 구현체)** 와 **자격 저장 어댑터** 가 핵심 추가 작업이다.

---

## ◇ 16. 비기능 요구사항 (NFR)

| 영역 | 목표 |
|---|---|
| Cold start | < 1.2s (Tauri main) |
| 카탈로그 조회 응답 | p95 < 300ms (캐시 hit), p95 < 1.5s (HTTP) |
| 메모리 (idle) | < 180 MB |
| 메모리 (설치 중) | < 350 MB |
| 패키지 다운로드 | 100MB 회선에서 100MB 패키지 < 12s |
| 사일런트 설치 성공률 | ≥ 98% (Phase 3) |
| 오류 보고 도달률 | ≥ 99% (오프라인 큐 포함) |
| 로그 보존 | 30일 회전, 압축 후 사내 보관 |
| 접근성 | WCAG 2.1 AA (키보드 only / 스크린리더 라벨) |

---

## ◇ 17. 개발자 경험 (DX)

| 항목 | 도구 |
|---|---|
| 모노레포 | pnpm workspace + cargo workspace |
| 코드 포맷 | Prettier + ESLint + rustfmt + clippy |
| 타입 | TypeScript strict + zod runtime |
| IPC 타입 안전 | `tauri-specta` 로 Rust ↔ TS 타입 자동 생성 |
| Storybook | `packages/ui` 공유 컴포넌트 카탈로그 (HEAXHub 와 미러) |
| Playwright | Tauri webview 대상 e2e (headless 가능) |
| Rust 테스트 | `cargo test` + `cargo-nextest` |
| CI | GitHub Actions (사내 self-hosted runner: windows-2022) 또는 Jenkins |
| 배포 자동화 | `tools/release/publish.ps1` (HEAXHub `POST /apps/.../installers`) |
| ADR | `docs/adr/0001-tauri-2.md` 부터 누적 |
| 라이브 데모 | `pnpm dev` (Vite HMR + Tauri dev) |
| 시드 데이터 | `tools/seed/` — 가짜 HEAXHub 응답 fixtures |

---

## ◇ 18. 리스크 / 미해결 사항

| ID | 리스크 | 영향 | 완화 |
|---|---|---|---|
| R1 | Tauri 2 플러그인 버전 fragmentation | 중 | 사내 npm/cargo 미러 + lockfile, Plan B WPF 보존 |
| R2 | SmartScreen 평판 누적 시간 | 중 | EV 사인 + 그룹 정책 사전 허용 |
| R3 | per-user MSI 미지원 벤더 | 중 | per-machine UAC 분기 + 정책 표시 |
| R4 | WebSocket NAT/Proxy 환경 차단 | 중 | long-poll fallback |
| R5 | Credential Manager 가 도메인 PC 에서 정책 차단되는 경우 | 저 | DPAPI fallback (사용자 scope) |
| R6 | Rust 인력 풀 부족 | 중 | "얇은 Rust" 가이드, 핵심 모듈 페어 코딩 |
| R7 | HEAXHub `windows_agents` 가 worker 모델 — launcher 와 의미 혼선 | 저 | `device_kind` 컬럼 분리 (13장 참조) |
| R8 | 로컬 자동화 도구의 임의 코드 실행 위험 | 고 | 인터프리터 화이트리스트 + 인자 배열 전달 + audit |

미해결 (Phase 1 KO 전 결정):

- 인증서: DigiCert EV vs 사내 PKI 단독
- 텔레메트리 수집 동의 절차 (옵트인 UI 위치)
- macOS/Linux Phase 4 범위 확정

---

## ◇ 19. 마이그레이션 (기존 사용자 / Hub 측)

1. `windows_agents` 마이그레이션
   - `device_kind` 컬럼 추가, 기본값 `worker` 로 채워 기존 워커 영향 없도록 함.
   - launcher 등록은 신규 endpoint 로만 진입.
2. 기존 windows_gui 앱
   - manifest 에 `windows_install:` 블록 없으면 launcher 가 "수동 설치 안내" 카드만 노출 (다운로드 + 가이드 텍스트).
3. 정책 전환
   - Phase 3 부터 `requires_signed: true` 가 기본값. Phase 1-2 동안 매니페스트 lint 경고 발송.

---

## ◇ 20. 부록

### 20.1 참고 링크

- Tauri 2 공식 문서 — `https://v2.tauri.app/`
- Tauri Updater (Ed25519) — `https://v2.tauri.app/plugin/updater/`
- WiX Toolset v4 — `https://wixtoolset.org/docs/v4/`
- Windows Installer Per-User Installs — `https://learn.microsoft.com/windows/win32/msi/single-package-authoring`
- MSIX Packaging Tool — `https://learn.microsoft.com/windows/msix/`
- Authenticode `WinVerifyTrust` — `https://learn.microsoft.com/windows/win32/api/wintrust/`
- Mica / window-vibrancy — `https://github.com/tauri-apps/window-vibrancy`
- Windows Credential Manager (wincred) — `https://docs.rs/wincred/`
- TanStack Router — `https://tanstack.com/router`
- TanStack Query — `https://tanstack.com/query`
- shadcn/ui — `https://ui.shadcn.com/`
- HEAXHub 내부 문서 — `docs/MANIFEST_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/CAPABILITY_MATRIX.md`

### 20.2 용어집

| 용어 | 정의 |
|---|---|
| HWAX | HEAXHub 의 Windows desktop 패밀리 코드네임 |
| Launcher | 본 문서 대상 — 사용자 PC 의 런처/에이전트 |
| Worker Agent | 기존 HEAXHub `windows_agents` 의 서버측 잡 워커 (HWAX 와 별개) |
| Enrollment Token | 디바이스 1회 등록용 단발성 토큰 |
| Device JWT | 디바이스 단위 장기 Refresh + 단기 Access JWT |
| Per-user MSI | `MSIIINSTALLPERUSER=1` 로 HKCU/사용자 폴더에 설치하는 MSI |
| Mica | Windows 11 의 반투명 배경 효과 |

### 20.3 Manifest 확장 예 (전체)

```yaml
schema_version: 2
id: heax_mesher
name: HEAX Mesher
version: 3.4.1
app_type: windows_gui
execution_target: local_pc
description: |
  HEAX 사내 메싱 GUI. 윈도우 데스크탑 전용.
permissions:
  visibility: company
windows_install:
  installer_type: msi
  silent_args: "/quiet /norestart"
  scope: per_user
  requires_uac: false
  post_install:
    - shortcut: "%USERPROFILE%\\Desktop\\HEAX Mesher.lnk"
  uninstall:
    product_code: "{8B7CDEAD-...-...}"
  policy:
    requires_signed: true
    minimum_launcher_version: "0.3.0"
launch:
  mode: local_executable
  exec: "%LocalAppData%\\HEAX\\Mesher\\mesher.exe"
```

---

> 본 문서는 v0.1 초안이며, Phase 1 킥오프 전 ADR 0001(UI 프레임워크), ADR 0002(인증 모델), ADR 0003(인스톨 전략) 으로 분기 추적한다.
