import { expect, test, type Page } from "@playwright/test";

async function openClaim(page: Page, claimId: string) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page
    .getByRole("complementary", { name: "Claims queue" })
    .getByRole("button")
    .filter({ hasText: claimId })
    .click();
  await expect(page.locator(".claim-heading")).toContainText(claimId);
}

async function start(page: Page) {
  await page.getByRole("button", { name: "Start agent" }).click();
  await expect(
    page.getByRole("button", { name: "Agent is working" }),
  ).toBeDisabled();
}

test("scripted approval shows activity and records a separate payment", async ({
  page,
  request,
}) => {
  await openClaim(page, "CLM-1001");
  await start(page);
  await page.screenshot({ path: "e2e/test-results/pilot-running.png" });
  await expect(page.getByText("Simulated payment recorded")).toBeVisible();
  await page.screenshot({ path: "e2e/test-results/pilot-decided.png" });
  await expect(
    page.getByRole("region", { name: "Agent activity" }),
  ).toContainText("Read policy wording");
  const response = await request.get("/api/claims/CLM-1001");
  const detail: unknown = await response.json();
  expect(detail).toMatchObject({
    status: "decided",
    decision: { outcome: "approve", payout_amount: 420 },
    payment: { amount_cents: 42000, simulated: true },
  });
  await page.reload();
  await expect(page.getByText("Simulated payment recorded")).toBeVisible();
});

test("over-limit payment is blocked until handler sign-off", async ({
  page,
  request,
}) => {
  await openClaim(page, "CLM-1003");
  await start(page);
  const sign = page.getByRole("button", { name: "Sign off and pay" });
  await expect(sign).toBeVisible();
  await expect(sign).toBeDisabled();
  await page.screenshot({ path: "e2e/test-results/pilot-sign-off.png" });
  const blocked = await request.post("/api/claims/CLM-1003/payment", {
    data: { amount: 3750 },
  });
  expect(blocked.status()).toBe(403);
  await page.getByLabel("Handler").selectOption("HND-01");
  await sign.click();
  await expect(page.getByText("Simulated payment recorded")).toBeVisible();
  await expect(page.getByText("Signed off by Dana Whitfield")).toBeVisible();
  const duplicate = await request.post("/api/claims/CLM-1003/payment", {
    data: { amount: 3750 },
  });
  expect(duplicate.status()).toBe(409);
});

for (const scenario of [
  { id: "CLM-1004", outcome: "Decline" },
  { id: "CLM-1005", outcome: "Refer" },
]) {
  test(`scripted ${scenario.outcome} has no payment action`, async ({
    page,
  }) => {
    await openClaim(page, scenario.id);
    await start(page);
    await expect(
      page
        .getByRole("region", { name: "Agent decision" })
        .getByText(scenario.outcome, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Record payment" }),
    ).toHaveCount(0);
  });
}

test("failed run can be retried without deleting its history", async ({
  page,
  request,
}) => {
  await openClaim(page, "CLM-1006");
  await start(page);
  await expect(page.getByRole("button", { name: "Retry agent" })).toBeVisible();
  await page.screenshot({ path: "e2e/test-results/pilot-failed.png" });
  await page.getByRole("button", { name: "Retry agent" }).click();
  await expect(
    page
      .getByRole("region", { name: "Agent decision" })
      .getByText("Refer", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("region", { name: "Agent activity" }),
  ).toContainText("Simulated provider failure");
  const response = await request.get("/api/claims/CLM-1006/runs");
  const saved: unknown = await response.json();
  expect(saved).toMatchObject([{ status: "failed" }, { status: "completed" }]);
});

test("exact approval boundary is enforced", async ({ page, request }) => {
  await openClaim(page, "CLM-1007");
  await start(page);
  await expect(page.getByText("Simulated payment recorded")).toBeVisible();
  await openClaim(page, "CLM-1008");
  await start(page);
  await expect(
    page.getByRole("button", { name: "Sign off and pay" }),
  ).toBeVisible();
  const blocked = await request.post("/api/claims/CLM-1008/payment", {
    data: { amount: 1000.01 },
  });
  expect(blocked.status()).toBe(403);
});

test("desktop and mobile layouts support evidence, instructions, and keyboard access", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => {
    errors.push(error.message);
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await openClaim(page, "CLM-1002");
  await page
    .locator("summary")
    .filter({ hasText: "Northwind Airlines lost baggage report" })
    .click();
  await expect(
    page.getByText(/property irregularity report PRG-NW/),
  ).toBeVisible();
  await page.evaluate(() => {
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: "e2e/test-results/pilot-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Agent instructions" }).click();
  await expect(
    page.getByRole("heading", { name: "Claims handling instructions" }),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
  await page.screenshot({
    path: "e2e/test-results/pilot-instructions.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Claims", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "Tom Becker" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "e2e/test-results/pilot-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Agent instructions" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Claims handling instructions" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
