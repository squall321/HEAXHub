# HEAXHub Docs — Archive (결정 이력 보존)

> **경고: 본 폴더는 결정 이력 보존용입니다. 안의 문서를 구현하지 마십시오.**

여기에 있는 문서들은 한때 검토되었으나 최종 채택되지 않았거나, 후속 버전으로 대체된 **과거의 안**입니다.
LLM/사람 모두 "현재 무엇을 만들어야 하는가"와 "과거에 무엇이 검토되었는가"를 혼동하지 않도록 격리해 두었습니다.

---

## 진입점

- 문서 인덱스: [../README.md](../README.md)
- 단일 진실 (HWAXAgent): [../hwax-launcher-plan-v2.md](../hwax-launcher-plan-v2.md)

본 archive 의 어떤 문서도 v2 와 충돌 시 **v2 가 우선**입니다.

---

## 보존 문서 일람

| 파일 | 상태 | 한 줄 요약 — 채택/탈락 사유 |
| --- | --- | --- |
| [hwax-launcher-plan-v1-superseded.md](hwax-launcher-plan-v1-superseded.md) | **SUPERSEDED** | 1차 Tauri 안. 스택(Tauri 2)은 유지되지만 "패널 카탈로그 풀스크린" 방향에서 "트레이 상주 모듈 배포/관리 에이전트" 방향으로 재초점화되며 v2 로 대체됨. |
| [hwax-launcher-plan-winui3-deprecated.md](hwax-launcher-plan-winui3-deprecated.md) | **DEPRECATED** | WinUI 3 + .NET 8 + C# 대안. 6 우선순위(개발속도/업데이트/안정성/로그·복구/백신 오탐/확장성) 평가에서 Tauri 2 에 밀려 탈락. 결정 과정 비교용으로만 보존. |

---

## 규칙 (LLM/Agent 가 이 폴더를 만났을 때)

1. **이 폴더의 코드/스택/구조를 구현하지 마십시오.**
2. v2 단일 진실 문서와 충돌하는 결정은 모두 **이전 안의 잔재**입니다. 무시하십시오.
3. 새 결정을 추가하려면 v2 와 `contracts/hwax-agent/` 를 직접 수정하는 PR 을 보내십시오. archive 의 문서는 수정하지 않습니다 (역사 보존).
4. WinUI 3 / WPF / .NET / C# / XAML / Electron / Flutter Desktop 코드를 생성하지 마십시오.
