<!--
HEAXHub PR 템플릿
HWAXAgent 와의 협업 규약: docs/hwax-agent-pr-protocol.md
-->

## 변경 요약

<!-- 1~3 문장으로 무엇을 왜 바꾸는지 적습니다. -->

## 영향 범위

- 영향받는 모듈/디렉터리:
- 영향받는 사용자 흐름:
- 데이터 마이그레이션 필요 여부: (예 / 아니오 — 있다면 alembic revision 번호)
- 백워드 호환성: (호환 / 부분 호환 / 비호환)

## contracts 영향

- contracts/ 또는 schemas/ 변경 포함 여부: (예 / 아니오)
- 영향받는 표면: (OpenAPI / manifest schema / SECURITY.md / 없음)
- contracts SemVer bump: (none / patch / minor / major)
- HWAXAgent 측 follow-up 필요 여부: (예 / 아니오)
  - 예인 경우 대응 issue/PR 링크:

## 테스트

- [ ] 단위 테스트 추가/갱신
- [ ] OpenAPI ↔ pydantic 정합성 테스트 통과
- [ ] `contracts-validate` 워크플로우 통과 (해당 시)
- [ ] 로컬 smoke test 결과 첨부 (필요 시 스크린샷/로그)

## 리뷰어

- 1차 리뷰어:
- 보안/계약 영향 시 추가 리뷰어:
- 관련 docs/hwax-agent-pr-protocol.md §:
