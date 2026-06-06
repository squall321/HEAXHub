# HWAXAgent Contracts

HEAXHub와 HWAXAgent(Windows 런처) 사이의 계약을 모아둔 디렉터리입니다. JSON Schema(매니페스트·설치 보고·감사 이벤트), OpenAPI 문서, 그리고 런처가 그대로 가져다 쓰는 디자인 토큰(`tokens.css`)이 들어 있습니다.

## 소비 방식

HWAXAgent 저장소는 이 폴더를 **git submodule** (`contracts/hwax-agent → HEAXHub/contracts/hwax-agent`) 로 끌어 씁니다. CI 빌드 시 submodule SHA를 고정합니다. 런처는 이 `*.schema.json` 으로부터 Rust 구조체(serde)와 TypeScript 타입을 만들어 사용합니다 — 스택은 **Tauri 2 + Rust + React** 이며 C#/.NET/WinUI3 은 사용하지 않습니다.

## 버전 규칙

- SemVer (`MAJOR.MINOR.PATCH`) 를 사용합니다.
- **MAJOR**: 필드 제거·이름 변경·enum 축소 등 호환 불가 변경.
- **MINOR**: 새 옵션 필드·새 enum 값 추가.
- **PATCH**: 문구·예시·주석만 수정.
- 변경은 항상 `CHANGELOG.md` 의 새 항목으로 기록하고, 릴리스 태그(`hwax-contracts-vX.Y.Z`)를 찍습니다. HWAXAgent 는 태그 단위로만 submodule pointer를 올립니다.
