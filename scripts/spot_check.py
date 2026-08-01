"""실키 LLM 스팟체크 — CI 밖에서 수동 실행하는 소규모 실측 평가.

프롬프트·라우팅 변경 전후를 비교할 수 있게 핵심 LLM 지점의 지표를 뽑아
scripts/spot_results/<UTC시각>.json 으로 저장한다. 실제 API 비용이 든다.

실행:  PYTHONPATH=. python scripts/spot_check.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from app.personas import classify_chat_intent
from app.service import ResearchAgent

QUESTIONS = [
    "서울 청년 1인 가구의 월세 부담을 줄이는 주거 지원 정책을 검토해줘",
    "대전 대학생 교통비 지원 정책을 검토해줘",
    "부산 청년의 문화생활 실태 페르소나를 만들어줘",
]

INTENT_CASES = [
    ("서울 청년 월세 지원 정책을 검토해줘", "policy_review"),
    ("아까 결과에서 지지율이 제일 높았던 대안이 뭐였지?", "conversation"),
    ("정책 하나 봐줘", "clarify"),
]


def run_review_metrics(agent: ResearchAgent, question: str) -> dict:
    completed = agent.autonomous_review(question)
    run = completed["run"]
    result = run["result"] or {}
    review = result.get("policy_review") or {}
    approved = [c for c in run["constraints"] if c["review_status"] == "approved"]
    mass_by_variable: dict[str, float] = defaultdict(float)
    for constraint in approved:
        for key in constraint["where"]:
            mass_by_variable[key] += float(constraint["value"])
    interviews = review.get("interviews") or []
    panel_ids = {segment["id"] for segment in review.get("panel") or []}
    interview_ids = {item.get("segment_id") for item in interviews}
    return {
        "question": question,
        "status": result.get("status"),
        "candidates": len(run["constraints"]),
        "approved": len(approved),
        "constrained_variables": len(mass_by_variable),
        "mass_by_variable": {key: round(value, 3) for key, value in mass_by_variable.items()},
        "interview_contract_ok": bool(interviews) and panel_ids <= interview_ids,
        "interview_count": len(interviews),
        "decisions": [
            {"round": e["payload"].get("round"), "action": e["payload"].get("action")}
            for e in run["events"]
            if e["type"] == "agent.decision"
        ],
    }


def main() -> int:
    results: dict = {"ran_at": datetime.now(UTC).isoformat(), "reviews": [], "intent": []}
    with tempfile.TemporaryDirectory() as root:
        agent = ResearchAgent(Path(root))
        for question in QUESTIONS:
            print(f"== 실행: {question}")
            try:
                metrics = run_review_metrics(agent, question)
            except Exception as error:  # 스팟체크는 실패도 기록이 목적이다
                metrics = {"question": question, "error": type(error).__name__, "detail": str(error)[:200]}
            results["reviews"].append(metrics)
            print(json.dumps(metrics, ensure_ascii=False, indent=2))
        correct = 0
        for text, expected in INTENT_CASES:
            got = classify_chat_intent(text, has_history=True)
            results["intent"].append({"text": text, "expected": expected, "got": got})
            correct += got == expected
        results["intent_accuracy"] = f"{correct}/{len(INTENT_CASES)}"
    out_dir = Path(__file__).parent / "spot_results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{results['ran_at'].replace(':', '-')}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out_path} | 의도 분류: {results['intent_accuracy']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
