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

## DEC-009 SQLite trace round-trip evaluation

```text
시간: 2026-08-01
요청: 손작성 fixture만 통과하는 평가를 줄이고 실제 저장 경로의 run.json을 grader에 연결한다.
판정: ProjectStore로 SQLite 이벤트를 append하고 get_run()/write_artifact()로 run.json을 만든 뒤 grade_file()로 채점하는 테스트를 추가했다. grade_trace CLI는 --run-dir도 지원하고, run_evals는 category별 통과율을 출력한다.
근거: 실제 SQLite round-trip 테스트 통과, 전체 pytest 24개 통과, regression 21/21, holdout 6/6.
상위 문서와의 차이: app 서비스 동작·P0·안전 경계·Raw Stream 원본은 변경하지 않았다. 테스트는 실제 외부 API 없이 저장·재생·채점 계약을 검증한다.
미해결: 실제 autonomous_review 전체 실행에서 생성되는 run.json을 CI fixture로 사용하는 end-to-end 시나리오는 후속 작업이다.
```

## DEC-010 Loop contract evaluation after remote integration

```text
시간: 2026-08-01
요청: #17/#18 루프 도입 후 결정 라운드 예산, stop 이후 도구 호출 차단, broader 승인 가정 note를 evals에 반영한다.
원격 반영: origin/agent/decision-event-visibility의 evidence recovery loop 변경을 merge 상태로 통합했다. README와 기존 evals/CI/lock은 보존했다.
판정: `agent.decision` 라운드 ≤2, stop 이후 `tool.started` 0건, 승인된 broader constraint의 `override_note` 필수 검사를 grader에 추가했다. 각 위반을 검출하는 mutation 테스트도 추가했다.
근거: 원격 loop 테스트 포함 pytest 41개 통과, regression 24/24, holdout 6/6, ruff 통과.
상위 문서와의 차이: loop 구현은 원격 이슈 범위에 따른 provisional 통합이며, evals/ 파일 소유권은 현재 이슈에 유지했다. 문제 정의·안전 경계·P0 범위는 변경하지 않았다.
미해결: merge는 아직 커밋 전이며, 실제 GitHub Actions hosted 실행 결과는 확인하지 않았다.
```

## DEC-011 Human-friendly frontend design alignment

```text
시간: 2026-08-02
요청: 제공된 design (1).md를 현재 코드베이스와 하네스 구조에 맞게 반영하고 frontend 프로젝트 스킬을 만든다.
판정: static/index.html과 static/style.css를 기존 API·data-testid·P0 산출물 계약을 유지하는 범위에서 리팩터링했다. 초기 화면은 입력 중심으로 단순화하고 실행 시작 후 기존 inspector를 노출한다.
디자인 근거: C:\Users\dlwjd\Downloads\design (1).md의 304px sidebar, 64px topbar, centered composer, Evidence gateway, accessible state/copy 요구사항.
스킬: skills/frontend-project/SKILL.md와 references/design-reference.md를 추가하고 원본 디자인 경로를 항상 참고하도록 명시했다.
상위 문서와의 차이: 문제 정의·안전 경계·P0 범위·외부 서비스·README·Raw Stream은 변경하지 않았다. 우측 inspector는 디자인의 초기 2열 개념과 기존 실행 산출물 요구를 함께 만족시키기 위해 empty state에서만 숨긴다.
검증: skill quick_validate 통과, unittest 33개 통과.
미해결: 실제 브라우저 시각 QA는 Playwright/브라우저 실행 환경에서 별도 확인이 필요하다.
```

## DEC-012 Project-local frontend design specification

```text
시간: 2026-08-02
요청: 외부 Downloads 디자인 파일이 아니라 프로젝트 폴더의 디자인 명세를 frontend 스킬이 참고하도록 변경한다.
판정: docs/frontend-design-spec.md를 프로젝트 로컬 Source of Truth로 추가하고, skills/frontend-project/SKILL.md·references/design-reference.md·agents/openai.yaml의 참조를 모두 해당 파일로 전환했다.
영향: 향후 frontend 작업은 외부 파일 없이 저장소만으로 디자인·접근성·반응형·카피 기준을 재현할 수 있다.
상위 문서와의 차이: 문제 정의·안전 경계·P0 범위·외부 서비스·README·Raw Stream은 변경하지 않았다.
검증: frontend-project quick_validate 통과, Downloads 경로 잔존 여부 확인, git diff --check 통과.
```

## DEC-013 Text breadcrumb header

```text
시간: 2026-08-02
요청: header의 back/icon button 대신 `Policy review › 새 정책 검토` 텍스트 breadcrumb을 사용한다.
판정: static/index.html의 topbar back button을 제거하고 semantic nav breadcrumb으로 변경했다. 새 정책 시작 기능은 sidebar의 기존 CTA로 유지한다.
영향: 이미지 기준의 업무형 header가 적용되고, 현재 페이지가 `aria-current="page"`로 식별된다. API·실행 흐름·P0 산출물은 변경하지 않았다.
검증: 기존 unittest 회귀 테스트와 git diff --check를 실행한다.
```

## DEC-014 Light theme visual alignment

```text
시간: 2026-08-02
요청: 현재 서비스는 기능과 UI 구성을 유지한 채, 다크 테마를 화이트 테마로 바꾸고 레이아웃 밀도만 조정한다.
판정: static/index.html의 브라우저 테마 힌트를 light로 전환하고 static/style.css에 화이트 캔버스·표면·경계·텍스트·블루 행동 강조 토큰을 적용했다. 기존 사이드바, 입력 컴포저, 우측 inspector와 모든 data-testid/API 흐름은 보존했다.
상위 문서와의 차이: 기존 frontend-design-spec.md의 다크 테마 기준은 사용자의 최신 명시 요청으로 대체해 화이트 테마 기준으로 동기화했다. 문제정의·안전 경계·P0 범위·외부 서비스·README·Raw Stream은 변경하지 않았다.
검증: CSS 구문·기존 프론트엔드 Playwright 회귀와 좁은 화면 레이아웃을 실행해 확인한다. 표현 전용 변경이므로 단위 테스트는 추가하지 않는다.
미해결: 브라우저 렌더링 결과는 실제 저장된 사용자 데이터의 긴 텍스트 조합까지 수동 확인이 필요하다.
```

## DEC-015 Submission lock terminal-state recovery

```text
시간: 2026-08-02
요청: AI 응답이 실행 중일 때는 버튼과 Enter 제출을 모두 막고, 응답 완료 후에는 다시 활성화한다.
판정: 실행 상태를 단일 setSubmissionLocked 함수로 통합해 제출 버튼·입력창·aria-busy와 Enter 제출을 같은 상태로 제어했다. chat.completed와 review.completed 수신 시점에도 즉시 잠금을 해제해, 스트림 연결 종료만 기다리던 기존 해제 경로를 보완했다. 오류와 finally에서도 해제된다.
상위 문서와의 차이: API·안전 경계·P0 범위·외부 서비스·README·Raw Stream은 변경하지 않았다.
검증: 완료된 검토 후 입력창과 제출 버튼이 모두 활성화되는 Playwright 회귀 단언을 추가하고, 기존 테스트를 실행한다.
```

## DEC-016 Restore dark color palette

```text
시간: 2026-08-02
요청: 색상 조합만 기존으로 되돌린다.
판정: 화이트 테마 CSS 오버라이드와 light 브라우저 테마 힌트를 제거하고, 기존 다크 색상 토큰을 복원했다. 레이아웃과 요청 제출 잠금 로직은 유지한다.
상위 문서와의 차이: frontend-design-spec.md와 UI 이슈 문서를 현재 다크 팔레트 기준으로 동기화했다. 문제정의·안전 경계·P0 범위·외부 서비스·README·Raw Stream은 변경하지 않았다.
검증: 색상 오버라이드 제거와 HTML 테마 힌트를 정적 확인하고, 기존 브라우저 회귀 테스트를 실행한다.
```

## DEC-017 Persona response badge single-line display

```text
시간: 2026-08-02
요청: 페르소나 상세 표의 반응 값을 한 줄로 표시한다.
판정: 반응 열을 76px 최소 폭으로 고정하고, 반응 배지에 inline-flex와 white-space: nowrap을 적용했다. 정책안과 이유 열의 자연스러운 줄바꿈은 유지한다.
상위 문서와의 차이: 정책 검토 내용·안전 경계·P0 범위·외부 서비스·README·Raw Stream은 변경하지 않았다.
검증: 페르소나 상세 Playwright 테스트에서 반응 배지의 계산된 white-space가 nowrap인지 확인한다.
```
