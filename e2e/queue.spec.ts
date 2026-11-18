import { expect, test, type APIRequestContext } from "@playwright/test";
import type { ClaimSummary, QueueRun } from "../dashboard/src/types";

async function currentQueue(request: APIRequestContext): Promise<QueueRun> {
  const response = await request.get("/api/queue/runs/current");
  return (await response.json()) as QueueRun;
}

test("resumes a stopped batch to its original cap before starting a fresh batch", async ({
  page,
  request,
}) => {
  const response = await request.get("/api/claims");
  const body: unknown = await response.json();
  const claims = body as ClaimSummary[];
  const keep = new Set([
    "CLM-1009",
    "CLM-1010",
    "CLM-1011",
    "CLM-1012",
    "CLM-1013",
    "CLM-1014",
  ]);
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
        manual_references: ["2"],
      },
    });
    expect(saved.ok()).toBe(true);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const control = page.getByRole("region", { name: "Queue processing" });
  await page.getByLabel("Claim limit").fill("2");
  await control.getByRole("button", { name: "Start queue (2)" }).click();
  await expect(control.getByText("Processing queue")).toBeVisible();
  const duplicate = await request.post("/api/queue/runs", {
    data: { mode: "all" },
  });
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
  await expect(control.getByText("1 / 2 processed")).toBeVisible();
  const stopped = await currentQueue(request);
  const blocked = await request.post("/api/claims/CLM-1009/payment", {
    data: { amount: 1250 },
  });
  expect(blocked.status()).toBe(403);
  await page.reload();
  await expect(control.getByText("Queue stopped")).toBeVisible();
  await expect(page.getByLabel("Run scope")).toHaveCount(0);
  await control.getByRole("button", { name: "Resume queue" }).click();
  await expect(control.getByText("2 / 2 processed")).toBeVisible({
    timeout: 15000,
  });
  await expect(control.getByText("Queue completed")).toBeVisible();
  const resumed = await currentQueue(request);
  expect(resumed.queue_run_id).toBe(stopped.queue_run_id);
  expect(resumed.admitted).toEqual(["CLM-1009", "CLM-1010"]);
  expect(resumed.items[0].thread_id).toBe(stopped.items[0].thread_id);
  const firstAttempts = await request.get("/api/claims/CLM-1009/runs");
  expect(await firstAttempts.json()).toHaveLength(1);
  await page.getByLabel("Claim limit").fill("3");
  await control.getByRole("button", { name: "Start queue (3)" }).click();
  await expect(control.getByText("Queue completed")).toBeVisible({
    timeout: 15000,
  });
  await expect(
    control.getByText("2 completed · 1 failed · 0 skipped"),
  ).toBeVisible();
  await control.getByText("Failed claims (1)").click();
  await expect(control.getByText(/provider failure/)).toBeVisible();
  await control.getByRole("button", { name: "CLM-1012", exact: true }).click();
  await expect(page.locator(".claim-heading")).toContainText("CLM-1012");
  const latest = await currentQueue(request);
  expect(latest.queue_run_id).not.toBe(stopped.queue_run_id);
  expect(latest.admitted).toEqual(["CLM-1011", "CLM-1012", "CLM-1013"]);
  expect(latest.items.map((item) => item.status)).toEqual([
    "completed",
    "failed",
    "completed",
    "pending",
  ]);
  const claimResponse = await request.get("/api/claims/CLM-1011");
  const detail: unknown = await claimResponse.json();
  expect(detail).toMatchObject({
    decision: { outcome: "approve" },
    payment: { simulated: true, amount: 420 },
  });
  const attempts = await request.get("/api/claims/CLM-1010/runs");
  const savedRuns: unknown = await attempts.json();
  expect(savedRuns).toHaveLength(2);
  expect(latest.admitted).toHaveLength(3);
  await expect(
    control.getByRole("list", { name: "Run to-do list" }),
  ).toContainText("Left for a handler");
  await expect(
    page.getByText(/Left for a handler after two unsuccessful attempts/),
  ).toBeVisible();
  const untouched = await request.get("/api/claims/CLM-1014");
  expect(await untouched.json()).toMatchObject({
    status: "waiting",
    decision: null,
  });
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
