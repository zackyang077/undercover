import { expect, test, type APIRequestContext } from "@playwright/test";

const CASES = { held: "CLM-1013", referred: "CLM-1014" };

async function activate(
  request: APIRequestContext,
  run: string,
  claim: string,
) {
  const response = await request.post(`${run}/claims/${claim}/activate`);
  await expect(response).toBeOK();
  const { thread_id } = (await response.json()) as { thread_id: string };
  expect(thread_id).toBeTruthy();
  return thread_id;
}

async function settle(
  request: APIRequestContext,
  run: string,
  claim: string,
  thread: string,
  outcome: "approve" | "refer",
) {
  const decision = await request.post(`/api/claims/${claim}/decision`, {
    data: {
      outcome,
      payout_amount: outcome === "approve" ? 1250 : 0,
      explanation: "This decision was recorded by the isolated browser test.",
      calculation: "The stub uses a fixed payout for the approved fixture.",
      policy_references: ["11"],
      manual_references: ["2"],
    },
  });
  await expect(decision).toBeOK();
  const finished = await request.post(
    `${run}/claims/${claim}/${thread}/finish`,
    {
      data: { error: "" },
    },
  );
  await expect(finished).toBeOK();
}

test("starts a dispatch through the agent service and displays recorded results", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const control = page.getByRole("region", { name: "Queue processing" });
  const agents = control.getByRole("combobox", {
    name: "Claim agents at once",
  });
  await expect(agents).toHaveValue("3");
  await agents.selectOption("7");
  const start = control.getByRole("button", { name: "Start dispatch" });
  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/dispatch") &&
      response.request().method() === "POST",
  );
  await start.click();
  const started = await accepted;
  expect(started.status()).toBe(202);
  const { run_id } = (await started.json()) as { run_id: string };
  const observations = await request.get(
    "http://127.0.0.1:8003/dispatch-observations",
  );
  expect(await observations.json()).toEqual([
    { run_id, request: { concurrency: 7 } },
  ]);
  await expect(start).toBeDisabled();
  expect((await request.get(`/api/dispatch/${run_id}`)).status()).toBe(404);

  const created = await request.post("/api/swarm/runs", {
    data: {
      run_id,
      kind: "dispatch",
      model: "browser-stub",
      reasoning_effort: null,
      dataset: null,
      size: null,
      concurrency: 7,
    },
  });
  expect(created.status()).toBe(201);
  const run = `/api/swarm/runs/${run_id}`;
  const thread = await activate(request, run, CASES.held);
  const items = control.getByRole("list", { name: "Dispatch claims" });
  await expect(control.getByText("Dispatch running")).toBeVisible();
  await expect(
    items.getByRole("listitem").filter({ hasText: CASES.held }),
  ).toContainText("In progress");
  await page.reload();
  await expect(control.getByText("Dispatch running")).toBeVisible();
  await expect(agents).toHaveValue("7");
  await expect(start).toBeDisabled();

  const reports = `/api/runs/${thread}`;
  await expect(
    await request.post(`${reports}/usage`, {
      data: {
        call_id: "browser-call",
        claim_id: CASES.held,
        run_id,
        usage: { cost: 0.0042, total_tokens: 13 },
      },
    }),
  ).toBeOK();
  await expect(
    await request.post(`${reports}/work-segments`, {
      data: {
        segment_id: "browser-segment",
        claim_id: CASES.held,
        run_id,
        started_at: "2026-11-13T08:00:00.000Z",
        finished_at: "2026-11-13T08:00:00.250Z",
        elapsed_ms: 250,
      },
    }),
  ).toBeOK();
  await settle(request, run, CASES.held, thread, "approve");
  const referredThread = await activate(request, run, CASES.referred);
  await settle(request, run, CASES.referred, referredThread, "refer");
  const listed = await request.get(`${run}/work`);
  await expect(listed).toBeOK();
  const claimIds = (await listed.json()) as string[];
  const dispositions = Object.fromEntries(
    claimIds
      .filter((id) => id !== CASES.held && id !== CASES.referred)
      .map((id) => [id, "skipped"]),
  );
  await expect(
    await request.post(`${run}/close`, {
      data: { status: "completed", error: "", dispositions },
    }),
  ).toBeOK();

  await expect(control.getByText("Dispatch completed")).toBeVisible();
  await expect(
    control.getByText("1 approved · 0 declined · 1 referred"),
  ).toBeVisible();
  await expect(
    control.getByText(`Waiting for sign-off · ${CASES.held} ($1,250.00)`),
  ).toBeVisible();
  await expect(
    control.getByText("Model cost $0.0042 · Elapsed time 0.25 sec"),
  ).toBeVisible();
  const blocked = await request.post(`/api/claims/${CASES.held}/payment`, {
    data: { amount: 1250 },
  });
  expect(blocked.status()).toBe(403);
  await items.getByRole("link", { name: CASES.held }).click();
  await expect(page.locator(".claim-heading")).toContainText(CASES.held);
  await expect(page).toHaveURL(new RegExp(`claim=${CASES.held}$`));
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
