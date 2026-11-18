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
  await page.setViewportSize({ width: 1280, height: 900 });
  await openClaim(page, "CLM-1001");
  await start(page);
  await page.screenshot({ path: "e2e/test-results/pilot-running.png" });
  await expect(page.getByText("Simulated payment recorded")).toBeVisible();
  const result = page.getByRole("region", { name: "Agent decision" });
  await expect(
    result.getByRole("heading", { name: "Claim approved" }),
  ).toBeVisible();
  const amount = result.getByText("$420.00", { exact: true });
  await expect(amount).toBeInViewport();
  const queue = page.getByRole("complementary", { name: "Claims queue" });
  await expect(queue.getByText("Approved · $420.00")).toBeVisible();
  await expect(queue.getByText("Paid (simulated)")).toBeVisible();
  await expect(page.getByRole("button", { name: "Start agent" })).toHaveCount(
    0,
  );
  await page.screenshot({ path: "e2e/test-results/pilot-decided.png" });
  await expect(
    page.getByRole("region", { name: "Agent activity" }),
  ).toContainText("Read guidance section");
  const section = page
    .locator(".section-read")
    .filter({ hasText: "Manual section 4" });
  await section.locator("summary").click();
  await expect(section.locator("pre")).toContainText(
    "Work every claim in the same order",
  );
  await page.screenshot({
    path: "e2e/test-results/harness-section.png",
    fullPage: true,
  });
  const response = await request.get("/api/claims/CLM-1001");
  const detail: unknown = await response.json();
  expect(detail).toMatchObject({
    status: "decided",
    decision: { outcome: "approve", payout_amount: 420 },
    payment: { amount_cents: 42000, simulated: true },
  });
  await page.reload();
  await expect(page.getByText("Simulated payment recorded")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await result
    .getByRole("heading", { name: "Claim approved" })
    .scrollIntoViewIfNeeded();
  await expect(amount).toBeInViewport();
  const payoutBox = await amount.boundingBox();
  const activityBox = await page
    .getByRole("region", { name: "Agent activity" })
    .boundingBox();
  expect(payoutBox?.y).toBeLessThan(activityBox?.y ?? 0);
  await page.screenshot({ path: "e2e/test-results/pilot-approved-mobile.png" });
});

test("sign-off reaches the agent service with the committed handler record", async ({
  page,
  request,
}) => {
  await openClaim(page, "CLM-1003");
  await start(page);
  const sign = page.getByRole("button", { name: "Sign off", exact: true });
  await expect(sign).toBeVisible();
  await expect(sign).toBeDisabled();
  await page.screenshot({ path: "e2e/test-results/pilot-sign-off.png" });
  const blocked = await request.post("/api/claims/CLM-1003/payment", {
    data: { amount: 3750 },
  });
  expect(blocked.status()).toBe(403);
  const pagePayments: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().endsWith("/payment")) {
      pagePayments.push(req.url());
    }
  });
  await page.getByLabel("Handler").selectOption("HND-01");
  await sign.click();
  const signed = page.getByText(/Signed off by Dana Whitfield/);
  await expect(signed).toBeVisible();
  const saved = await request.get("/api/claims/CLM-1003");
  const detail = (await saved.json()) as { sign_off: { created_at: string } };
  const signedAt = detail.sign_off.created_at;
  await expect(signed.locator("time")).toHaveAttribute("datetime", signedAt);
  await expect
    .poll(async () => {
      const observations = await request.get(
        "http://127.0.0.1:8003/observations/CLM-1003",
      );
      return observations.json() as Promise<unknown>;
    })
    .toEqual([
      {
        status: "in_progress",
        sign_off: {
          handler_id: "HND-01",
          handler_name: "Dana Whitfield",
          created_at: signedAt,
        },
      },
    ]);
  await page.reload();
  await expect(page.locator(".claim-heading")).toContainText("In progress");
  await expect(signed.locator("time")).toHaveAttribute("datetime", signedAt);
  await expect(sign).toHaveCount(0);
  expect(pagePayments).toEqual([]);
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

test("failed run retries automatically without deleting its history", async ({
  page,
  request,
}) => {
  await openClaim(page, "CLM-1006");
  await start(page);
  await page.screenshot({ path: "e2e/test-results/pilot-failed.png" });
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
    page.getByRole("button", { name: "Sign off", exact: true }),
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
