import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Locator } from "./session";
import { standInUrl } from "./servers";

const selection = { dataset: "held_out", size: "tiny", concurrency: 5 };
const explanation = "The isolated browser worker saved this decision.";

async function createEvaluation(
  request: APIRequestContext,
  runId: string,
  dataset: "evaluation" | "held_out",
) {
  const created = await request.post("/api/swarm/runs", {
    data: {
      ...selection,
      dataset,
      run_id: runId,
      kind: "evaluation",
      model: "browser-evaluation-stub",
      reasoning_effort: null,
    },
  });
  expect(created.status()).toBe(201);
  const listed = await request.get(`/api/swarm/runs/${runId}/work`);
  await expect(listed).toBeOK();
  return (await listed.json()) as string[];
}

async function queueSnapshot(request: APIRequestContext): Promise<unknown> {
  const response = await request.get("/api/claims");
  await expect(response).toBeOK();
  return response.json() as Promise<unknown>;
}

async function playClaim(
  request: APIRequestContext,
  runId: string,
  claimId: string,
  held: boolean,
) {
  const member = `/api/swarm/runs/${runId}/claims/${claimId}`;
  const opened = await request.post(`${member}/activate`);
  await expect(opened).toBeOK();
  const { thread_id } = (await opened.json()) as { thread_id: string };
  expect(thread_id).toBeTruthy();
  const reports = `/api/runs/${thread_id}`;
  const identity = { claim_id: claimId, run_id: runId };
  await expect(
    await request.post(`${reports}/usage`, {
      data: {
        ...identity,
        call_id: "single-reply",
        usage: { cost: 0.0042, total_tokens: 13 },
      },
    }),
  ).toBeOK();
  await expect(
    await request.post(`${reports}/work-segments`, {
      data: {
        ...identity,
        segment_id: "browser-work",
        started_at: "2026-11-13T08:00:00.000Z",
        finished_at: "2026-11-13T08:00:00.250Z",
        elapsed_ms: 250,
      },
    }),
  ).toBeOK();
  await expect(
    await request.post(`/api/claims/${claimId}/decision`, {
      data: {
        outcome: held ? "approve" : "refer",
        payout_amount: held ? 1250 : 0,
        explanation,
        calculation: "This test uses a fixed payout on its approved claim.",
        policy_references: ["11"],
        manual_references: ["2"],
      },
    }),
  ).toBeOK();
  await expect(
    await request.post(`${member}/${thread_id}/finish`, {
      data: { error: "" },
    }),
  ).toBeOK();
}

async function verifyMeasurements(summary: Locator) {
  const expected = {
    "Decision accuracy": "20%",
    Accuracy: "20%",
    "Straight-through rate": "20%",
    "Cost of mistakes": "$1,400.00",
    "Model cost per claim": "$0.0042",
    "Time per claim": "0.25 s",
    "Whole-run time": "0.25 s",
  };
  for (const [name, value] of Object.entries(expected)) {
    const term = summary.page().getByText(name, { exact: true });
    await expect(
      summary.locator("dl > div").filter({ has: term }).locator("dd"),
    ).toHaveText(value);
  }
}

test("evaluates the held-out tiny set and shows persisted comparisons without changing the queue", async ({
  page,
  request,
  handlerHeaders,
}) => {
  const before = await queueSnapshot(request);
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const queue = page.getByRole("region", { name: "Queue overview" });
  const visibleBefore = await queue.innerText();
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await nav.getByText("Scoreboard", { exact: true }).click();
  const form = page.getByRole("form", { name: "Start an evaluation" });
  await form.getByLabel("Dataset").selectOption(selection.dataset);
  await form.getByLabel("Claims to evaluate").selectOption(selection.size);
  const concurrency = form.getByLabel("Claim agents");
  await concurrency.fill(String(selection.concurrency));
  await concurrency.press("Tab");
  const startButton = form.getByRole("button", { name: "Start evaluation" });
  await expect(startButton).toBeFocused();
  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/evaluations") &&
      response.request().method() === "POST",
  );
  await page.keyboard.press("Enter");
  const started = await accepted;
  expect(started.status()).toBe(202);
  const { run_id } = (await started.json()) as { run_id: string };
  const observed = await request.get(`${standInUrl}/evaluation-observations`);
  expect(await observed.json()).toContainEqual({ run_id, request: selection });

  const copies = await createEvaluation(request, run_id, "held_out");
  const run = `/api/swarm/runs/${run_id}`;
  expect(copies).toHaveLength(5);
  expect(new Set(copies).size).toBe(5);
  expect(copies.every((id) => id.startsWith("EVAL-"))).toBe(true);
  const [held, ...referred] = copies;
  const summary = page.getByRole("region", { name: "Evaluation summary" });
  await expect(summary).toContainText("Running · 5 claim agents");

  await playClaim(request, run_id, held, true);
  for (const id of referred) await playClaim(request, run_id, id, false);
  await expect(
    await request.post(`${run}/close`, {
      data: { status: "completed", error: "", dispositions: {} },
    }),
  ).toBeOK();
  await expect(summary).toContainText("Completed · 5 claim agents");
  await expect(summary).toContainText("browser-evaluation-stub");
  await verifyMeasurements(summary);

  const stoppedId = randomUUID();
  const [firstClaim] = await createEvaluation(request, stoppedId, "evaluation");
  await playClaim(request, stoppedId, firstClaim, false);
  await expect(
    await request.post(`/api/swarm/runs/${stoppedId}/close`, {
      data: { status: "stopped", error: "", dispositions: {} },
    }),
  ).toBeOK();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("link", { name: "Back to Scoreboard" }).click();
  const history = page.getByRole("list", { name: "Evaluations" });
  const stoppedLink = `a[href="?evaluation=${stoppedId}"]`;
  const completedLink = `a[href="?evaluation=${run_id}"]`;
  await expect(history.locator(`${stoppedLink}, ${completedLink}`)).toHaveText([
    /Evaluation set · Tiny/,
    /Held-out set · Tiny/,
  ]);
  await history.locator(stoppedLink).press("Enter");
  await expect(summary).toContainText("Stopped · 5 claim agents");
  await page.reload();
  await expect(page).toHaveURL(
    (url) => url.searchParams.get("evaluation") === stoppedId,
  );
  await expect(summary).toContainText("Partial results · 1 of 5 claims worked");
  const comparisons = page.getByRole("region", { name: "Evaluation claims" });
  await expect(comparisons.getByText("No decision")).toHaveCount(4);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("link", { name: "Back to Scoreboard" }).click();
  await history.locator(completedLink).press("Enter");
  await expect(comparisons.getByRole("listitem")).toHaveCount(5);
  await expect(comparisons.getByText("Mismatch", { exact: true })).toHaveCount(
    4,
  );
  const heldComparison = comparisons
    .getByRole("listitem")
    .filter({ hasText: "CLM-1114" });
  await expect(heldComparison).toContainText("Decline · $0.00");
  await expect(heldComparison).toContainText("Approve · $1,250.00");
  await expect(heldComparison).toContainText(explanation);
  const copyLink = heldComparison.getByRole("link", { name: "CLM-1114" });
  await expect(copyLink).toHaveAttribute("href", `?claim=${held}`);
  await copyLink.click();
  const heading = page.locator(".claim-heading");
  await expect(heading).toContainText(held);
  await expect(heading).toContainText("Evaluation copy");
  await expect(
    page.getByText("Approval held for sign-off", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Sign off", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Handler", { exact: true })).toHaveCount(0);
  const blocked = await request.post(`/api/claims/${held}/sign-off`, {
    headers: handlerHeaders,
    data: {},
  });
  expect(blocked.status()).toBe(403);
  await nav.getByText("Claims", { exact: true }).click();
  await expect(queue).toHaveText(visibleBefore, { useInnerText: true });
  expect(await queueSnapshot(request)).toEqual(before);
});
