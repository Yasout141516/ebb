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

  // Concepts start unlearned, so the first one opens its lesson. Wait for the streamed text to
  // settle: the lesson card stops being busy once it is complete.
  const lesson = page.getByRole("article", { name: "Lesson" });
  await expect(lesson).toBeVisible({ timeout: 30_000 });
  await expect(lesson.locator('[aria-busy="false"]')).toBeVisible({ timeout: 30_000 });
  await expect(lesson.getByRole("heading", { name: "Check yourself" })).toBeVisible({ timeout: 30_000 });

  // "Answer this for me" on the first check: a plain answer streams in, with follow-up chips.
  await lesson.getByRole("button", { name: "Answer this for me" }).first().click();
  const suggested = page.getByRole("region", { name: "Suggested follow-ups" });
  await expect(suggested).toBeVisible({ timeout: 30_000 });
  await suggested.getByRole("button").first().click();
  await expect(page.getByText("You asked: How is this used in practice?")).toBeVisible({ timeout: 30_000 });

  // On to the quiz. It starts with multiple choice; its options are shuffled, so choose by name
  // wherever the option landed; Enter submits. A wrong answer first: the tutor re-teaches, then
  // asks a short question, which the fake tutor grades (so Costs gets a grading call).
  await page.getByRole("button", { name: "I'm ready, quiz me" }).click();
  await expect(check).toBeVisible({ timeout: 30_000 });
  await page.getByRole("radio", { name: "wrong option A", exact: true }).check();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("article", { name: "Grade: Incorrect" })).toHaveCount(1, { timeout: 30_000 });
  await expect(composer).toBeEnabled({ timeout: 30_000 });
  await composer.fill("right");
  await composer.press("Enter");
  await expect(page.getByRole("article", { name: "Grade: Correct" })).toHaveCount(1, { timeout: 30_000 });

  // The next question is waiting.
  await expect(waiting).toBeVisible({ timeout: 30_000 });

  // The header's Costs link opens the page with the run's calls.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: /Costs/ }).click();
  const recent = page.getByRole("region", { name: "Recent calls" });
  await expect(recent).toBeVisible({ timeout: 30_000 });
  await expect(recent.getByRole("cell", { name: "Lesson", exact: true }).first()).toBeVisible({ timeout: 30_000 });
  await expect(recent.getByRole("cell", { name: "Learn-phase answer", exact: true }).first()).toBeVisible();
  await expect(recent.getByRole("cell", { name: "Write questions", exact: true }).first()).toBeVisible();
  await expect(recent.getByRole("cell", { name: "Grade answer", exact: true }).first()).toBeVisible();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Study" }).click();
  await expect(waiting).toBeVisible({ timeout: 30_000 });

  // End the session: the summary card appears.
  await page.getByRole("button", { name: "End session" }).click();
  await expect(page.getByRole("article", { name: "Session summary" })).toBeVisible({ timeout: 30_000 });

  // The progress page renders for the subject.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Progress" }).click();
  await expect(page.getByRole("heading", { name: "Progress", level: 1 })).toBeVisible({ timeout: 30_000 });
});
