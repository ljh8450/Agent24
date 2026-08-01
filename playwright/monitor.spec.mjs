import { expect, test } from "@playwright/test";

test("second server follows raw tool events from the product server", async ({ page, request }) => {
  const createdResponse = await request.post("http://127.0.0.1:8124/api/chat", {
    data: { text: "대전 청년 교통비 지원 정책을 검토해줘" },
  });
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json();
  const runId = created.run.id;

  const failedTool = await request.post(`http://127.0.0.1:8124/api/runs/${runId}/sources/kosis`, {
    data: { user_stats_id: "missing-key-fixture" },
  });
  expect(failedTool.status()).toBe(400);

  await page.goto("http://127.0.0.1:8125");
  await page.getByTestId("run-filter").selectOption(runId);
  const feed = page.getByTestId("event-feed");
  await expect(feed).toContainText("tool_call");
  await expect(feed).toContainText("tool_result");
  await expect(feed).toContainText("kosis.statistics_openapi");
  await expect(feed).toContainText('"type": "tool.started"');
  await expect(feed).toContainText('"type": "tool.failed"');
  await expect(feed.getByText('"type": "tool.failed"', { exact: false })).toBeVisible();
  await page.screenshot({ path: "playwright-results/raw-event-monitor.png", fullPage: true });
});
