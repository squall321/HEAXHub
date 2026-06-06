# HEAXHub Documentation Index

본 디렉터리의 문서를 읽기 전에 **이 README 를 먼저 읽으십시오**.
어떤 문서가 단일 진실이고 어떤 문서가 결정 이력 보존용인지 명확히 표시되어 있습니다.

---

## HWAXAgent (Windows 트레이 런처) — 어떤 문서를 보아야 하는가

### 단일 진실 (Source of Truth) — **이것만 구현하십시오**

| 문서 | 역할 |
| --- | --- |
| [hwax-launcher-plan-v2.md](hwax-launcher-plan-v2.md) | **메인 청사진**. 스택·UI·모듈 라이프사이클·보안. **여기에 없는 결정은 무시**하십시오. |
| [hwax-agent-split-strategy.md](hwax-agent-split-strategy.md) | HEAXHub ↔ HWAXAgent 책임 분담 + PR 양방향 협업 흐름 |
| [hwax-agent-backend-plan.md](hwax-agent-backend-plan.md) | HEAXHub 서버측 endpoint·migration·service 작업 계획 |
| [hwax-agent-pr-protocol.md](hwax-agent-pr-protocol.md) | 협업 시나리오 4종 + 라벨/CODEOWNERS/CI |
| [hwax-agent-e2e-example.md](hwax-agent-e2e-example.md) | Koo Preprocessor 가상 시나리오 — 매니페스트→설치→audit 전과정 |
| `../contracts/hwax-agent/` | JSON Schema + OpenAPI + 디자인 토큰. **단일 진실** — schema 위반 PR 은 머지 금지. |

### 확정된 스택

- **Tauri 2 (Rust core) + React 18 + TypeScript + Vite + Tailwind**
- 다음 스택은 **명시적으로 탈락**, 절대 사용 금지:
  - WinUI 3 / WPF / .NET 8 / .NET Framework / C# / XAML / MAUI / Avalonia
  - Electron / Flutter Desktop

### 결정 이력 보존 — **참조만, 구현 금지**

| 문서 | 상태 | 비고 |
| --- | --- | --- |
| [archive/hwax-launcher-plan-v1-superseded.md](archive/hwax-launcher-plan-v1-superseded.md) | **SUPERSEDED** | 1차 Tauri 안. "패널 카탈로그" 방향. v2 로 재초점화됨. |
| [archive/hwax-launcher-plan-winui3-deprecated.md](archive/hwax-launcher-plan-winui3-deprecated.md) | **DEPRECATED** | WinUI 3 대안. 결정 과정 비교용으로만 보존. 구현 금지. |

> 위 두 문서 안의 어떤 결정도 v2 와 충돌 시 **v2 우선**. 헷갈리면 항상 v2.

---

## HEAXHub (서버) — 운영/아키텍처/API

| 문서 | 역할 |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 전체 시스템 아키텍처 |
| [API_REFERENCE.md](API_REFERENCE.md) | API 레퍼런스 |
| [CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md) | 기능 매트릭스 |
| [MANIFEST_SPEC.md](MANIFEST_SPEC.md) | 매니페스트 스펙 |
| [RUNBOOK.md](RUNBOOK.md) | 운영 런북 |
| [CHANGE_REQUEST_DESIGN.md](CHANGE_REQUEST_DESIGN.md) | 변경 요청 설계 |
| [OFFLINE_DEPLOY.md](OFFLINE_DEPLOY.md) | 오프라인 배포 |
| [SECRET_ROTATION.md](SECRET_ROTATION.md) | 시크릿 로테이션 |
| [INTERPRETERS.md](INTERPRETERS.md) | 인터프리터 (스택 빌더) |

---

## LLM/Agent 가 본 디렉터리에서 작업할 때의 규칙

1. 새 작업을 시작하기 전에 **본 README 를 먼저 읽으십시오**.
2. HWAXAgent 관련 결정은 **[hwax-launcher-plan-v2.md](hwax-launcher-plan-v2.md) 만이 단일 진실** 입니다.
3. v2 에 없거나 v2 와 충돌하는 결정은 **이전 안의 잔재**입니다. 무시하십시오.
4. **WinUI 3 / .NET / C# 코드를 생성하지 마십시오.** Tauri 2 + Rust + React 만 사용하십시오.
5. 계약 변경은 `contracts/hwax-agent/` 의 JSON Schema 와 OpenAPI 를 직접 수정하는 PR 로 합니다.
