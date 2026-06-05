---
name: HWAXAgent 관련 버그 보고
about: HWAXAgent ↔ HEAXHub 연동에서 발생한 버그
title: "[hwax-agent][bug] "
labels: ["hwax-agent", "bug", "from-hwax-agent"]
assignees: []
---

<!--
협업 규약: docs/hwax-agent-pr-protocol.md
보안 관련 버그는 라벨에 `security` 를 추가하고, 가능하면 비공개 채널로도 통보합니다.
-->

## 환경

- Windows 버전: (Win10 / Win11)
- Windows 빌드: (예: 22H2, OS build 22621.xxxx)
- HWAXAgent 버전:
- launcher 채널: (stable / canary)
- contracts 버전 (HWAXAgent 가 동기화한 버전):
- HEAXHub 서버 버전 (응답 헤더 `X-Heaxhub-Version` 등):
- 네트워크 환경: (사내망 / VPN / 인터넷 직결)

## 재현 절차

1.
2.
3.

기대 결과:
실제 결과:

## 로그 첨부

<!--
가능한 로그를 첨부합니다. 민감 정보는 마스킹.
- launcher 측: %APPDATA%\HWAXAgent\logs\*.log
- 서버 측: backend audit log (가능한 경우 audit id 만 적어도 됨)
-->

```
(로그 본문 또는 첨부 파일 링크)
```

## audit kind

<!--
영향받은 감사 이벤트 종류. 잘 모르면 비워둡니다.
예: submission.create, submission.upload, auth.refresh, agent.register
-->

- audit kind:
- 관련 submission/job id (있다면):

## 영향 범위

- 영향받는 사용자: (단일 사용자 / 특정 부서 / 전사)
- 영향받는 기능: (제출 / 결과 다운로드 / 인증 / 매니페스트 검증 / 기타)
- 데이터 손실 가능성: (없음 / 의심됨 / 확인됨)
- 회피 방법:
