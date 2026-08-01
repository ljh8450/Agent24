export function completedReview() {
  return {
    message: "정책 검토를 끝까지 완료했습니다. 정량 근거 공백은 시나리오로 구분했습니다.",
    research: {
      results: [{
        title: "공직선거법 선거권 연령",
        url: "https://law.go.kr/example",
        domain: "law.go.kr",
        trust_tier: "korean_official",
        query: "국가법령정보센터 공직선거법 선거권 연령",
      }],
      failed_queries: [],
    },
    artifacts: {
      html_report: "/api/runs/run_ui_fixture/artifacts/report.html",
      panel: "/api/runs/run_ui_fixture/artifacts/panel.jsonl",
      evidence: "/api/runs/run_ui_fixture/artifacts/evidence.json",
    },
    run: {
      id: "run_ui_fixture",
      question: "수원에 사는 대학생들에게 투표권 소유 자격을 10살 올리고 싶어",
      target_population: "대한민국 수원, 대학 재학생",
      status: "completed",
      variables: [],
      constraints: [],
      sources: [{
        id: "src_1",
        title: "공직선거법 선거권 연령",
        url: "https://law.go.kr/example",
        organization: "국가법령정보센터",
        observed_period: "현행",
        trust_tier: "korean_official",
        snapshot_hash: "aabbccddeeff0011",
      }],
      events: [
        { type: "tool.completed", payload: { tool: "agent.intake_question" }, created_at: "2026-08-01T00:00:00Z" },
        {
          type: "policy.plan_created",
          payload: {
            plan: {
              id: "plan_fixture",
              status: "COMPLETED_WITH_ASSUMPTIONS",
              policy_focus: "선거권·참정권",
              target_population: "수원 · 대학 재학생",
              assumptions: [{ field: "기준 시점", value: "가장 최근 공개 통계", reason: "최신값 우선" }],
              rights_review: {
                severity: "high",
                finding: "특정 지역·학생 집단에 대한 선거권 차등은 기본권과 평등선거 검토가 선행되어야 합니다.",
                issues: ["공직선거법상 권한 범위", "필요성·비례성·최소침해"],
              },
              alternatives: [
                { id: "original", label: "원안", hypothesis: "권리 제한의 영향을 검토합니다." },
                { id: "alternative_1", label: "동일 선거연령 유지·시민정보 지원", hypothesis: "권리 차등 없이 접근 장벽을 낮춥니다." },
              ],
            },
          },
          created_at: "2026-08-01T00:00:01Z",
        },
        { type: "tool.completed", payload: { tool: "policy.plan_request" }, created_at: "2026-08-01T00:00:02Z" },
        { type: "tool.completed", payload: { tool: "web.parallel_korean_policy_research" }, created_at: "2026-08-01T00:00:03Z" },
        { type: "tool.completed", payload: { tool: "source.fetch_snapshot" }, created_at: "2026-08-01T00:00:04Z" },
        { type: "tool.completed", payload: { tool: "review.approve_constraints" }, created_at: "2026-08-01T00:00:05Z" },
        { type: "tool.completed", payload: { tool: "statistics.identification_bounds" }, created_at: "2026-08-01T00:00:06Z" },
        { type: "tool.completed", payload: { tool: "personas.sample_joint_distribution" }, created_at: "2026-08-01T00:00:07Z" },
        { type: "tool.completed", payload: { tool: "policy.weighted_panel_interviews" }, created_at: "2026-08-01T00:00:08Z" },
        { type: "tool.completed", payload: { tool: "report.write_provenance" }, created_at: "2026-08-01T00:00:09Z" },
      ],
      result: {
        status: "scenario_only",
        selected_model: "uninformed_maximum_entropy_scenario",
        identification: { lower: 0, upper: 1 },
        policy_review: {
          status: "COMPLETED_WITH_ASSUMPTIONS",
          warning: "인터뷰는 PGM으로 가중된 완전 합성 패널에 대한 모델 모의 응답입니다.",
          alternatives: [
            { id: "original", label: "원안" },
            { id: "alternative_1", label: "동일 선거연령 유지·시민정보 지원" },
          ],
          responses: { original: { support: 0.125, conditional: 0.125 } },
          interviews: [
            { segment_id: "P01", policy_id: "original", response: "conditional", reason: "표집된 속성만 참조한 가상 모의 응답입니다.", barrier: "등록·정보 접근 제약", suggested_change: "접근 지원을 별도 검증하세요.", tag: "narrative", mode: "configured_llm" },
            { segment_id: "P02", policy_id: "original", response: "support", reason: "표집된 속성 기준의 가상 모의 응답입니다.", barrier: "식별되지 않음", suggested_change: "실제 조사로 확인하세요.", tag: "narrative", mode: "configured_llm" },
          ],
          brief: "권리·법률 검토와 실제 조사 계획을 우선합니다.",
          panel: [
            {
              id: "P01",
              display_name: "가온",
              avatar: { seed: "f3640270d85579d395da5591", url: "/api/avatars/notionists/f3640270d85579d395da5591.svg", alt: "P01의 장식용 Notionists 합성 아바타" },
              attributes: [{ variable: "age_eligibility_band", value: "18_27" }],
              weight_display: "12.5%",
              evidence_level: "scenario_only",
              narrative: "선거 연령 경계 구간에 속한 완전 합성 세그먼트입니다.",
            },
            {
              id: "P02",
              display_name: "나래",
              avatar: { seed: "37ab708b043b9e741aa11355", url: "/api/avatars/notionists/37ab708b043b9e741aa11355.svg", alt: "P02의 장식용 Notionists 합성 아바타" },
              attributes: [{ variable: "age_eligibility_band", value: "28_plus" }],
              weight_display: "12.5%",
              evidence_level: "scenario_only",
              narrative: "기존 선거 연령 이상 구간의 완전 합성 세그먼트입니다.",
            },
          ],
        },
      },
    },
  };
}

export async function mockAutonomousReview(page, customize = (completed) => completed) {
  await page.route("**/api/agent/review/stream", async (route) => {
    const completed = customize(completedReview());
    const frames = [
      ["review.accepted", { run: completed.run }],
      ["message.stream.start", { phase: "progress" }],
      ["message.delta", { delta: "정책 목표를 해석했습니다.\n\n" }],
      ["tool.update", { tool: "policy.plan_request", status: "running" }],
      ["tool.update", { tool: "policy.plan_request", status: "completed" }],
      ["tool.update", { tool: "web.parallel_korean_policy_research", status: "completed" }],
      ["message.delta", { delta: "한국 공공·연구 출처를 병렬 탐색하고 있습니다.\n" }],
      ["run.updated", { run: completed.run }],
      ["research.completed", completed.research],
      ["tool.update", { tool: "report.write_provenance", status: "completed" }],
      ["message.stream.start", { phase: "final" }],
      ["message.delta", { delta: completed.message }],
      ["review.completed", completed],
    ];
    const body = frames.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");
    await route.fulfill({ contentType: "text/event-stream; charset=utf-8", status: 201, body });
  });
  await page.route("**/api/runs/run_ui_fixture/artifacts/report.html", async (route) => {
    await route.fulfill({
      contentType: "text/html; charset=utf-8",
      body: "<!doctype html><html><body style=\"font-family:sans-serif;padding:32px\"><h1>정책 검토 보고서</h1><p>픽스처 문서 본문입니다.</p></body></html>",
    });
  });
  const completedForArtifacts = customize(completedReview());
  const fixtureReview = completedForArtifacts.run.result.policy_review;
  const alternativeLabels = new Map(fixtureReview.alternatives.map((item) => [item.id, item.label]));
  const mergedPanel = fixtureReview.panel.map((segment) => ({
    id: segment.id,
    name: segment.display_name,
    attributes: Object.fromEntries(segment.attributes.map((attr) => [attr.variable, attr.value])),
    share: segment.weight_display,
    narrative: segment.narrative,
    answers: fixtureReview.interviews
      .filter((item) => item.segment_id === segment.id)
      .map((item) => ({
        policy_id: item.policy_id,
        policy: alternativeLabels.get(item.policy_id) || item.policy_id,
        response: item.response,
        reason: item.reason,
        barrier: item.barrier,
        suggestion: item.suggested_change,
      })),
  }));
  await page.route("**/api/runs/run_ui_fixture/artifacts/panel.jsonl", async (route) => {
    await route.fulfill({ contentType: "application/x-ndjson; charset=utf-8", body: mergedPanel.map((item) => JSON.stringify(item)).join("\n") });
  });
  await page.route("**/api/runs/run_ui_fixture/artifacts/evidence.json", async (route) => {
    await route.fulfill({ contentType: "application/json; charset=utf-8", body: JSON.stringify({ sources: [{ id: "src_1", organization: "국가법령정보센터" }], constraints: [] }, null, 2) });
  });
  await page.route("**/api/avatars/notionists/*.svg", async (route) => {
    await route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#f3efe7"/><circle cx="32" cy="23" r="11" fill="none" stroke="#111" stroke-width="2"/><path d="M13 62c2-17 10-25 19-25s17 8 19 25" fill="none" stroke="#111" stroke-width="2"/></svg>',
    });
  });
}

export async function submitPolicy(page) {
  await mockAutonomousReview(page);
  await page.goto("/");
  await page.getByTestId("question-input").fill("수원에 사는 대학생들에게 투표권 소유 자격을 10살 올리고 싶어");
  await page.getByTestId("start-research").click();
}
