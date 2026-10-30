import AxeBuilder from "@axe-core/playwright";
import { expect, Page, test } from "@playwright/test";

import { mockApi } from "./mock-api";

async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
}

test("the queue lists work most urgent first, with deadlines in words", async ({ page }) => {
  await mockApi(page);
  await page.goto("/queue");
  await expect(page.getByRole("heading", { name: "Work queue" })).toBeVisible();
  const rows = page.getByRole("row");
  await expect(rows.nth(1)).toContainText("Urgent");
  await expect(rows.nth(1)).toContainText("Review certificate");
  await expect(rows.nth(1)).toContainText("No legal deadline");
  await expect(rows.nth(2)).toContainText("Overdue: overdue by 3 hours");
  await expectAccessible(page);
});

test("N takes the next task and opens the review screen", async ({ page }) => {
  const captured = await mockApi(page);
  await page.goto("/queue");
  await expect(page.getByRole("row").nth(1)).toBeVisible();
  await page.keyboard.press("n");
  await expect(page.getByRole("heading", { name: /Review certificate/ })).toBeVisible();
  expect(captured.claims).toBe(1);
});

test("the review screen shows pages beside fields, with flags and confidence explained", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tasks/a0000000-0000-4000-8000-000000000001");
  await expect(page.getByRole("img", { name: "Certificate page 1 of 2" })).toBeVisible();
  const postcode = page.getByLabel("Postcode");
  await expect(postcode).toHaveValue("SW1A 1AA");
  await expect(postcode).toHaveAttribute("aria-invalid", "true");
  await expect(postcode).toHaveAccessibleDescription(/Low confidence.*AI confidence 62% \(low\)/);
  await expect(page.getByText("Contradiction: Outcome \"Satisfactory\" with C2 present.")).toBeVisible();
  await page.keyboard.press("Alt+ArrowRight");
  await expect(page.getByRole("img", { name: "Certificate page 2 of 2" })).toBeVisible();
  await expectAccessible(page);
});

test("an untouched draft is approved; Ctrl+Enter submits and moves to the next task", async ({ page }) => {
  const captured = await mockApi(page, { emptyQueueAfterReview: true });
  await page.goto("/tasks/a0000000-0000-4000-8000-000000000001");
  await expect(page.getByRole("button", { name: /^Approve/ })).toHaveText(/^Approve\b(?! with)/);
  await page.getByLabel("Engineer's name").focus();
  await page.keyboard.press("Control+Enter");
  await expect(page).toHaveURL(/\/queue$/);
  expect(captured.reviews).toHaveLength(1);
  expect(captured.reviews[0].body).toMatchObject({ decision: "approved" });
  expect(captured.reviews[0].idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);
});

test("editing the draft records a correction with the corrected values", async ({ page }) => {
  const captured = await mockApi(page, { emptyQueueAfterReview: true });
  await page.goto("/tasks/a0000000-0000-4000-8000-000000000001");
  await page.getByLabel("Overall outcome").selectOption("unsatisfactory");
  await page.getByRole("button", { name: "Add a defect" }).click();
  await page.getByLabel("Description", { exact: true }).nth(1).fill("Missing bonding to gas pipe");
  await page.getByLabel("Code", { exact: true }).nth(1).selectOption("C2");
  await expect(page.getByRole("status").filter({ hasText: "saved as a correction" })).toBeVisible();
  await page.getByRole("button", { name: /Approve with corrections/ }).click();
  await expect(page).toHaveURL(/\/queue$/);
  expect(captured.reviews[0].body).toMatchObject({
    decision: "corrected",
    approved_result: {
      outcome: "unsatisfactory", uprn: null,
      defects: [{ code: "C2", description: "No RCD on sockets", location: "Kitchen" },
                { code: "C2", description: "Missing bonding to gas pipe", location: null }],
    },
  });
});

test("rejecting needs a reason", async ({ page }) => {
  const captured = await mockApi(page, { emptyQueueAfterReview: true });
  await page.goto("/tasks/a0000000-0000-4000-8000-000000000001");
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Reject this extraction" });
  await expect(dialog.getByRole("button", { name: "Reject extraction" })).toBeDisabled();
  await dialog.getByLabel("Reason").fill("Not an EICR: this is a gas record");
  await dialog.getByRole("button", { name: "Reject extraction" }).click();
  await expect(page).toHaveURL(/\/queue$/);
  expect(captured.reviews[0].body).toEqual({ decision: "rejected", notes: "Not an EICR: this is a gas record" });
});

test("alerts are acknowledged without hiding the deadline", async ({ page }) => {
  const captured = await mockApi(page);
  await page.goto("/alerts");
  await expect(page.getByText(/80% of the time used: due in 1 day/)).toBeVisible();
  await expectAccessible(page);
  await page.getByRole("button", { name: /Acknowledge alert: Investigate significant hazard/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "The deadline itself is unchanged" })).toBeVisible();
  expect(captured.acknowledged).toHaveLength(1);
});

test("shortcuts are listed, and single-key shortcuts can be turned off", async ({ page }) => {
  const captured = await mockApi(page);
  await page.goto("/queue");
  await expect(page.getByRole("row").nth(1)).toBeVisible();
  await page.keyboard.press("?");
  const dialog = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(dialog).toBeVisible();
  await expectAccessible(page);
  await expect(dialog.getByRole("switch")).toBeChecked();
  await dialog.getByText(/Single-key shortcuts/).click();
  await expect(dialog.getByRole("switch")).not.toBeChecked();
  await dialog.getByRole("button", { name: "Close" }).click();
  await page.keyboard.press("n");
  await page.waitForTimeout(300);
  expect(captured.claims).toBe(0);
});

test("the sign-in page is accessible and says SSO is coming", async ({ page }) => {
  await page.goto("/sign-in");
  await expect(page.getByText(/Single sign-on with multi-factor authentication will replace this page/)).toBeVisible();
  await expectAccessible(page);
});

test("dates are day/month/year, and impossible dates are caught before submitting", async ({ page }) => {
  const captured = await mockApi(page, { emptyQueueAfterReview: true });
  await page.goto("/tasks/a0000000-0000-4000-8000-000000000001");
  const due = page.getByLabel("Next inspection due");
  await expect(due).toHaveValue("14/09/2031");
  await due.fill("09/31/2031"); // month-first by mistake
  await page.getByRole("button", { name: /Approve with corrections/ }).click();
  await expect(due).toBeFocused();
  await expect(due).toHaveAccessibleDescription(/Enter a real date as day\/month\/year/);
  expect(captured.reviews).toHaveLength(0);
  await due.fill("13/09/2031");
  await page.getByRole("button", { name: /Approve with corrections/ }).click();
  await expect(page).toHaveURL(/\/queue$/);
  expect(captured.reviews[0].body).toMatchObject({ decision: "corrected",
    approved_result: { next_inspection_due: "2031-09-13", inspection_date: "2026-09-14" } });
});
