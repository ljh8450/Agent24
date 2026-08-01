# E2P Agent (Evidence-to-Persona Agent)

공개 통계 근거로 가중 페르소나 패널을 합성하고, 정책·서비스 기획안의 사각지대와 실효성을 사전 검증하는 정책 연구 에이전트입니다. 모든 산출물에 출처·가중치·가정·불확실성이 함께 기록되며, 검증된 정량 근거가 없으면 수치를 지어내지 않고 `scenario_only` 상태로 정직하게 완료합니다.

## 해결하려는 문제

근거 있는 페르소나를 만들려면 수많은 통계와 보고서를 일일이 찾고, 파편화된 데이터를 검토해 대표 유형과 비중을 직접 정해야 합니다. 일반 LLM에 맡기면 관련 없는 수치를 임의로 이어 붙이거나 근거 없는 가상 설정을 그럴듯하게 지어내는 위험이 있습니다.

E2P Agent는 조사 인력과 예산이 부족한 지자체·NGO를 위해 공개 통계 탐색 → 원문 스냅샷 → 정량 제약 검증 → 가중 패널 합성 → 모의 인터뷰 → 보고서까지를 하나의 실행으로 자동화합니다.

## 동작 구조 — 두 개의 대화 레인

채팅 입력은 먼저 의도 분류를 거칩니다.

| 의도 | 동작 |
| :--- | :--- |
| `policy_review` | 자율 검토 파이프라인 실행: 계획 → 한국 신뢰 출처 병렬 탐색 → 원문 스냅샷 → 제약 추출·자동 승인 게이트 → PGM/식별구간 → 가중 패널 → 모의 인터뷰 → 인사이트·보고서 |
| `clarify` | 검토 요청이 너무 얇을 때 — 실행하지 않고 대상·범위·수단 중 빠진 것만 되물음 |
| `conversation` | 이전 결과에 대한 후속 질문·일반 대화 — 세션 메모리 기반 실시간 스트리밍 응답 |

오케스트레이션(실행 순서·승인 규칙·안전 차단·통계 계산)은 전부 결정론적 코드입니다. LLM은 계획 설계, 제약 후보 추출, 패널 서술, 모의 인터뷰, 인사이트 작성에만 쓰이고 DB·솔버·네트워크에 직접 접근하지 못합니다.

## 설치와 실행

Python 3.12 이상 필요.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m uvicorn app.asgi:app --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. 데이터베이스, 출처 스냅샷, run artifact는 전부 저장소의 `data/`에만 저장됩니다.

데모 중 원본 실행 이벤트를 별도 화면에서 동시에 보려면 두 서버를 같은 저장소로 실행합니다.

```bash
.venv/bin/python scripts/run_servers.py
```

- 제품 화면: `http://127.0.0.1:8000`
- 원본 이벤트 모니터: `http://127.0.0.1:8001`

모니터는 제품 서버와 같은 SQLite WAL을 이벤트 ID 커서로 따라갑니다. `tool.started`와
`tool.completed`/`tool.failed`는 각각 `tool_call`과 `tool_result` 배지 옆에 원본 JSON 그대로 실시간 출력됩니다.

`.env.example`을 `.env`로 복사해 필요한 값만 채우면 서버 시작 시 자동으로 읽습니다(셸에서 export한 값이 우선).

| 환경 변수 | 필수 | 용도 |
| :--- | :--- | :--- |
| `LLM_API_URL`, `LLM_API_KEY`, `LLM_MODEL` | 선택 | OpenAI 호환 chat-completions 모델. 없으면 통계·패널·보고서는 그대로 나오고 인터뷰·서술만 생략 |
| `LLM_MODEL_FINAL` | 선택 | 인사이트·대화에 우선 사용할 상위 모델(실패 시 기본 모델로 폴백) |
| `PERSONA_RESTORER_DEMO_MODEL=1` | 선택 | 결정론 데모 응답(명시 라벨) — 모델·실제 조사 대체 아님 |
| `KOSIS_API_KEY`, `DATA_GO_KR_SERVICE_KEY` | 선택 | 한국 공공 통계 커넥터. 키는 요청 URL에만 쓰이고 저장·보고서에는 `[configured]`로 대체 |
| `GATEWAY_TOKEN` | 선택 | loopback 밖 노출 시 API 인증 토큰(`Authorization: Bearer`) |

## 검증

```bash
.venv/bin/ruff check app tests
.venv/bin/python -m unittest discover -s tests -v
npm install && npx playwright install chromium && npm run test:e2e   # 웹 E2E
PYTHONPATH=. python scripts/spot_check.py   # 실키 LLM 스팟체크(비용 발생) — 결과는 scripts/spot_results/
```

## 산출물

실행이 끝나면 `data/runs/<run-id>/`에 남습니다.

| 파일 | 내용 |
| :--- | :--- |
| `report.html` | 최종 보고서 — 근거·가정, 식별구간, 정책안별 반응, 한계 |
| `panel.jsonl` | 가중 페르소나 세그먼트(비중·속성·서술)와 세그먼트별 인터뷰 응답 |
| `evidence.json` | 저장한 출처, 제약, 제외 사유 |
| `run.json` | 전체 실행 이력 재현용 매니페스트(sha256 포함) |

## 안전·정확성 경계

- 정치적 설득·조작 표적화, 강압·배제 설계, 민감 특성 추론 요청은 계획 단계에서 차단됩니다.
- 웹 원문은 신뢰할 수 없는 데이터로 취급합니다 — 원문 속 지시는 실행되지 않고, 수집은 공인 IP만 허용(SSRF·사설망 차단), 응답 5MB 제한.
- 모집단이 정확히 일치하고 원문 문장이 있는 제약만 코드 규칙으로 자동 승인됩니다. `overlap_unknown`/`incompatible` 제약은 명시적 override 사유 없이는 쓰이지 않습니다.
- 점추정은 최대엔트로피 구조 가정의 결과이며 LP 식별구간과 구별해 표시합니다.
- 페르소나·인터뷰·인사이트는 전부 "완전 합성"으로 라벨되며 실제 개인·여론·인과효과가 아닙니다. 미성년자·취약 집단의 1인칭 합성 페르소나는 만들지 않습니다.
- API 키는 환경 변수로만 주입되고 저장소·산출물·로그에 기록되지 않습니다.

## 알려진 한계와 다음 단계

- 근거 게이트가 자동 검증 가능한 교차표를 찾지 못하면 결과는 균등 시나리오(`scenario_only`)입니다 — 모집단 추정이 아님이 보고서에 명시됩니다.
- 모의 인터뷰는 실제 시민 반응이 아니며, 보고서의 검증 계획(소규모 시범·실제 설문)이 항상 후속 단계입니다.
- 평가 하네스·CI는 준비 중입니다(이슈 #9). 수치가 확정되면 이 절에 기록합니다.

## 라이선스와 자산

MIT — [LICENSE](LICENSE). 아바타는 DiceBear notionists(CC0-1.0), 데이터 출처는 각 공공기관 이용약관을 따릅니다. 자세한 내용은 [THIRD_PARTY_ASSETS.md](THIRD_PARTY_ASSETS.md).
