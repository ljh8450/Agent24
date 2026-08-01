import { expect, test } from "@playwright/test";
import { submitPolicy } from "./fixtures.mjs";

const SHOT_DIR = process.env.SPLIT_SHOT_DIR || "playwright-results";

test("artifacts render as a compact file list in the chat", async ({ page }) => {
  await submitPolicy(page);
  const openers = page.locator("#artifact-openers-block");
  await expect(openers).toBeVisible();
  await expect(openers.locator(".artifact-file")).toHaveCount(3);
  await openers.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOT_DIR}/artifact-cards.png` });
});

test("opening a document splits the workspace instead of covering it", async ({ page }) => {
  await submitPolicy(page);
  await expect(page.getByText("검토 완료", { exact: true })).toBeVisible();
  const drawer = page.locator("#document-drawer");
  // The persona panel auto-opens once the panel is created — verify, then close for a clean measurement.
  await expect(drawer).toHaveClass(/is-open/);
  await page.locator("[data-document-close]").click();
  await expect(drawer).not.toHaveClass(/is-open/);
  await expect.poll(async () => (await drawer.boundingBox()).width).toBeLessThan(5);
  const firstFile = page.locator("#artifact-openers-block .artifact-file").first();
  await firstFile.scrollIntoViewIfNeeded();

  const mainBefore = await page.locator(".workbench-main").boundingBox();
  await firstFile.click();

  await expect(drawer).toHaveClass(/is-open/);
  await expect(drawer).toHaveAttribute("aria-hidden", "false");

  // The drawer must participate in layout (split), not float above the chat.
  await expect.poll(async () => (await page.locator(".workbench-main").boundingBox()).width, { timeout: 3000 })
    .toBeLessThan(mainBefore.width - 200);
  const position = await drawer.evaluate((node) => getComputedStyle(node).position);
  expect(position).not.toBe("fixed");

  const drawerBox = await drawer.boundingBox();
  const mainAfter = await page.locator(".workbench-main").boundingBox();
  expect(mainAfter.x + mainAfter.width).toBeLessThanOrEqual(drawerBox.x + 1);

  await page.screenshot({ path: `${SHOT_DIR}/document-split-open.png` });

  await page.locator("[data-document-close]").click();
  await expect(drawer).not.toHaveClass(/is-open/);
  await expect(drawer).toHaveAttribute("aria-hidden", "true");
  await expect.poll(async () => (await page.locator(".workbench-main").boundingBox()).width, { timeout: 3000 })
    .toBeGreaterThan(mainBefore.width - 10);
});

test("persona fleet rows appear in chat and open profile cards on the right", async ({ page }) => {
  await submitPolicy(page);
  const fleet = page.locator("#policy-review .persona-fleet");
  await expect(fleet).toBeVisible();
  await expect(fleet.locator(".persona-row")).toHaveCount(2);
  await fleet.locator(".persona-row").first().click();

  await expect(page.locator("#document-drawer")).toHaveClass(/is-open/);
  const native = page.locator("#document-native");
  await expect(native).toBeVisible();
  await expect(native.locator(".persona-detail")).toHaveCount(1);
  await expect(native.locator(".persona-detail")).toContainText("가온");
  await expect(native.locator(".persona-chip")).toHaveCount(2);
  await expect(native.locator(".persona-chip.is-active")).toContainText("가온");
  await expect(native).toContainText("조건부");
  await expect(native).toContainText("원안");
  await expect(native.locator(".persona-answers-table")).toBeVisible();
  await native.locator(".persona-chip", { hasText: "나래" }).click();
  await expect(native.locator(".persona-detail")).toContainText("나래");
  await expect(native.locator(".persona-chip.is-active")).toContainText("나래");
  await expect.poll(async () => (await page.locator("#document-drawer").boundingBox()).width).toBeGreaterThan(400);
  await page.screenshot({ path: `${SHOT_DIR}/persona-panel.png` });
});

test("jsonl and markdown artifacts render natively instead of a blank frame", async ({ page }) => {
  await submitPolicy(page);
  await page.locator("#artifact-openers-block .artifact-file", { hasText: "합성 패널·응답" }).click();
  const native = page.locator("#document-native");
  await expect(native).toBeVisible();
  await expect(native.locator(".persona-card")).toHaveCount(2);
  await expect(page.locator("#document-frame")).toBeHidden();

  await page.locator("#artifact-openers-block .artifact-file", { hasText: "증거·프로버넌스" }).click();
  await expect(native.locator(".native-json")).toContainText("국가법령정보센터");
  // 자동 오픈된 페르소나 패널 탭 + 합성 패널 + 브리프 = 3
  await expect(page.locator("#document-tabs .document-tab")).toHaveCount(3);
  await page.screenshot({ path: `${SHOT_DIR}/native-artifact.png` });
});

test("Enter submits the composer while Shift+Enter adds a newline", async ({ page }) => {
  const { mockAutonomousReview } = await import("./fixtures.mjs");
  await mockAutonomousReview(page);
  await page.goto("/");
  const input = page.getByTestId("question-input");
  await input.fill("첫 줄");
  await input.press("Shift+Enter");
  await input.type("둘째 줄");
  await expect(input).toHaveValue("첫 줄\n둘째 줄");
  await input.press("Enter");
  await expect(page.getByText("검토 완료", { exact: true })).toBeVisible();
  await expect(input).toHaveValue("");
});

test("the streaming cursor stays last and finished answers join the thread in order", async ({ page }) => {
  await submitPolicy(page);
  await expect(page.getByText("검토 완료", { exact: true })).toBeVisible();
  // Finished final answer joins the conversation in order and the artifact list stays at the very bottom;
  // the live-stream tail is empty and remains the last section of the thread.
  await expect(page.locator("#stream-tail .chat-message")).toHaveCount(0);
  const finalMessage = page.locator('#conversation [data-testid="streaming-response"]').last();
  await expect(finalMessage).toContainText("정책 검토를 끝까지 완료했습니다");
  const finalBox = await finalMessage.boundingBox();
  const artifactsBox = await page.locator("#artifact-openers-block").boundingBox();
  expect(artifactsBox.y).toBeGreaterThan(finalBox.y);
  const structure = await page.evaluate(() => ({
    lastThreadSection: document.querySelector(".chat-thread").lastElementChild.id,
    lastConversationChild: document.getElementById("conversation").lastElementChild.id,
  }));
  expect(structure.lastThreadSection).toBe("stream-tail");
  expect(structure.lastConversationChild).toBe("artifact-openers-block");
});

test("conversation lane keeps user and agent turns in chronological order", async ({ page }) => {
  let call = 0;
  await page.route("**/api/agent/review/stream", async (route) => {
    call += 1;
    const reply = call === 1 ? "첫 번째 답변입니다." : "두 번째 답변입니다.";
    const frames = [
      ["chat.accepted", {}],
      ["message.stream.start", { phase: "final" }],
      ["message.delta", { delta: reply }],
      ["chat.completed", { reply }],
    ];
    const body = frames.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");
    await route.fulfill({ contentType: "text/event-stream; charset=utf-8", status: 201, body });
  });
  await page.route("**/api/chats", (route) => route.fulfill({ contentType: "application/json", body: '{"chats":[]}' }));
  await page.goto("/");
  const input = page.getByTestId("question-input");
  await input.fill("안녕?");
  await input.press("Enter");
  await expect(page.locator("#conversation")).toContainText("첫 번째 답변");
  await input.fill("또 안녕");
  await input.press("Enter");
  await expect(page.locator("#conversation")).toContainText("두 번째 답변");
  const order = await page.locator("#conversation .chat-message").allTextContents();
  expect(order[0]).toContain("안녕?");
  expect(order[1]).toContain("첫 번째 답변");
  expect(order[2]).toContain("또 안녕");
  expect(order[3]).toContain("두 번째 답변");
});

test("새 정책 검토 resets the workspace", async ({ page }) => {
  await submitPolicy(page);
  await expect(page.locator("#artifact-openers-block")).toBeVisible();
  await page.locator(".new-study-button").click();
  await expect(page.locator("#conversation")).toBeEmpty();
  await expect(page.locator("#policy-review")).toHaveCount(0);
  await expect(page.locator("#run-title")).toHaveText("새 정책 검토");
  await expect(page.locator("#document-drawer")).not.toHaveClass(/is-open/);
});
