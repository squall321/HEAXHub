# HWAXLauncher — Windows Desktop Launcher 기획서 (WinUI 3 안)

> HEAXHub 자매 프로젝트. Tauri 2 안(`docs/hwax-launcher-plan.md`)과 동일 범위·동일 깊이로,
> **WinUI 3 + .NET 8 + Windows App SDK** 스택으로 재해석한 두 번째 기획서.

| 항목 | 내용 |
|---|---|
| 문서 버전 | v0.1 (초안, Tauri 안 대조용) |
| 대상 OS | Windows 10 19041+ / Windows 11 22H2+ (x64, arm64는 Phase 4) |
| 모기지 시스템 | HEAXHub (FastAPI :4040, Caddy :4180, React 18) |
| 통신 프로토콜 | HTTPS REST + SignalR/WebSocket |
| 추천 스택 | **WinUI 3 (Windows App SDK 1.6) + .NET 8 + C# 12 + CommunityToolkit.Mvvm + CommunityToolkit.WinUI** |
| 배포 형식 | MSIX (per-user) + AppInstaller 자동 업데이트 1차, 폴백 MSI/Zip portable |
| 코드 사인 | DigiCert EV (Azure Key Vault HSM 보관) |
| 작성자 | HEAXHub Platform Team |

---

## ◇ 1. 한 줄 요약

> **WinUI 3 + .NET 8 + Windows App SDK 1.6 + C# 12 + Community Toolkit + Win11 Fluent Design System** 으로 간다.
>
> 이유 3줄:
> 1. **디자인 자유도 + Win11 네이티브 룩의 교집합** — Mica/Acrylic, ConnectedAnimation, ThemeTransitions 가 OS 1급 컴포넌트라 HEAXHub 다크+amber 톤을 살리면서 Win11 시스템 감각도 잃지 않는다.
> 2. **사내 .NET 자산 재사용** — NuGet 라이브러리, MSAL, EF Core, 기존 WPF/WinForms 컨트롤(`WindowsXamlHost`)이 즉시 동작.
> 3. **MSIX + AppInstaller**가 Windows 1급 배포 채널 — 자동 업데이트·롤백·서명 검증 인프라를 별도 구축할 필요가 없다.

---

## ◇ 2. 요약 (Executive Summary)

HEAXHub 가 카탈로그·인증·매니페스트·인스톨러 저장소를 책임지고, 윈도우 워크스테이션에서는 데스크탑 런처가 **검색 → 설치 → 실행 → 보고**의 라이프사이클을 담당하는 분리된 두 컴포넌트 모델은 Tauri 안과 동일. UI 프레임워크 결정만 다르다.

핵심 의사결정 — 사용자 우선순위 3가지에 1:1 매핑:

1. **디자인 자유도**: XAML + ResourceDictionary 로 Fluent 시스템 토큰(`AccentFillColorDefaultBrush`, `LayerFillColorDefaultBrush`, `ControlCornerRadius`)을 HEAXHub 의 다크·amber·둥근 카드에 override. framer-motion 수준 전환은 **ConnectedAnimation + ImplicitAnimations + ThemeTransitions** 조합.
2. **Win11 네이티브 룩 (Fluent/Mica)**: `MicaBackdrop` / `DesktopAcrylicBackdrop` 가 OS 합성기 레벨. NavigationView/InfoBar/TeachingTip 가 Win11 시스템 컴포넌트와 시각·동작 1:1.
3. **사내 .NET 자산 재사용**: NuGet 그대로(MSAL/EF Core/Serilog/Polly/Refit), 기존 WPF 컨트롤은 `WindowsXamlHost`, WinForms 도 호스팅 가능.

추가 결정:

- **모델 재사용**: `windows_agents`, `installer_packages`, `App.app_type=windows_gui` 가 이미 정의되어 신규 테이블 추가 최소화 (Tauri 안과 동일).
- **설치 트랜잭션**: State Machine `queued → downloading → verifying → preparing → installing → registered → completed / failed / rolled_back`.
- **PyInstaller exe / MSI / Zip portable 3종을 1급 시민** — Strategy Pattern `IInstallerStrategy` 4 구현체(MSI/EXE/ZIP/MSIX).
- **자동 업데이트**: MSIX + AppInstaller 1차. 자체 폴링은 폴백.
- **로드맵 4 Phase**: MVP → CLI·SignalR → MSIX·정책 → 오프라인·텔레메트리·i18n.

강점 3가지:

- Fluent 시스템 컴포넌트가 OS 1급이라 Win11 감각이 자연스럽고, 모션도 ConnectedAnimation/ImplicitAnimations 로 framer-motion 등가.
- NuGet 생태계 + 사내 .NET 자산을 즉시 사용. MSAL/EF Core/Serilog/Polly 가 검증된 채로 들어온다.
- MSIX + AppInstaller 가 Windows 표준이라 사용자 권한 없는 자동 업데이트, App Container 격리, 무결성 검증이 무료.

약점 2가지(솔직히):

- **Windows 10/11 한정** — mac/Linux 가 미래 요건이 되면 Avalonia 11 마이그레이션 또는 별도 Tauri 클라이언트 신규 개발이 필요.
- **HEAXHub 프런트 React 코드는 직접 재사용 불가** — 컴포넌트는 XAML 로 재작성. 디자인 토큰만 공유. WebView2 임베드는 일부 페이지 한정.

Tauri 안과의 차별점:

| 축 | Tauri 2 안 | WinUI 3 안 (본 문서) |
|---|---|---|
| UI 코드 공유 | HEAXHub React 직접 재활용 | XAML 재작성, 디자인 토큰만 공유 |
| Windows 통합 | Rust + windows-rs (간접) | 네이티브 Windows App SDK (직접) |
| 사내 .NET 자산 | 재사용 어려움 | NuGet/MSAL/EF Core 즉시 재사용 |
| 배포/업데이트 | Tauri updater (Ed25519) + MSI/NSIS | MSIX + AppInstaller 1차 |
| 메모리 (idle) | 80~150MB | 120~180MB |
| 패키지 크기 | ~10MB | ~50MB (self-c) / ~5MB (FW dep) |
| mac/Linux 확장 | universal 빌드 | 불가, Avalonia/Tauri 신규 필요 |

---

## ◇ 3. 시스템 컨텍스트 (Context Diagram)

```
                ┌─────────────────────────────────────────────┐
                │                HEAXHub Server                │
                │  FastAPI:4040   Celery   Postgres            │
                │  apps / users / installer_packages           │
                │  windows_agents (device_kind=launcher)       │
                └──────────────┬──────────────────────────────┘
                               │ HTTPS (REST + SignalR/WS)
                               │ enrollment + device JWT
                ┌──────────────┴──────────────────────────────┐
                │            Windows Workstation               │
                │  ┌────────────────────────────────────────┐  │
                │  │  HWAXLauncher.exe (WinUI 3 / .NET 8)   │  │
                │  │   XAML UI · MicaBackdrop · Navigation  │  │
                │  │      ▲                                  │  │
                │  │      │ MVVM (CommunityToolkit)           │  │
                │  │   ViewModels / Services / DI            │  │
                │  │      │                                  │  │
                │  │   Native Bridges                        │  │
                │  │    msiexec  ·  Process.Start            │  │
                │  │    PackageManager.AddPackageAsync       │  │
                │  │    PasswordVault (Credential Mgr)       │  │
                │  └────────────────────────────────────────┘  │
                │  %LocalAppData%\HWAXLauncher\                │
                │   ├─ cache\   (msi/exe/zip/msix + sha256)    │
                │   ├─ logs\    (Serilog rolling)              │
                │   ├─ cache.db (EF Core SQLite)               │
                │   └─ Credential Manager alias                │
                └──────────────────────────────────────────────┘
```

---

## ◇ 4. UI 프레임워크 비교 및 선택

### 4.1 비교표

| 항목 | **WinUI 3** | Tauri 2 | WPF + ModernWpf | Avalonia 11 |
|---|---|---|---|---|
| 바이너리 크기 | ~50MB / ~5MB FW-dep | ~10MB | ~30MB FW-dep | ~40MB |
| 메모리 (idle) | 120~180MB | 80~150MB | 60~120MB | 100~160MB |
| HEAXHub UI 재사용 | ☆ XAML 재작성 | ★★★ React 그대로 | ☆ XAML 재작성 | ☆ XAML 재작성 |
| Win11 Fluent 룩 | ★★★ 1급 (Mica/Acrylic) | ★★ window-vibrancy | ★★ ModernWpf 보정 | ★ 라이브러리 의존 |
| 사내 .NET 자산 재사용 | ★★★ NuGet 그대로 | ☆ FFI 우회 | ★★★ | ★★★ |
| 디자인 자유도 | ★★★ XAML + 토큰 override | ★★★ CSS | ★★ ModernWpf 한계 | ★★★ |
| 모션 (framer-motion 급) | ★★★ ConnectedAnim + Implicit | ★★★ framer-motion | ★★ Storyboard 수동 | ★★ |
| 자동 업데이트 | ★★★ MSIX AppInstaller | ★★★ 내장 (Ed25519) | ★ ClickOnce/커스텀 | ★ 자체 구현 |
| 코드 사인 워크플로 | 매우 성숙 | 성숙 | 가장 성숙 | 성숙 |
| 도메인 정책(GPO) 친화도 | ★★★ | ★★ | ★★★ | ★★ |
| mac/Linux 확장 | ✗ | ★★★ universal | ✗ | ★★★ 1급 |
| 보안 기본값 | MSIX App Container | CSP·allowlist | 신뢰 가정 | 신뢰 가정 |

### 4.2 결정: **WinUI 3**

사용자 우선순위 3가지와의 매핑:

| 우선순위 | WinUI 3 의 해결 방식 |
|---|---|
| 디자인 자유도 (다크+amber, framer-motion 수준) | ResourceDictionary 토큰 override + ConnectedAnimation/ImplicitAnimations/ThemeTransitions (OS 합성기 가속) |
| Win11 네이티브 룩 (Fluent/Mica) | `MicaBackdrop` + Fluent 시스템 컨트롤 (NavigationView/InfoBar/TeachingTip 등) |
| 사내 .NET 자산 재사용 | NuGet 그대로(MSAL/EF Core/Serilog/Polly/Refit) + `WindowsXamlHost` 로 WPF 임베드 |

### 4.3 차선책

- **Plan B**: WPF + ModernWpf — 사내 .NET 인력 풀이 가장 깊다. Mica 가 라이브러리 보정인 점이 약점.
- **Plan C**: Avalonia 11 — mac/Linux 동시 요건이 1순위가 되면 즉시 검토. XAML 호환성 ~70%.
- **Plan D**: Tauri 2 (자매 안) — HEAXHub React 재사용이 결정적일 때.

### 4.4 솔직한 단점 인정

1. **Windows 10/11 한정** — 구형 도메인 PC 에서 부트스트래퍼가 막힐 수 있어 사내 베이스 이미지 정책으로 보완.
2. **HEAXHub React 컴포넌트 직접 재사용 불가** — 디자인 토큰(색상/폰트/간격/radius)만 공유. 매뉴얼·릴리스 노트 등 일부 페이지는 WebView2 임베드로 절충.
3. **XAML 학습 곡선** — React/CSS 익숙한 인력에게 진입 장벽. 디자이너+개발자 분리, 공통 토큰 사전 정의로 완화.

---

## ◇ 5. 프런트엔드 / UI 상세

### 5.1 UI 컴포넌트 스택

| 영역 | 선택 | 이유 |
|---|---|---|
| UI 런타임 | WinUI 3 (Windows App SDK 1.6+) | OS 1급 Fluent |
| 언어/런타임 | C# 12 + .NET 8 LTS | LTS, async, NuGet |
| 컨트롤 | Microsoft.UI.Xaml.Controls | NavigationView, InfoBar, ProgressRing, TeachingTip, ContentDialog, SelectorBar, InfoBadge |
| Toolkit | CommunityToolkit.WinUI.* | Behaviors, Converters, AnimatedIcon, SettingsCard/SettingsExpander |
| MVVM | CommunityToolkit.Mvvm | ObservableObject, RelayCommand, Messenger |
| DI / Host | Microsoft.Extensions.{DI,Hosting} | 표준 |
| 폼 | XAML + DataTemplateSelector | 매니페스트 inputs → 자동 폼 |
| 폰트 | Pretendard Variable (UI), JetBrains Mono (코드) | HEAXHub 동일 |
| 아이콘 | Segoe Fluent Icons + lucide-svg 사내 fork | 시스템 + 카테고리 통일 |
| i18n | Microsoft.Extensions.Localization + .resw | ko/en |

다크 모드 기본, 사용자 override 가능. **이모지는 카테고리 아이콘(■ ▶ ◇ ◆) 외 사용하지 않는다.**

### 5.2 Mica / Acrylic 백드롭

```csharp
// MainWindow.xaml.cs
this.SystemBackdrop = MicaController.IsSupported()
    ? new MicaBackdrop { Kind = MicaKind.BaseAlt }
    : DesktopAcrylicController.IsSupported()
        ? new DesktopAcrylicBackdrop()
        : null;   // Win10 < 19041 → 어두운 솔리드 #0E0F12
this.ExtendsContentIntoTitleBar = true;
```

### 5.3 다크 + amber accent — ResourceDictionary override

HEAXHub 토큰(`#0E0F12`, `#F5A524`, `#1A1B1F`) 을 Fluent 시스템 토큰에 매핑.

```xml
<!-- Themes/HEAXTokens.xaml (Dark) -->
<Color x:Key="HeaxBgBase">#FF0E0F12</Color>
<Color x:Key="HeaxBgLayer">#FF1A1B1F</Color>
<Color x:Key="HeaxAccent">#FFF5A524</Color>

<SolidColorBrush x:Key="AccentFillColorDefaultBrush" Color="{StaticResource HeaxAccent}"/>
<SolidColorBrush x:Key="LayerFillColorDefaultBrush"  Color="{StaticResource HeaxBgLayer}"/>
<SolidColorBrush x:Key="SolidBackgroundFillColorBaseBrush" Color="{StaticResource HeaxBgBase}"/>
<CornerRadius x:Key="ControlCornerRadius">8</CornerRadius>
<CornerRadius x:Key="OverlayCornerRadius">12</CornerRadius>
```

폰트 임베드는 `ms-appx:///Assets/Fonts/PretendardVariable.ttf#Pretendard Variable` 형태.

### 5.4 전환 애니메이션 (framer-motion 대안)

| HEAXHub 의도 | WinUI 3 구현 |
|---|---|
| 페이지 전환 슬라이드 | `Frame.Navigate(..., new DrillInNavigationTransitionInfo())` |
| 카드 → 상세 morph | `ConnectedAnimationService.GetForCurrentView().PrepareToAnimate(...)` |
| 리스트 항목 stagger | `ImplicitAnimations` + `RepositionThemeAnimation` |
| 모달 등장 | `ContentDialog` 기본 spring + `ThemeTransitions` |
| Toast / 알림 | `InfoBar` slide-in |

ConnectedAnimation 은 OS 합성기에서 처리되어 60fps 보장. framer-motion 의 spring 등가는 `ThemeAnimations` 기본 곡선(`CubicBezier(0.4,0,0.2,1)`).

### 5.5 다크/라이트 자동 + 사용자 override

```csharp
this.RequestedTheme = settings.ThemeMode switch
{
    "Light" => ApplicationTheme.Light,
    "Dark"  => ApplicationTheme.Dark,
    _       => ApplicationTheme.Dark   // 기본 다크
};
```

시스템 테마 변경은 `UISettings.ColorValuesChanged` 구독.

---

## ◇ 6. MVVM 아키텍처

### 6.1 토대

- **CommunityToolkit.Mvvm 8.x**: `[ObservableProperty]`, `[RelayCommand]`, `ObservableValidator`
- **Microsoft.Extensions.DependencyInjection** + **Hosting** (`IHostedService` 로 설치 큐/SignalR 백그라운드 워커)
- **StrongReferenceMessenger** (CommunityToolkit) — ViewModel 간 느슨한 통신
- 깊어지면 **MediatR** 도입

### 6.2 페이지 라우팅

```csharp
public interface INavigationService
{
    bool Navigate<TVm>(object? parameter = null) where TVm : ObservableObject;
    bool GoBack();
}
// Frame + VM→Page 매핑 dict, DrillInNavigationTransitionInfo 기본
```

### 6.3 ViewModel 예시

```csharp
public partial class CatalogViewModel(ICatalogRepository repo, IMessenger bus)
    : ObservableObject
{
    [ObservableProperty] private string searchQuery = "";
    [ObservableProperty] private ObservableCollection<AppCardDto> items = new();

    [RelayCommand]
    private async Task LoadAsync()
        => Items = new(await repo.ListAsync(new CatalogQuery(SearchQuery)));

    [RelayCommand]
    private void OpenDetail(AppCardDto app)
        => bus.Send(new NavigateMessage(typeof(AppDetailViewModel), app.Id));
}
```

### 6.4 DI 구성 (요약)

```csharp
Host.CreateDefaultBuilder().ConfigureServices((_, s) =>
{
    s.AddSingleton<INavigationService, NavigationService>();
    s.AddSingleton<IMessenger>(StrongReferenceMessenger.Default);
    s.AddSingleton<IAuthService, AuthService>();
    s.AddSingleton<ICatalogRepository, HttpCatalogRepository>();
    s.AddSingleton<IInstallService, InstallService>();
    s.AddSingleton<ISignalRClient, HeaxSignalRClient>();
    s.AddSingleton<ICredentialStore, WindowsCredentialStore>();

    // Installers (Strategy)
    s.AddTransient<IInstallerStrategy, MsiInstaller>();
    s.AddTransient<IInstallerStrategy, ExeInstaller>();
    s.AddTransient<IInstallerStrategy, ZipInstaller>();
    s.AddTransient<IInstallerStrategy, MsixInstaller>();
    s.AddSingleton<InstallerStrategyResolver>();

    // HTTP (Refit + Polly)
    s.AddRefitClient<IHeaxApi>()
     .ConfigureHttpClient(c => c.BaseAddress = new Uri("https://hub.heax.local/"))
     .AddPolicyHandler(Policies.RetryWithBackoff());

    s.AddDbContext<LauncherDbContext>(o => o.UseSqlite($"Data Source={Paths.CacheDb}"));

    // ViewModels + Hosted workers
    s.AddTransient<CatalogViewModel>();
    /* ... */
    s.AddHostedService<InstallQueueWorker>();
    s.AddHostedService<SignalRSubscriberWorker>();
}).Build();
```

---

## ◇ 7. 인증 · 페어링 · 통신 · 오프라인

### 7.1 페어링 (1회용 enrollment)

```
[Admin Web UI]    [HEAXHub API]         [HWAXLauncher]
   create agent ─→ enrollment_token (TTL 10m)
                       │  (QR + 코드)
                       │ ←── /agents/enroll ──── (token + hostname)
                       │ ─── device_jwt ──────→  (Access 1h + Refresh 30d)
                       │ ←── /agents/heartbeat ─
```

신규 endpoint 는 Tauri 안과 동일: `POST /api/v1/agents/enroll`. `WindowsAgent.auth_token_hash` 가 enrollment 토큰 해시 자리. 사용자 JWT + 디바이스 JWT 의 이중 토큰 모델.

### 7.2 HTTP 클라이언트

**Refit + IHttpClientFactory + Polly**. `IHeaxApi` 인터페이스 한 장:

```csharp
public interface IHeaxApi
{
    [Get("/api/v1/apps")]
    Task<List<AppDto>> ListAppsAsync([Query] CatalogQuery q, CancellationToken ct);

    [Get("/api/v1/installers/{id}/download")]
    Task<HttpResponseMessage> DownloadInstallerAsync(string id, CancellationToken ct);

    [Post("/api/v1/agents/installs")]
    Task ReportInstallAsync([Body] InstallReport report, CancellationToken ct);
}
```

Cert pinning 은 `HttpClientHandler.ServerCertificateCustomValidationCallback`. 사내 CA 신뢰는 Windows 시스템 trust store.

### 7.3 토큰 보관 (Windows Credential Manager)

**평문 저장 금지.** WinRT `PasswordVault` 가 표준.

```csharp
public sealed class WindowsCredentialStore : ICredentialStore
{
    private const string Resource = "HWAXLauncher/hub.heax.local";
    private readonly PasswordVault _vault = new();

    public void Save(string scope, string token)
        => _vault.Add(new PasswordCredential(Resource, scope, token));

    public string? Read(string scope)
    {
        try { return _vault.Retrieve(Resource, scope).Password; }
        catch { return null; }
    }

    public void Delete(string scope)
    {
        try { _vault.Remove(_vault.Retrieve(Resource, scope)); } catch { }
    }
}
```

도메인 정책상 `PasswordVault` 막힌 경우 → **DPAPI(`ProtectedData.Protect`)** user-scope 폴백.

### 7.4 실시간 push (SignalR 우선, raw WS 폴백)

HEAXHub 는 `/ws/...` 를 노출. 두 옵션:

- **A. SignalR Hub 신설** — 서버 측 `/hubs/agent` 추가, 클라이언트 `Microsoft.AspNetCore.SignalR.Client` 사용. 재연결·백오프가 SDK 표준화. **권장.**
- **B. raw WebSocket 유지** — `System.Net.WebSockets.ClientWebSocket` 직접 사용.

```csharp
// HeaxSignalRClient (요약)
_hub = new HubConnectionBuilder()
    .WithUrl("https://hub.heax.local/hubs/agent",
        opt => opt.AccessTokenProvider = () => Task.FromResult(_auth.DeviceJwt))
    .WithAutomaticReconnect(new[] { 1, 2, 5, 10, 30 }.Select(TimeSpan.FromSeconds).ToArray())
    .Build();
_hub.On<AppPublished>("AppPublished", e => _bus.Send(new CatalogInvalidated(e.AppId)));
_hub.On<InstallerUploaded>("InstallerUploaded", e => _bus.Send(new InstallerArrived(e)));
_hub.On<PolicyUpdated>("PolicyUpdated", e => _bus.Send(new PolicyChanged(e)));
await _hub.StartAsync(ct);
```

프록시 환경 폴백: long-poll (`GET /api/v1/agents/poll?since=...`, 30s timeout).

### 7.5 오프라인 캐시

- **EF Core + SQLite** at `%LocalAppData%\HWAXLauncher\cache.db`
- 테이블: `Apps`, `InstallerPackages`, `InstallHistory`, `Settings`, `AuditQueue`
- 카탈로그 스냅샷 보관, "오프라인 (마지막 동기화: 14분 전)" InfoBar 표시.
- 인증 만료 + 오프라인: 카탈로그 열람만 허용, 설치/실행 차단.
- `AuditQueue` 가 오프라인 동안 audit 이벤트 큐잉, 복귀 시 batch 전송.

---

## ◇ 8. UI / UX 와이어프레임 4종

### 8.1 정보 구조 (NavigationView 좌측)

```
HWAXLauncher
├─ 카탈로그 (검색·필터·태그)
├─ 내 PC (설치된 앱 / 최근 실행)
├─ 자동화 도구 (CLI)
├─ 알림
└─ 설정 (계정·저장소·네트워크·업데이트·로깅)
```

### 8.2 카탈로그 (메인 셸)

```
┌──────────────────────────────────────────────────────────────────┐
│ HWAXLauncher                                ●online   user ▾  ⚙  │  ← Mica titlebar
├──────┬───────────────────────────────────────────────────────────┤
│      │  [ Search apps...                              ] [ 태그 ▾ ] │
│ ■    │ ───────────────────────────────────────────────────────── │
│ 카탈 │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐      │
│ 로그 │  │ HEAX Mesher  │ │ HEAX Plotter │ │ HEAX CMS Tool│      │
│      │  │ windows_gui  │ │ cli_tool     │ │ windows_gui  │      │
│ ▶    │  │ v3.4.1       │ │ v1.2.0       │ │ v0.9.0  β    │      │
│ 내PC │  │ [설치] [상세] │ │ [실행] [상세] │ │ [업데이트]    │      │
│      │  └──────────────┘ └──────────────┘ └──────────────┘      │
│ ◇    │                                                            │
│ 자동 │                                                            │
│ 화   │                                                            │
│ ◆    │                                                            │
│ 알림 │                                                            │
│ ⚙    │                                                            │
│ 설정 │                                                            │
├──────┴───────────────────────────────────────────────────────────┤
│ 카탈로그 동기화: 방금 전 · 설치 진행: 1건 · 서버: hub.heax.local  │  ← InfoBar
└──────────────────────────────────────────────────────────────────┘
```

좌측 `NavigationView`, 상단 검색은 `AutoSuggestBox`, 카드 그리드는 `ItemsView` + `UniformGridLayout`.

### 8.3 앱 상세 (SelectorBar)

```
┌── HEAX Mesher v3.4.1 ────────────────────────────── [ ← 뒤로 ] ──┐
│  [ 개요 ] [ 실행 ] [ 이력 ] [ 문서 ]            ← SelectorBar      │
│  ─────────────────────────────────────────────────────────────── │
│  ┌────────────────────┐  카테고리 : windows_gui                   │
│  │     (스크린샷)      │  버전     : 3.4.1 (서명됨, 사내 EV)        │
│  │                    │  크기     : 184 MB                        │
│  └────────────────────┘  타입     : MSI (per-user)                │
│                          업데이트  : 14분 전                       │
│                                                                   │
│  설명 / 변경 이력 ...                                              │
│                              [ 설치 ]  [ 즐겨찾기 ]  [ 공유 링크 ] │
└───────────────────────────────────────────────────────────────────┘
```

`ConnectedAnimation` 으로 카드 → 상세 morphing.

### 8.4 설치 진행 (ContentDialog + ProgressBar + 단계별 InfoBar)

```
┌── 설치: HEAX Mesher v3.4.1 ──────────────────────────────────┐
│  버전     : 3.4.1 (서명됨, 사내 EV)                            │
│  크기     : 184 MB                                            │
│  대상     : %LocalAppData%\HEAX\Mesher                        │
│  방식     : per-user MSI (silent)                              │
│                                                               │
│  ▸ 다운로드 ▸ 검증(SHA256) ▸ 준비 ▸ 설치 ▸ 등록                 │
│  ●─────────●─────────●─────────○─────────○                    │
│  [###############---------]  72%  (132 MB / 184 MB)            │
│                                                               │
│  ─ 단계별 InfoBar ───────────────────────────────              │
│  ◆ 다운로드 완료 (184 MB, 7.2s)                                 │
│  ◆ SHA256 일치 (cf3a...8e21)                                   │
│  ◆ Authenticode 서명 검증 OK                                    │
│  ▸ msiexec 진행 중 (per-user, /quiet /norestart)                │
│                                       [ 취소 ]  [ 백그라운드 ] │
└──────────────────────────────────────────────────────────────┘
```

### 8.5 설정

```
┌── 설정 ───────────────────────────────────────── [ ← 뒤로 ] ──┐
│  [ 계정 ]                                                       │
│  로그인 : alice@heax.local           [ 로그아웃 ]                │
│  디바이스 : DESKTOP-AB12CD            [ 등록 해제 ]              │
│                                                                │
│  [ 저장소 ]                                                     │
│  캐시 위치 : %LocalAppData%\HWAXLauncher\cache  [ 변경 ]         │
│  현재 사용량 : 1.2 GB / 5 GB                    [ 비우기 ]       │
│                                                                │
│  [ 네트워크 ]                                                   │
│  서버 주소 : hub.heax.local      mTLS : ◯ 사용 ◉ 사용 안 함      │
│                                                                │
│  [ 업데이트 ]                                                   │
│  채널 : ◉ stable ◯ beta ◯ dev                                  │
│  자동 업데이트 : ◉ 켜기 (MSIX AppInstaller)   [ 지금 확인 ]     │
│                                                                │
│  [ 로깅 ]   레벨: ◉ Information   보관: 30일                    │
└────────────────────────────────────────────────────────────────┘
```

각 섹션은 `CommunityToolkit.WinUI.Controls.SettingsCard` / `SettingsExpander`.

### 8.6 시각 톤

- Win11 Mica (`MicaBackdrop`), Win10 폴백은 어두운 솔리드 `#0E0F12`.
- amber `#F5A524` accent (HEAXHub 동일 토큰), Pretendard / JetBrains Mono.
- 모션: `DrillInNavigationTransitionInfo` 페이지 전환, `ConnectedAnimation` 카드 morph, `InfoBar` 슬라이드 in.

---

## ◇ 9. 설치 워크플로우 (State Machine)

### 9.1 상태 머신

```
queued → downloading → verifying → preparing → installing → registered → completed
            │              │            │            │             │
            └──────────────┴────────────┴────────────┴─────────────┘
                                       │ fail
                                       ▼
                                    failed → rollback() → rolled_back
```

구현은 **Stateless** NuGet 또는 enum + switch. 전이마다 audit_log 발송 + UI InfoBar 갱신.

### 9.2 매니페스트 `windows_install` 확장

PyInstaller exe / MSI / Zip / MSIX 4종을 1급 시민으로:

```yaml
windows_install:
  installer_type: exe        # exe | msi | msix | zip
  silent_args: ["/S", "/SILENT"]
  expected_exit_codes: [0, 3010]
  sha256: "cf3a...8e21"
  install_target: "%LOCALAPPDATA%\\HEAX\\<id>"
  uninstall_registry_key: "HKCU\\Software\\HEAX\\<id>"
  requires_admin: false
  reboot_required_codes: [3010]
  post_install_check:
    executable: "tool.exe"
    args: ["--version"]
  post_install:
    - shortcut: "%USERPROFILE%\\Desktop\\HEAX Tool.lnk"
  policy:
    requires_signed: true
    minimum_launcher_version: "0.3.0"
```

### 9.3 Strategy Pattern (C#)

```csharp
public interface IInstallerStrategy
{
    InstallerKind Kind { get; }
    Task<InstallResult> InstallAsync(InstallContext ctx, IProgress<InstallProgress> p, CancellationToken ct);
    Task<InstallResult> UninstallAsync(UninstallKey key, CancellationToken ct);
}

public sealed class MsiInstaller : IInstallerStrategy
{
    public InstallerKind Kind => InstallerKind.Msi;
    public async Task<InstallResult> InstallAsync(InstallContext ctx, IProgress<InstallProgress> p, CancellationToken ct)
    {
        var psi = new ProcessStartInfo("msiexec.exe") {
            ArgumentList = { "/i", ctx.LocalPath, "/quiet", "/norestart",
                             "/l*v", ctx.InstallLogPath, "MSIINSTALLPERUSER=1" },
            UseShellExecute = false, CreateNoWindow = true,
        };
        using var proc = Process.Start(psi)!;
        await proc.WaitForExitAsync(ct);
        return InstallResult.From(proc.ExitCode, ctx.Manifest.ExpectedExitCodes);
    }
    // Uninstall: msiexec /x {ProductCode} /quiet
}

public sealed class ExeInstaller : IInstallerStrategy { /* Process.Start + silent_args */ }

public sealed class ZipInstaller : IInstallerStrategy
{
    public async Task<InstallResult> InstallAsync(InstallContext ctx, IProgress<InstallProgress> p, CancellationToken ct)
    {
        var target = Environment.ExpandEnvironmentVariables(ctx.Manifest.InstallTarget);
        Directory.CreateDirectory(target);
        ZipFile.ExtractToDirectory(ctx.LocalPath, target, overwriteFiles: true);
        ShortcutFactory.CreateLnk(/* exec, shortcut path */);
        UninstallRegistry.Register(ctx);   // Add/Remove Programs 노출
        return InstallResult.Success();
    }
}

public sealed class MsixInstaller : IInstallerStrategy
{
    public async Task<InstallResult> InstallAsync(InstallContext ctx, IProgress<InstallProgress> p, CancellationToken ct)
    {
        var op = new PackageManager().AddPackageAsync(new Uri(ctx.LocalPath), null, DeploymentOptions.None);
        op.Progress = (_, prog) => p.Report(InstallProgress.Pct((int)prog.percentage));
        var result = await op.AsTask(ct);
        return result.IsRegistered ? InstallResult.Success() : InstallResult.Failed(result.ErrorText);
    }
}
```

`InstallerStrategyResolver` 가 `installer_type` 으로 디스패치.

### 9.4 권한 모델

- 기본 per-user. `requires_admin: true` 일 때만 UAC.
- `app.manifest` 의 `<requestedExecutionLevel level="asInvoker"/>` 로 런처 자체는 standard.
- elevation 필요 시 `ShellExecute(verb:"runas")` 로 elevated child spawn. 메인 런처는 standard 유지.
- 정책 위반은 **사유 카드(InfoBar Error)** 로 즉시 거부, audit_log 기록.

### 9.5 롤백 / install_history

- 로컬 SQLite `install_history`: `Id, AppId, Version, InstallerType, InstalledAt, UninstallKey, BackupPath?, Status`
- Pre-install 시 기존 디렉토리 ZIP 백업. 실패 시 자동 복원 후 `rolled_back` 상태.

### 9.6 PyInstaller exe 특수 처리

- `--onefile` 빌드는 `%TEMP%\_MEIxxxxxx` 해제 → 디스크 여유 < 500MB 사전 경고.
- AV 오탐: 매니페스트 SHA256 + "사내 검증 완료" 배지. Defender Controlled Folder Access 가이드 InfoBar.
- 첫 실행 SmartScreen 경고: 매니페스트에 "최초 실행 시 경고 발생 가능" 명시.

### 9.7 미래 대응

- winget: 매니페스트 `winget_id` 필드 예약, Phase 4 에 `winget install --silent --id <id>` Strategy.
- Chocolatey: 사내 표준이면 Phase 4 추가 검토.

---

## ◇ 10. CLI / 자동화 도구 실행

### 10.1 매니페스트 → XAML 자동 폼

| manifest type | XAML 위젯 |
|---|---|
| `string` | `TextBox` |
| `int` / `float` | `NumberBox` |
| `bool` | `ToggleSwitch` |
| `path` | `Button + FolderPicker.PickFolderAsync` |
| `file` | `Button + FileOpenPicker.PickSingleFileAsync` |
| `enum` | `ComboBox` |
| `secret` | `PasswordBox` |

`DataTemplateSelector` 로 타입별 위젯 선택, 검증은 `DataAnnotations` 또는 `ObservableValidator.ValidateProperty`.

### 10.2 실행 위치 라우팅

| 시나리오 | 위치 | 근거 |
|---|---|---|
| `execution_target = local_pc` | HWAXLauncher (로컬) | 매니페스트 명시 |
| `execution_target = windows_worker` | HEAXHub 풀 Windows agent | 매니페스트 명시 |
| `execution_target = linux_runner / slurm / apptainer` | HEAXHub Job Runner | 서버 위임 |
| 사용자 토글 | `local_override_allowed: true` 일 때만 노출 |

### 10.3 실행 흐름

```
[폼 submit]
   │
   ▼
[Command 객체]   ── audit "job.requested"
   ├── 로컬: Process.Start (stdout/stderr 라인 → IObservable<string> → RichTextBlock)
   │          └── 종료 후 result.json 업로드 → /api/v1/jobs (post-hoc)
   └── 서버: POST /api/v1/jobs → JobId → SignalR /hubs/jobs/{id} 구독
              └── 라인별 로그 스트림 → UI
```

### 10.4 로컬 stdout/stderr 스트림

WPF `FlowDocument` 대안으로 `RichTextBlock` + `Paragraph` + `Run` 동적 추가.

```csharp
var psi = new ProcessStartInfo(job.Executable) {
    RedirectStandardOutput = true, RedirectStandardError = true,
    UseShellExecute = false, WorkingDirectory = job.SandboxDir, CreateNoWindow = true,
};
foreach (var a in job.Args) psi.ArgumentList.Add(a);
using var p = Process.Start(psi)!;
await Task.WhenAll(
    ReadLinesAsync(p.StandardOutput, ct, LogLevel.Info,  sink),
    ReadLinesAsync(p.StandardError,  ct, LogLevel.Error, sink),
    p.WaitForExitAsync(ct));
```

### 10.5 로컬 실행 보안

- 인터프리터 화이트리스트: `python | pwsh | node | cmd`.
- 사용자 입력은 **`ArgumentList`** 로만 전달 (셸 보간 금지).
- Sandbox: `%LocalAppData%\HWAXLauncher\runs\<job_id>\`.
- 환경 변수는 매니페스트 `env_passthrough` 화이트리스트 외 차단.
- 결과 파일은 sandbox 내부, "Open in Explorer" / "서버 업로드" 액션 제공.

---

## ◇ 11. 보안 다층화

| 영역 | 정책 |
|---|---|
| 코드 사인 | DigiCert EV, **Azure Key Vault HSM** 보관, `signtool sign /tr /td sha256 /fd sha256` CI 게이트 |
| SmartScreen | EV 인증서 평판 + 사내 그룹 정책 사전 허용 |
| 패키지 무결성 | SHA256 (DB 등록값) + optional Ed25519/JWT 서명 |
| Authenticode | MSI/EXE 다운로드 후 `WinVerifyTrust` (P/Invoke) |
| MSIX 무결성 | 패키지 서명·매니페스트 검증을 OS 가 자동 처리 |
| App Container | MSIX 설치 시 격리 sandbox (선택) |
| Token 저장 | `PasswordVault` 외 사용 금지. 폴백은 DPAPI user-scope |
| HTTPS | TLS 1.2+, cert pinning 옵션, 사내 CA trust store |
| WebSocket/SignalR | Bearer device_jwt, 토큰 만료 자동 재발급 |
| CSP (WebView2 임베드 시) | `default-src 'self'; connect-src 'self' https://hub.heax.local wss://hub.heax.local` |
| Audit | 모든 install/uninstall/run → `POST /api/v1/agents/audit`. 오프라인 `AuditQueue` 큐잉 |
| 매니페스트 서명 | 서버측 PGP 또는 JWT 서명 → 클라이언트 검증 게이트 |
| 토큰 폐기 | 로그아웃 / 디바이스 disable / 30일 무heartbeat → 자동 폐기 |
| 텔레메트리 | 옵트인 only, PII 0건 (machine name 해시화) |

CVE / 의존성:

- `dotnet list package --vulnerable` CI 차단 게이트.
- WebView2 자동 업데이트 활성 (Edge Evergreen).
- Renovate / Dependabot 으로 NuGet 자동 PR.

코드 사인 인증서 보관: 1차 **Azure Key Vault HSM + Managed Identity + AzureSignTool**, 2차 사내 HSM(YubiHSM2) 오프라인 수동 서명.

---

## ◇ 12. 자동 업데이트

### 12.1 선택지

| 선택 | 설명 | 장점 | 단점 |
|---|---|---|---|
| **A. MSIX + AppInstaller** | `.appinstaller` XML 이 update feed | 사용자 권한 불필요, 무결성/롤백 자동, delta 다운로드, 인프라 0 | Win10 19041+ 한정, MSIX 패키징 환경 필요 |
| B. Squirrel.Windows | .NET 용 NSIS 대안 | per-user, delta, UAC 불필요 | OS 통합 얕음, App Container 없음 |
| C. 자체 구현 | HEAXHub 폴링 + 백그라운드 재시작 | 완전한 제어 | 무결성/롤백/delta 모두 직접 책임 |

### 12.2 `.appinstaller` 예시

```xml
<?xml version="1.0" encoding="utf-8"?>
<AppInstaller Uri="https://hub.heax.local/installers/HWAXLauncher.appinstaller"
              Version="0.4.0"
              xmlns="http://schemas.microsoft.com/appx/appinstaller/2018">
  <MainPackage Name="HeaxHub.HWAXLauncher"
               Publisher="CN=HEAX Engineering, O=HEAX, C=KR"
               Version="0.4.0.0"
               ProcessorArchitecture="x64"
               Uri="https://hub.heax.local/installers/HWAXLauncher_0.4.0_x64.msix"/>
  <UpdateSettings>
    <OnLaunch HoursBetweenUpdateChecks="6" UpdateBlocksActivation="false" ShowPrompt="false"/>
    <AutomaticBackgroundTask/>
  </UpdateSettings>
</AppInstaller>
```

### 12.3 결정

**선택 A (MSIX + AppInstaller)** 채택. 사내 Win10 19041+ 표준 가정 성립. 폴백으로 자체 구현(C)을 베타 채널 옵션 보존.

업데이트 매니페스트 URL: `GET /api/v1/apps/hwax_launcher/updates/latest?os=windows-x64` → HEAXHub 동적 생성.

### 12.4 사내 배포 채널

| 채널 | 목적 | 정책 | AppInstaller URL |
|---|---|---|---|
| dev | 내부 개발자 | 매 머지 | `/installers/HWAXLauncher.dev.appinstaller` |
| beta | 파일럿 부서 | 매주 금요일 cut | `/installers/HWAXLauncher.beta.appinstaller` |
| stable | 전사 | 격주 화요일 cut, 24h soak | `/installers/HWAXLauncher.appinstaller` |

---

## ◇ 13. 디자인 패턴

- **Command** — `IRelayCommand` (CommunityToolkit). 설치 큐는 `Channel<InstallCommand>` 단일 워커 + 동시성 3 슬롯.
- **Strategy** — `IInstallerStrategy` 4 구현체(MSI/EXE/ZIP/MSIX). 9.3 참조.
- **Repository** — `ICatalogRepository`, `IInstallHistoryRepository`, `ISettingsRepository`, `IAuditRepository`. `Http*`, `SqliteCached*`, `Composite*` 구현으로 온라인/오프라인 분기.
- **Observer** — `StrongReferenceMessenger.Default` 가 단일 이벤트 버스. SignalR 수신을 `IMessenger.Send`, ViewModel 이 `IRecipient<T>` 구독.
- **State Machine** — `Stateless` NuGet 또는 enum+switch. 9.1 참조. `OnTransitioned` 에 audit hook.
- **Mediator** — VM 간 통신 깊어지면 **MediatR** 도입.
- **Adapter** — vendor 별 silent_args 차이(Inno/NSIS/Squirrel/WiX) 흡수.
- **Decorator** — `HttpClientHandler` 데코레이션: `LoggingHandler`, `RetryHandler` (Polly), `AuthHandler`.
- **Factory** — `CommandFactory.For(app, action)` 가 적절한 Command 생성.

---

## ◇ 14. 폴더 구조

### 14.1 솔루션

```
HWAXLauncher/
├─ HWAXLauncher.sln
├─ src/
│  ├─ HWAXLauncher.App/                  # WinUI 3 main
│  │  ├─ App.xaml(.cs)                    # IHost + DI
│  │  ├─ MainWindow.xaml(.cs)             # MicaBackdrop
│  │  ├─ Pages/  (Catalog, AppDetail, Install, Automation, Notifications, Settings)
│  │  ├─ ViewModels/
│  │  ├─ Views/  (AppCard, AutoFormControl, InstallProgressDialog, Templates/)
│  │  ├─ Themes/HEAXTokens.xaml           # 색상/토큰 override
│  │  ├─ Converters/
│  │  ├─ Assets/Fonts | Icons | Logo
│  │  ├─ Strings/ko-KR | en-US (.resw)
│  │  ├─ app.manifest                     # asInvoker
│  │  └─ Package.appxmanifest             # MSIX
│  │
│  ├─ HWAXLauncher.Core/                  # .NET class lib
│  │  ├─ Installers/  IInstallerStrategy + Msi/Exe/Zip/Msix + Resolver
│  │  ├─ Services/    IHeaxApi(Refit), AuthService, InstallService(큐+SM),
│  │  │               HeaxSignalRClient, WindowsCredentialStore, AuditService
│  │  ├─ Models/      AppDto, InstallerPackageDto, ManifestDto, InstallReport
│  │  ├─ Persistence/ LauncherDbContext, Entities/, Migrations/
│  │  ├─ StateMachines/InstallStateMachine.cs
│  │  ├─ Policies/    Polly retry/circuit-breaker
│  │  └─ Paths.cs     # %LocalAppData% 헬퍼
│  │
│  └─ HWAXLauncher.Tests/                 # xUnit
│
├─ installer/
│  ├─ msix/ (Package.appxmanifest, HWAXLauncher.appinstaller)
│  ├─ msi/  (WiX 폴백)
│  └─ portable/ (Zip 산출 스크립트)
│
├─ .github/workflows/ (ci.yml, release.yml)
├─ docs/ (architecture, runbook, adr/)
└─ tools/
   ├─ sign/    AzureSignTool 래퍼
   ├─ release/ publish.ps1 (HEAXHub 업로드)
   └─ seed/    가짜 HEAXHub fixtures
```

### 14.2 런타임 디렉토리 (사용자 PC)

```
%LocalAppData%\HWAXLauncher\
├─ cache\<sha256>.{msi|exe|zip|msix}
├─ runs\<job_id>\        # 자동화 sandbox
├─ logs\launcher-YYYYMMDD.json  # Serilog rolling, 30일
├─ cache.db              # EF Core SQLite
├─ settings.json         # 비민감 설정
└─ updates\              # MSIX staging (OS 관리)
```

자격은 Credential Manager 별도 (`HWAXLauncher/hub.heax.local/<scope>`).

---

## ◇ 15. 빌드 / CI / 사인 / 배포

### 15.1 빌드 매트릭스

| 단계 | 명령 | 산출물 |
|---|---|---|
| restore | `dotnet restore` | NuGet |
| lint | `dotnet format --verify-no-changes` + Roslyn analyzers | 보고서 |
| test | `dotnet test --collect "XPlat Code Coverage"` | trx + cobertura |
| publish | `dotnet publish -c Release -r win-x64 --self-contained` | exe + dll |
| MSIX | `msbuild /p:GenerateAppxPackageOnBuild=true /p:UapAppxPackageBuildMode=SideloadOnly` | `.msix` (미서명) |
| sign | `AzureSignTool sign -kvu ... -tr http://timestamp.digicert.com -td sha256` | 서명된 산출물 |
| upload | `POST /api/v1/apps/hwax_launcher/installers` | HEAXHub installer_packages |
| AppInstaller 갱신 | `tools/release/update-appinstaller.ps1` | 새 `.appinstaller` XML |
| release | `git tag v0.x.y` + GitHub release | release notes |

### 15.2 CI (GitHub Actions, windows-latest)

```yaml
jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-dotnet@v4
        with: { dotnet-version: 8.0.x }
      - uses: microsoft/setup-msbuild@v2
      - run: dotnet restore
      - run: dotnet build -c Release
      - run: dotnet test -c Release --no-build
      - name: Build MSIX
        run: msbuild src/HWAXLauncher.App/HWAXLauncher.App.csproj
              /p:Configuration=Release /p:Platform=x64
              /p:GenerateAppxPackageOnBuild=true
              /p:AppxPackageSigningEnabled=false
              /p:UapAppxPackageBuildMode=SideloadOnly
      - name: Sign (Azure Key Vault)
        run: |
          dotnet tool install --global AzureSignTool
          AzureSignTool sign `
            -kvu ${{ secrets.AZ_KV_URL }} -kvc ${{ secrets.AZ_KV_CERT_NAME }} `
            -kvi ${{ secrets.AZ_CLIENT_ID }} -kvs ${{ secrets.AZ_CLIENT_SECRET }} `
            -kvt ${{ secrets.AZ_TENANT_ID }} `
            -tr http://timestamp.digicert.com -td sha256 `
            "AppPackages/HWAXLauncher_0.4.0_x64.msix"
      - name: Publish to HEAXHub
        run: pwsh ./tools/release/publish.ps1
        env: { HEAX_TOKEN: ${{ secrets.HEAX_ADMIN_TOKEN }} }
      - name: Update AppInstaller feed
        run: pwsh ./tools/release/update-appinstaller.ps1
```

### 15.3 산출물 종류

| 종류 | 용도 |
|---|---|
| `.msix` + `.appinstaller` | 1차 배포 채널, 자동 업데이트 (Win10 19041+) |
| `.msi` (WiX 폴백) | 도메인 정책상 MSIX 차단 환경 |
| Portable `.zip` | 데모/오프라인, 사외 검토자 |

---

## ◇ 16. HEAXHub 서버 측 변경 작업

| 영역 | 변경 | 비고 |
|---|---|---|
| Schema | `windows_agents.device_kind`(`worker`\|`launcher`) 추가 | 기존 worker 분리 |
| Schema | `windows_agents.last_heartbeat_meta` (JSONB: os_build, launcher_version, mem_free, disk_free) | UI 가시화 |
| Endpoint | `POST /api/v1/agents/enroll` | enrollment_token → device_jwt |
| Endpoint | `POST /api/v1/agents/heartbeat` 페이로드 확장 | metrics |
| Endpoint | `POST /api/v1/agents/installs` | 설치 결과 보고 |
| Endpoint | `POST /api/v1/agents/audit` | 사용자 액션 audit, 오프라인 큐 호환 |
| Endpoint | `GET  /api/v1/apps/{id}/updates/latest` | AppInstaller XML 또는 JSON |
| Endpoint | `GET  /api/v1/installers/{id}/download` | presigned URL 또는 stream |
| Hub | `/hubs/agent` (SignalR) 또는 raw `/ws/agent/{agent_id}` | server-push |
| Manifest | `windows_install:` 블록 정식화 (9.2) | 신규 |
| Manifest | `app_type=desktop_launcher` 신설 검토 | 또는 windows_gui 활용 |
| Service | installer 다운로드 endpoint `Range`/`ETag`/`Content-MD5` 정식 지원 | resume/캐싱 |
| Service | installer presigned URL 옵션 (S3/minio) | Phase 3+ |
| Audit | 신규 action: `launcher.install`, `launcher.uninstall`, `launcher.run.local`, `launcher.policy.deny` | 기존 audit_service 그대로 |
| Policy | `windows_install.policy.minimum_launcher_version` 강제 | 위반 시 deny |

신규 테이블 최소화. `install_history` 는 launcher 로컬 SQLite, 서버는 `audit_log` + `windows_agents.last_heartbeat_meta` 통합 조회.

---

## ◇ 17. 로드맵 (4 Phase)

| Phase | 기간 | 목표 | 산출 | 종료 조건 |
|---|---|---|---|---|
| **Phase 1 — MVP** | 4주 | 카탈로그 + JWT 인증 + MSI/EXE 사일런트 설치 (3종 1급 시민) | `0.1.x` MSI 폴백, HEAX Mesher 데모 | 5명 파일럿, 인스톨 성공률 ≥95% |
| **Phase 2 — Live & CLI** | 4주 | SignalR 알림, CLI 자동화 (로컬/서버 라우팅), 매니페스트 자동 폼, 설치 이력 | `0.2.x`, CMS Batch Runner 데모 | 3개 CLI 앱 매니페스트만으로 동작 |
| **Phase 3 — MSIX & Policy** | 3주 | MSIX + AppInstaller 자동 업데이트, 사내 정책(allow/block, 서명 강제, 최소 버전), Authenticode 검증, 우아한 롤백 | `0.3.x`, 정책 위반 거부 화면 | 위반 케이스 100% 차단 + 텔레메트리 옵트인 |
| **Phase 4 — Offline & i18n & Telemetry** | 3주 | 오프라인 카탈로그 캐시, 익명 텔레메트리, 다국어 (한/영) | `1.0.0`, 오프라인 환경 데모 | 사외 노트북 오프라인 카탈로그 + i18n |

이정표 회의는 Phase 종료 1주 전.

---

## ◇ 18. mac / Linux 확장 가능성

WinUI 3 는 Windows 한정. 미래 요건 발생 시 세 옵션:

| 옵션 | 설명 | 비용 / 일관성 |
|---|---|---|
| A. **Avalonia 11 마이그레이션** | XAML 호환성 ~70%, ViewModel/Service 거의 그대로 | 중. UI 부분 재작성, 토큰 그대로 |
| B. **별도 Tauri 2 클라이언트 신규 개발** | mac/Linux 전용 새 코드베이스 | 고. 코드 공유 0, 디자인 토큰만 공유 |
| C. **PWA 폴백** | 카탈로그 열람만 (설치/Process.Start 불가) | 저. 기능 제한 |

솔직히 사내가 Windows 단일이면 **deferred**. 1년 내 mac/Linux 가 1순위가 될 가능성이 높다면 처음부터 Plan D (Tauri 2) 검토가 낫다.

---

## ◇ 19. 비기능 요구사항 (NFR)

| 영역 | 목표 |
|---|---|
| 패키지 크기 | ~50MB (self-contained .NET 8) / ~5MB (FW-dep, 사내 사전 설치 가정) |
| 메모리 (idle) | < 180 MB (Tauri 80~150MB 보다 큼, Electron 500MB 보다 훨씬 작음) |
| 메모리 (설치 중) | < 350 MB |
| Cold start | < 2.0s on SSD |
| 카탈로그 조회 응답 | p95 < 300ms (캐시 hit), p95 < 1.5s (HTTP) |
| 패키지 다운로드 | 100MB 회선에서 100MB 패키지 < 12s |
| 동시 설치 슬롯 | 3개 |
| 사일런트 설치 성공률 | ≥ 98% (Phase 3) |
| 오류 보고 도달률 | ≥ 99% (오프라인 큐 포함) |
| 로깅 | Serilog → 일별 롤링 + HEAXHub batch 전송, 30일 |
| 접근성 | WCAG 2.1 AA, Narrator/UI Automation 호환 |

---

## ◇ 20. 개발자 경험 (DX)

| 항목 | 도구 |
|---|---|
| IDE | Visual Studio 2022 17.10+ (WinUI 3 워크로드) |
| Hot Reload | XAML Hot Reload + .NET Hot Reload |
| 라이브 시각화 | Live Visual Tree, Live Property Explorer |
| 컴포넌트 탐색 | WinUI Gallery 앱 (Store) |
| 코드 포맷 | `dotnet format` + Roslyn analyzers + StyleCop |
| 타입 안전 | C# 12 nullable + `<Nullable>enable</Nullable>` |
| 단위 테스트 | xUnit + Moq + FluentAssertions |
| UI/E2E | WinAppDriver + Appium |
| CI | GitHub Actions windows-latest 또는 사내 Jenkins |
| 패키지 관리 | NuGet + Directory.Packages.props (중앙 버전) |
| ADR | `docs/adr/0001-winui3.md` 부터 누적 |
| 로컬 데모 | `dotnet run --project src/HWAXLauncher.App` |
| 시드 데이터 | `tools/seed/` fixtures |

---

## ◇ 21. 리스크 / 완화책

| ID | 리스크 | 영향 | 완화 |
|---|---|---|---|
| R1 | WebView2 미설치 (Win10) | 중 | MSIX 의존성 자동 설치, 폴백 부트스트래퍼 동봉 |
| R2 | EV 인증서 비용·관리 | 중 | Azure Key Vault HSM + CI 자동 서명 |
| R3 | PyInstaller exe AV 오탐 | 중 | SHA256 + "사내 검증 완료" 배지, Defender 가이드 |
| R4 | XAML 학습 곡선 | 중 | 디자이너+개발자 분리, 공통 토큰 사전 정의, Gallery 워크숍 |
| R5 | React 컴포넌트 재사용 불가 | 중 | 디자인 토큰만 공유, WebView2 임베드는 매뉴얼 등 일부 |
| R6 | SmartScreen 평판 누적 시간 | 중 | EV 사인 + 그룹 정책 사전 허용 |
| R7 | per-user MSI 미지원 벤더 | 중 | per-machine UAC 분기 + 정책 표시 |
| R8 | SignalR 차단 환경 | 중 | long-poll fallback |
| R9 | PasswordVault 도메인 차단 | 저 | DPAPI user-scope fallback |
| R10 | `windows_agents` 의미 혼선 | 저 | `device_kind` 컬럼 분리 (16장) |
| R11 | 로컬 자동화 임의 코드 실행 | 고 | 인터프리터 화이트리스트 + 인자 배열 + sandbox + audit |
| R12 | mac/Linux 확장 요청 | 중 | 사전 deferred 명시, 발생 시 Avalonia 또는 Tauri 신규 |

미해결 (Phase 1 KO 전 결정):

- 인증서: DigiCert EV + Azure Key Vault HSM 단독 vs 사내 PKI 병행
- 텔레메트리 옵트인 UI 위치
- MSIX 1차 vs MSI 1차 (도메인 정책 환경 사전 조사)

---

## ◇ 22. 마이그레이션 시나리오 (양방향)

| 방향 | 재사용 가능 | 재작성 필요 | 추정 비용 |
|---|---|---|---|
| **WinUI 3 → Tauri** | Refit 인터페이스, 매니페스트 DTO, State Machine 상태/전이 정의, 디자인 토큰 | XAML UI 100% → React/TSX, ViewModel → React Hook, C# Service → Rust+TS | 6~10주 |
| **Tauri → WinUI 3** | API 호출 패턴, 매니페스트 스키마 일부, 디자인 토큰 | 모든 UI/IPC/Rust 코어 | 10~14주 (추천 X) |

양방향 모두 큼. **첫 결정이 사실상 최종 결정**. 우선순위 3가지에 가장 부합하는 안을 골라야 한다.

기존 사용자 / Hub 측 마이그레이션 (양안 공통):

1. `windows_agents.device_kind` 추가, 기본값 `worker` — 기존 워커 무영향.
2. windows_gui 앱 중 `windows_install:` 블록 없는 것은 "수동 설치 안내" 카드만 노출.
3. Phase 3 부터 `requires_signed: true` 기본값. Phase 1-2 동안 매니페스트 lint 경고 발송.

---

## ◇ 23. 부록

### 23.1 참고 링크

- Windows App SDK — `https://learn.microsoft.com/windows/apps/windows-app-sdk/`
- WinUI 3 — `https://learn.microsoft.com/windows/apps/winui/winui3/`
- WinUI Gallery — `https://aka.ms/winuigallery`
- CommunityToolkit.WinUI — `https://learn.microsoft.com/windows/communitytoolkit/`
- CommunityToolkit.Mvvm — `https://learn.microsoft.com/dotnet/communitytoolkit/mvvm/`
- MSIX Toolkit — `https://learn.microsoft.com/windows/msix/`
- AppInstaller 스펙 — `https://learn.microsoft.com/windows/msix/app-installer/app-installer-file-overview`
- Mica / DesktopAcrylic — `https://learn.microsoft.com/windows/apps/design/style/mica`
- Fluent Design — `https://fluent2.microsoft.design/`
- ConnectedAnimation — `https://learn.microsoft.com/windows/apps/design/motion/connected-animation`
- Refit — `https://reactiveui.github.io/refit/`
- Polly — `https://www.pollydocs.org/`
- Serilog — `https://serilog.net/`
- AzureSignTool — `https://github.com/vcsjones/AzureSignTool`
- Authenticode `WinVerifyTrust` — `https://learn.microsoft.com/windows/win32/api/wintrust/`
- WinAppDriver — `https://github.com/microsoft/WinAppDriver`
- HEAXHub 내부 — `docs/MANIFEST_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/CAPABILITY_MATRIX.md`

### 23.2 용어집

| 용어 | 정의 |
|---|---|
| HWAX | HEAXHub Windows desktop 패밀리 코드네임 |
| Launcher | 본 문서 대상 — 사용자 PC 의 런처/에이전트 |
| Worker Agent | 기존 HEAXHub 의 서버측 잡 워커 (HWAX 와 별개) |
| Windows App SDK | Win32 + UWP 통합 SDK (구 Project Reunion) |
| WinUI 3 | Windows App SDK 의 최신 UI 프레임워크 |
| MSIX | Windows 차세대 패키지 포맷 (서명/격리/델타) |
| MSI | Windows Installer 전통 포맷 |
| AppInstaller | MSIX 의 업데이트 feed XML |
| Mica | Win11 의 반투명 시스템 배경 효과 |
| ConnectedAnimation | 페이지 간 요소 morphing (OS 합성기) |
| Enrollment Token | 디바이스 1회 등록용 단발성 토큰 |
| Device JWT | 디바이스 단위 장기 Refresh + 단기 Access JWT |
| Per-user MSI | `MSIINSTALLPERUSER=1` HKCU/사용자 폴더 설치 |

### 23.3 Manifest 확장 예 (3종 발췌)

#### MSI per-user (heax-mesher.yaml)

```yaml
schema_version: 2
id: heax_mesher
name: HEAX Mesher
version: 3.4.1
app_type: windows_gui
execution_target: local_pc
permissions: { visibility: company }
windows_install:
  installer_type: msi
  silent_args: ["/quiet", "/norestart"]
  expected_exit_codes: [0, 3010]
  sha256: "cf3a...8e21"
  install_target: "%LOCALAPPDATA%\\HEAX\\Mesher"
  uninstall_registry_key: "HKCU\\Software\\HEAX\\Mesher"
  requires_admin: false
  reboot_required_codes: [3010]
  post_install:
    - shortcut: "%USERPROFILE%\\Desktop\\HEAX Mesher.lnk"
  post_install_check: { executable: "mesher.exe", args: ["--version"] }
  policy: { requires_signed: true, minimum_launcher_version: "0.3.0" }
launch: { mode: local_executable, exec: "%LOCALAPPDATA%\\HEAX\\Mesher\\mesher.exe" }
```

#### PyInstaller EXE (heax-cms-tool.yaml)

```yaml
schema_version: 2
id: heax_cms_tool
name: HEAX CMS Tool
version: 0.9.0
app_type: windows_gui
execution_target: local_pc
windows_install:
  installer_type: exe
  silent_args: ["/S"]
  expected_exit_codes: [0]
  sha256: "a17b...0c5d"
  install_target: "%LOCALAPPDATA%\\HEAX\\CMSTool"
  uninstall_registry_key: "HKCU\\Software\\HEAX\\CMSTool"
  requires_admin: false
  notes: |
    PyInstaller --onefile. 첫 실행 시 %TEMP%\_MEIxxxx 해제.
    Defender Controlled Folder Access 환경은 사용자 가이드 필요.
  post_install_check: { executable: "cmstool.exe", args: ["--version"] }
  policy: { requires_signed: true }
launch: { mode: local_executable, exec: "%LOCALAPPDATA%\\HEAX\\CMSTool\\cmstool.exe" }
```

#### Zip Portable (heax-plotter.yaml)

```yaml
schema_version: 2
id: heax_plotter
name: HEAX Plotter
version: 1.2.0
app_type: windows_gui
execution_target: local_pc
windows_install:
  installer_type: zip
  expected_exit_codes: [0]
  sha256: "9f4e...b2c1"
  install_target: "%LOCALAPPDATA%\\HEAX\\Plotter"
  uninstall_registry_key: "HKCU\\Software\\HEAX\\Plotter"
  requires_admin: false
  post_install:
    - shortcut: "%USERPROFILE%\\Desktop\\HEAX Plotter.lnk"
    - shortcut: "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\HEAX\\Plotter.lnk"
  post_install_check: { executable: "plotter.exe", args: ["--version"] }
  policy: { requires_signed: false }
launch: { mode: local_executable, exec: "%LOCALAPPDATA%\\HEAX\\Plotter\\plotter.exe" }
```

---

> 본 문서는 v0.1 초안이며, Phase 1 킥오프 전 ADR 0001(UI 프레임워크), ADR 0002(인증 모델), ADR 0003(인스톨 전략), ADR 0004(MSIX vs MSI 1차) 로 분기 추적한다.
>
> 자매 문서: `docs/hwax-launcher-plan.md` (Tauri 2 안) — 최종 결정 시 두 문서를 나란히 두고 비교.
