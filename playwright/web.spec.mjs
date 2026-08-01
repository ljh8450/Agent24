import { expect, test } from "@playwright/test";
import { submitPolicy } from "./fixtures.mjs";

test("sidebar toggle collapses and expands the desktop navigation", async ({ page }) => {
  await page.goto("/");
  const shell = page.getByTestId("app-shell");
  const toggle = page.getByRole("button", { name: "사이드바 접기" });
  await toggle.click();
  await expect(shell).toHaveClass(/is-sidebar-collapsed/);
  await expect(page.getByRole("button", { name: "사이드바 펼치기" })).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "사이드바 펼치기" }).click();
  await expect(shell).not.toHaveClass(/is-sidebar-collapsed/);
  await expect(page.getByRole("button", { name: "사이드바 접기" })).toHaveAttribute("aria-expanded", "true");
});

test("one policy sentence completes the agent workflow without manual controls", async ({ page }) => {
  await submitPolicy(page);
  await expect(page.getByText("검토 완료", { exact: true })).toBeVisible();
  await expect(page.locator(".execution-history")).toContainText("정책 보고서 생성");
  await expect(page.getByTestId("streaming-response").last()).toContainText("정책 검토를 끝까지 완료했습니다");
  await expect(page.locator(".policy-plan")).toContainText("권리·법률 선행 검토");
  await expect(page.getByText("NOT_FOUND", { exact: false })).toHaveCount(0);
  await expect(page.getByTestId("manual-lab")).toHaveCount(0);
  await expect(page.getByTestId("question-input")).toBeEnabled();
  await expect(page.getByTestId("start-research")).toBeEnabled();
  await page.screenshot({ path: "playwright-results/autonomous-policy-review.png", fullPage: true });
});

test("artifacts and provenance surface as read-only document cards in the chat", async ({ page }) => {
  await submitPolicy(page);
  const openers = page.locator("#artifact-openers-block");
  await expect(openers).toContainText("종합 보고서");
  await expect(openers).toContainText("증거·프로버넌스");
  await expect(page.locator(".study-inspector")).toBeHidden();
  await expect(page.locator("#policy-review input, #policy-review textarea, #policy-review select")).toHaveCount(0);
});

test("scenario-only PGM keeps Notionists synthetic profiles wired to the panel surface", async ({ page }) => {
  await submitPolicy(page);
  await expect.poll(() => page.locator("#distribution-tag").textContent()).toBe("SCENARIO ONLY");
  await expect.poll(() => page.getByTestId("swarm-personas").textContent()).toContain("age_eligibility_band:18_27");
  const avatars = page.getByTestId("swarm-personas").locator("img");
  await expect(avatars).toHaveCount(2);
  const firstAvatar = avatars.first();
  await expect(firstAvatar).toHaveAttribute("src", /\/api\/avatars\/notionists\/[a-f0-9]{24}\.svg/);
  await expect(firstAvatar).toHaveAttribute("alt", /장식용 Notionists 합성 아바타/);
});
