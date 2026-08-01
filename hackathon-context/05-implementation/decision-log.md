---
Status: Active
Last Updated: 2026-08-01
Owner: Team
Source of Truth: false
---

# Decision Log

## DEC-001 Harness bootstrap

```text
시간: 2026-07-30
결정: 단계별 컨텍스트와 게이트를 사용하는 최소 하네스를 채택한다.
이유: 구현 단계에 폐기된 아이디어가 다시 섞이는 것을 방지한다.
대안: 전체 문서를 모든 작업에 제공한다.
영향: Accepted Source of Truth만 Builder 입력으로 사용한다.
```

## DEC-002 Persona Restorer scaffolding gate

```text
시간: 2026-08-01
요청: 조건부확률 기반 통계 연쇄 탐색 페르소나 복원기로 프로젝트 전체 스캐폴딩 시작
판정: 구현 보류
이유: 신규 문제 정의, 핵심 루프, 안전 경계와 P0가 모두 Proposed이며 현재 게이트는 problem_lock이다.
적용 규칙: 승인된 P0만 구현하고, 문제 정의·안전 경계·P0 범위 변경은 구현하지 않고 변경 요청으로 보고한다.
해제 조건: problem_lock → idea_lock → build_lock 승인과 첫 데모 fixture·근접도 계산 계약 확정
영향: src/**, tests/**와 신규 의존성은 생성하지 않았으며 요청과 차단 사유를 프로젝트 상태에 기록했다.
```

## DEC-003 Report artifact format alignment

```text
시간: 2026-08-01
결정: 사람이 읽는 정식 보고서는 report.html로 제공한다. panel.jsonl과 interviews.jsonl은 구조화 검토 산출물이며 evidence.json은 근거 검토용 보조 산출물이다. run.json은 실행 재현용 매니페스트로 보존한다.
이유: 현재 README와 실제 구현의 artifact gateway가 HTML 보고서를 제공하며, report.md를 생성하지 않는다.
영향: 활성 설계 문서와 데모 계획의 report.md 표현을 report.html로 정렬한다. historical Draft/Archived 문서는 당시 기록으로 보존한다.
상태: 문서 정렬 완료 예정; 구현·테스트 계약 정렬은 별도 작업이다.

## DEC-004 Current API test flow and SQLite handle cleanup

```text
시간: 2026-08-01
요청: 현재 API 엔드포인트의 정상·실패·SSE 테스트 플로우를 실행 가능한 문서로 정리하고 검증한다.
판정: docs/api-test-flow.md를 추가하고 ProjectStore의 단기 SQLite 연결이 항상 닫히도록 내부 수명 관리를 수정했다.
근거: tests/test_asgi.py 실행에서 Windows 임시 디렉터리 정리 시 SQLite 파일 잠금이 재현되었고, 연결 close 보장 후 6개 ASGI 테스트가 통과했다.
상위 문서와의 차이: API 경로, 안전 경계, P0 범위, 외부 서비스 계약 변경 없음.
영향: API 테스트 플로우의 재현성이 개선되며, 제품 응답 계약과 Raw Stream 보존 동작은 유지된다.
미해결: 실제 uvicorn 프로세스에 대한 네트워크 smoke test는 별도 실행 환경 확인이 필요하다.
```

## DEC-005 Scoped provisional implementation authorization

```text
시간: 2026-08-01
요청: 기능 명세 변경을 따라가지 못하는 현재 하네스 허가 전략을 최대한 완화한다.
판정: build_lock은 완료 게이트로 유지하되, 사용자 명시 요청에 따른 scoped_provisional 구현·테스트·문서 동기화를 허용한다.
허용: 기존 기능 명세를 반영하는 app/**·tests/**·docs/**·scripts/** 변경, 기존 의존성 내 버그 수정.
보호: 문제정의·핵심 목표·안전 경계·P0 범위·신규 외부 의존성·README·최종 제출물은 사람 승인 없이는 변경하지 않는다.
영향: implementation_authorized를 true로 바꾸되 authorization_mode를 scoped_provisional로 명시한다. build_lock 미통과를 숨기거나 P0 완료로 전이하지 않는다.
검증: harness/authorization-policy.yaml, docs/harness-authorization-strategy.md, phase-manifest.yaml, builder/orchestrator 규칙 간 정합성을 확인한다.
```

## DEC-006 Problem and solution confirmed

```text
시간: 2026-08-01
확인: 사용자가 현재 문제 정의와 솔루션이 확정된 상태임을 확인했다.
적용: problem-definition-persona-restorer.md와 persona-restorer-chain-search-concept.md를 구현 기준으로 취급한다.
영향: 구현 허가를 문제·솔루션 미확정 사유로 차단하지 않는다. build_lock은 워크플로·도구 계약·안전·P0 준비와 완료 판정에만 적용한다.
보호: 안전 경계·P0 범위·신규 외부 서비스·README·최종 제출물 변경은 기존 승인 규칙을 유지한다.
```

## DEC-007 Deterministic trace evaluation harness

```text
시간: 2026-08-01
요청: 실행 트레이스 기반 결정론 평가, 안전·전체 게이트, pytest/ruff/CI와 의존성 lock을 구현한다.
판정: evals/cases.jsonl 21개, evals/fixtures.json, grade_trace.py, run_evals.py, grader 회귀 테스트, GitHub Actions CI, pytest dev 의존성 및 uv.lock을 추가했다.
근거: ruff 전체 통과, pytest 19개 통과, unittest 16개 통과, 평가 21/21 통과(overall 1.0, safety 1.0), fixture hash 보존 확인.
상위 문서와의 차이: 문제 정의·안전 경계·P0 범위·외부 서비스·README·Raw Stream 원본 변경 없음. 평가 fixture는 실제 LLM·웹 검색이 아닌 결정론 합성 run이다.
영향: 프롬프트·라우팅 변경의 trace 회귀를 CI에서 검출할 수 있으며, 실제 provider 품질이나 실제 공개 통계 품질은 평가하지 않는다.
미해결: 현재 하네스는 이벤트 payload가 표현하는 순서·승인·실패 정직성만 채점하며, 실제 서비스의 모든 의도/대상 추출 동작을 실행하는 end-to-end 평가로 확장하지 않았다.
```

## DEC-008 Anti-overfitting evaluation hardening

```text
시간: 2026-08-01
요청: overall 점수에 맞춘 fixture/grader 과적합을 피하도록 평가 하네스를 개선한다.
판정: terminal-event·outcome 일관성 검증, 엄격한 event shape 검증, fixture 재사용 경고, 독립 holdout 6개, mutation 회귀 테스트 4개를 추가했다.
근거: regression 21/21, holdout 6/6, pytest 23개, ruff 전체 통과.
상위 문서와의 차이: 애플리케이션 핵심 루프·안전 경계·P0·README·Raw Stream은 변경하지 않았다. holdout은 실제 provider 호출 없이 변형된 결정론 trace로 구성했다.
과적합 방지 원칙: 점수 임계값을 낮추거나 fixture에 맞춘 예외 규칙을 추가하지 않고, unseen 이벤트 노이즈·변형 trace·terminal 상태 위반을 실패시키는 검증을 추가했다.
미해결: 현재 holdout도 합성 trace이며, 실제 앱이 SQLite에서 생성한 run.json 기반의 end-to-end 평가와 완전 독립 fixture 생성은 후속 작업이다.
```
