import { expect, test } from "@playwright/test";

test("a learner studies a typed topic, ends the session and sees their progress", async ({ page }) => {
  // Onboarding by topic.
  await page.goto("/new");
  await page.getByRole("tab", { name: "Type a topic" }).click();
  await page.getByRole("textbox", { name: "Topic" }).fill("Computer Networks");
  await page.getByRole("button", { name: "Start studying" }).click();
  // `next dev` compiles each route on its first visit, which can take a while.
  await expect(page).toHaveURL(/\/study\//, { timeout: 30_000 });

  const check = page.getByRole("button", { name: "Check answer" });
  const composer = page.getByPlaceholder("Type your answer…");
  const waiting = check.or(composer.and(page.locator(":enabled")));

  // The fake tutor opens a concept with a short question ("right" is graded correct) and follows
  // it with a harder multiple-choice one (its options are shuffled; "right option" is correct).
  await expect(waiting).toBeVisible({ timeout: 30_000 });
  await composer.fill("right");
  await composer.press("Enter");
  await expect(page.getByRole("article", { name: "Grade: Correct" })).toHaveCount(1, { timeout: 30_000 });

  // Multiple choice: choose the option the fake marks correct, wherever it landed; Enter submits.
  await expect(check).toBeVisible({ timeout: 30_000 });
  await page.getByRole("radio", { name: "right option", exact: true }).check();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("article", { name: "Grade: Correct" })).toHaveCount(2, { timeout: 30_000 });

  // The next concept's first question is waiting.
  await expect(waiting).toBeVisible({ timeout: 30_000 });

  // End the session: the summary card appears.
  await page.getByRole("button", { name: "End session" }).click();
  await expect(page.getByRole("article", { name: "Session summary" })).toBeVisible({ timeout: 30_000 });

  // The progress page renders for the subject.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Progress" }).click();
  await expect(page.getByRole("heading", { name: "Progress", level: 1 })).toBeVisible({ timeout: 30_000 });
});
