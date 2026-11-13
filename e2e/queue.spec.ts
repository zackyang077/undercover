import { expect, test } from "@playwright/test";
import type { ClaimSummary, QueueRun } from "../dashboard/src/types";

test("one-click queue processing survives reload, stops safely, and continues after one failure", async ({
  page,
  request,
}) => {
  const response = await request.get("/api/claims");
  const body: unknown = await response.json();
  const claims = body as ClaimSummary[];
  const keep = new Set(["CLM-1009", "CLM-1010", "CLM-1011"]);
  for (const claim of claims.filter(
    (c) => c.status === "waiting" && !keep.has(c.claim_id),
  )) {
    const saved = await request.post(`/api/claims/${claim.claim_id}/decision`, {
      data: {
        outcome: "refer",
        payout_amount: 0,
        explanation:
          "This is an isolated browser fixture. The claim is outside this queue scenario.",
        calculation: "No payout in this test fixture.",
        policy_references: ["11"],
      },
    });
    expect(saved.ok()).toBe(true);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const control = page.getByRole("region", { name: "Queue processing" });
  await control.getByRole("button", { name: "Start queue (3)" }).click();
  await expect(control.getByText("Processing queue")).toBeVisible();
  const duplicate = await request.post("/api/queue/runs");
  expect(duplicate.status()).toBe(409);
  await page.reload();
  await expect(control.getByText("Processing queue")).toBeVisible();
  await expect(
    control.getByRole("button", { name: "View current claim: CLM-1009" }),
  ).toBeVisible();
  await control
    .getByRole("button", { name: "Stop after current claim" })
    .click();
  await expect(control.getByText("Queue stopped")).toBeVisible();
  await expect(control.getByText("1 / 3 processed")).toBeVisible();
  const blocked = await request.post("/api/claims/CLM-1009/payment", {
    data: { amount: 1250 },
  });
  expect(blocked.status()).toBe(403);
  await page.reload();
  await expect(control.getByText("Queue stopped")).toBeVisible();
  await control.getByRole("button", { name: "Start queue (2)" }).click();
  await expect(control.getByText("Queue completed")).toBeVisible();
  await expect(
    control.getByText("1 completed · 1 failed · 0 skipped"),
  ).toBeVisible();
  await control.getByText("Failed claims (1)").click();
  await expect(control.getByText(/Simulated provider failure/)).toBeVisible();
  await control.getByRole("button", { name: "CLM-1010", exact: true }).click();
  await expect(page.locator(".claim-heading")).toContainText("CLM-1010");
  const currentResponse = await request.get("/api/queue/runs/current");
  const latestBody: unknown = await currentResponse.json();
  const latest = latestBody as QueueRun;
  expect(latest.items.map((item) => item.status)).toEqual([
    "failed",
    "completed",
  ]);
  const claimResponse = await request.get("/api/claims/CLM-1011");
  const detail: unknown = await claimResponse.json();
  expect(detail).toMatchObject({
    decision: { outcome: "approve" },
    payment: { simulated: true, amount: 420 },
  });
  const attempts = await request.get("/api/claims/CLM-1010/runs");
  const savedRuns: unknown = await attempts.json();
  expect(savedRuns).toHaveLength(1);
  await control.getByRole("button", { name: "Start queue (1)" }).waitFor();
  await page.screenshot({
    path: "e2e/test-results/queue-completed.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await control.scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "e2e/test-results/queue-mobile.png" });
});
