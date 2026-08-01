import { expect, test } from "@playwright/test";
import { submitPolicy } from "./fixtures.mjs";

const SHOT_DIR = process.env.SPLIT_SHOT_DIR || "playwright-results";

test("artifact cards render as clean file rows in the chat", async ({ page }) => {
  await submitPolicy(page);
  const openers = page.locator("#policy-review .artifact-openers");
  await expect(openers).toBeVisible();
  await expect(openers.locator(".artifact-card")).toHaveCount(5);
  await openers.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOT_DIR}/artifact-cards.png` });
});

test("opening a document splits the workspace instead of covering it", async ({ page }) => {
  await submitPolicy(page);
  const firstCard = page.locator("#policy-review .artifact-card").first();
  await firstCard.scrollIntoViewIfNeeded();

  const mainBefore = await page.locator(".workbench-main").boundingBox();
  await firstCard.getByRole("button", { name: "열기" }).click();

  const drawer = page.locator("#document-drawer");
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

  await page.locator("[data-document-close]").last().click();
  await expect(drawer).not.toHaveClass(/is-open/);
  await expect(drawer).toHaveAttribute("aria-hidden", "true");
  await expect.poll(async () => (await page.locator(".workbench-main").boundingBox()).width, { timeout: 3000 })
    .toBeGreaterThan(mainBefore.width - 10);
});
