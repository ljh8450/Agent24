# 페르소나 복원기 실행 안내

`SPEC.md`의 정책 검토 수직 슬라이스를 구현한 로컬 WebChat 앱이다. 사용자는 정책 의도 한 문장만 입력한다. 에이전트가 대상·가정·대안·권리 검토·한국 증거 검색·원문 스냅샷·정량 제약 gate·PGM·합성 프로필·보고서까지 실행한다. 검증된 정량 제약이 없으면 멈추거나 수치를 꾸미지 않고 `scenario_only`와 `[0,1]` 식별구간으로 완료한다. 합성 패널 인터뷰는 별도 LLM 설정이 있어야 하며, 실제 조사 결과가 아니다.

화면은 실행된 도구를 `검색 → 원문 스냅샷 → 제약 검토 → 분포 계산 → 표집` 순서의 카드형 활동 기록으로 남기고, 실제로 표집된 합성 페르소나만 결합분포 주위의 스웜으로 표시한다. 출처는 도메인 기반의 `korean_official` / `korean_research` / `unreviewed_web` 시작 등급을 가지지만, 등급이 조사설계 검토를 대신하지는 않는다.

페르소나 프로필 이미지는 CC0인 Notionists by Zoish를 DiceBear `notionists` SVG 스타일로 사용한다. 이미지는 합성 ID만으로 결정되며 PGM 속성·정책 반응·실제 인물과 연결되지 않는 `decorative_synthetic` 표현층이다. 출처와 라이선스는 [THIRD_PARTY_ASSETS.md](THIRD_PARTY_ASSETS.md)에 고정한다.

## 실행

```bash
cd /Users/yuchanlee/agent24/project
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m uvicorn app.asgi:app --reload --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 연다. 모든 데이터베이스, 출처 스냅샷, run artifact는 `project/data/`에만 저장된다.

현재 개발 환경에서 검증한 인터프리터는 Python 3.12이다. 의존성이 없는 다른 `python3`가 먼저 잡히면 `python3.12`을 명시하고 위의 프로젝트 전용 virtual environment를 사용한다.

API는 기본적으로 loopback 요청만 받는다. LAN/프록시로 노출하려면 강한 `GATEWAY_TOKEN`을 설정하고 모든 API 요청에 `Authorization: Bearer <token>`을 보낸다.

## 검증

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/python -m unittest discover -s tests -v
npm install
npx playwright install chromium
npm run test:e2e
```

## 워크플로

1. 채팅에 정책 목표를 한 문장으로 입력한다.
2. 에이전트가 대상·누락 가정·원안/대안·권리 위험·PGM 변수·검색어를 설계한다. 강압·정치 조작·민감 특성 표적화는 `SAFETY_BLOCKED`로 종료한다.
3. 한국 공공기관·연구 도메인을 병렬 검색하고 상위 원문을 project 로컬 cache에 hash로 고정한다.
4. 설정된 LLM이 있으면 수치 후보를 추출한다. deterministic evidence gate는 신뢰 출처, 원문 문장, `exact` 모집단을 모두 만족하는 후보만 자동 승인한다.
5. 승인 제약이 있으면 feasibility·IPF·LP 식별구간을 계산한다. 없으면 균등 시나리오와 `[0,1]`을 표시해 근거 공백을 보존한다.
6. 성인 범위에서 Notionists 합성 프로필과 가중 세그먼트를 만들고, LLM이 있을 때만 구조화된 모의 인터뷰를 실행한다.
7. 우측 Artifacts에 정책 브리프, 패널 JSONL, 인터뷰 JSONL, 증거 JSON, HTML 보고서와 provenance를 남긴다. 우측에는 사용자 입력폼이 없다.

보고서는 Markdown과 `report.html` 두 형태로 `project/data/runs/<run-id>/`에 저장된다. 정책 패널 검토가 완료된 run에는 `policy_brief.md`, `panel.jsonl`, `interviews.jsonl`, `evidence.json`도 생성된다. HTML은 `pandas` 데이터프레임과 Great Tables 렌더링 하네스를 사용해 실행 요약, 출처 원장, 제약, 식별성, 정책안, 가중 패널, 도구 이력을 정리한다. 모든 artifact는 인증된 gateway 경로에서만 읽는다.

## LLM 설정

`.env.example`의 세 값을 셸 환경변수로 설정한다. OpenAI 호환 chat-completions API를 사용한다.

```bash
export LLM_API_URL=https://api.openai.com/v1/chat/completions
export LLM_API_KEY=...
export LLM_MODEL=...
```

키 없이 설문 응답을 만들지 않는다. 프론트엔드 검증용으로만 `PERSONA_RESTORER_DEMO_MODEL=1`을 설정할 수 있으며, 결과에는 결정론적 데모임이 표시된다.

## 한국 공공 데이터 연결

KOSIS 통계표는 `KOSIS_API_KEY`를 설정하면 `userStatsId` 또는 직접 통계표 파라미터로 수집한다. 공공데이터포털은 `DATA_GO_KR_SERVICE_KEY`를 설정하면 현재 UI의 복지서비스·국민연금 가입현황 커넥터를 호출할 수 있다. `project/.env`는 서버 시작 시 자동으로 읽으며, 셸에서 명시적으로 export한 값이 우선한다. 키는 요청 URL에서만 사용하고, 저장되는 출처 URL과 보고서에는 `[configured]`로 대체한다.

```bash
export KOSIS_API_KEY=...
export DATA_GO_KR_SERVICE_KEY=...
```

## 안전·정확성 경계

- 웹 원문은 `untrusted_external` 증거이며 에이전트 지시가 아니다.
- `overlap_unknown` 또는 `incompatible` 모집단 제약은 명시적 override 사유 없이는 승인할 수 없다.
- feasibility가 실패하면 IPF, 식별구간, 페르소나 생성으로 진행하지 않는다.
- 점추정은 최대엔트로피 구조 가정의 결과이고, LP 식별구간과 구별해서 표시한다.
- 선택적으로 DAG 후보(`{"id":"interest_by_region","parents":{"interest":["region"]}}`)를 넣어 제약을 만족하는 베이지안 네트워크 점모형의 민감도를 비교할 수 있다. 이 비선형 적합이 실패하면 후보는 `not_fitted`로 보고하며, LP 식별구간은 여전히 구조 가정 없이 계산된다.
- 미성년자·취약 집단의 1인칭 합성 페르소나는 생성하지 않는다.
