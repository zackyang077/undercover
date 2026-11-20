import { expect, test, type APIRequestContext } from "./session";

interface Job {
  version: number;
  run_id: string;
  dataset: string;
  concurrency: number;
}
async function finishBaseline(request: APIRequestContext, id: string) {
  const next = `/api/improver/rounds/${id}/next-evaluation`;
  for (let index = 0; index < 3; index++) {
    const response = await request.post(next, { data: {} });
    await expect(response).toBeOK();
    const job = (await response.json()) as Job;
    await expect(
      await request.post(`/api/swarm/runs/versions/${String(job.version)}`, {
        data: {
          run_id: job.run_id,
          kind: "evaluation",
          dataset: job.dataset,
          size: "full",
          concurrency: job.concurrency,
          model: "openai/gpt-6-luna",
          reasoning_effort: "low",
        },
      }),
    ).toBeOK();
    await expect(
      await request.post(`/api/swarm/runs/${job.run_id}/close`, {
        data: { status: "completed", error: "", dispositions: {} },
      }),
    ).toBeOK();
  }
  const complete = await request.post(next, { data: {} });
  await expect(complete).toBeOK();
  expect(await complete.json()).toMatchObject({
    done: true,
    status: "working",
  });
}

test("reviews a candidate proposed through the round API and keeps the deployed version until a verdict", async ({
  page,
  request,
  handlerHeaders,
}) => {
  await page.getByRole("button", { name: "Improvement", exact: true }).click();
  const start = page.getByRole("button", { name: "Start round", exact: true });
  await start.click();
  const accepted = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/improvement/rounds") &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Start round" })
    .click();
  const response = await accepted;
  expect(response.ok()).toBe(true);
  const { id } = (await response.json()) as { id: string };
  await finishBaseline(request, id);
  const proposal = await request.post(`/api/improver/rounds/${id}/proposal`, {
    data: {
      kind: "configuration",
      args: {
        instructions: "State the policy section for each decision.",
        model: "openai/gpt-6-luna",
        reasoning_effort: "low",
        reason: "Clarify the grounds for the decision.",
      },
    },
  });
  await expect(proposal).toBeOK();
  const control = page.getByRole("region", { name: "Current round" });
  await expect(control).toContainText("Configuration awaiting review");
  await expect(control.getByLabel("Text changes")).toContainText(
    "+State the policy section",
  );
  await control.getByRole("button", { name: "Approve proposal" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Approve proposal" })
    .click();
  await expect(
    control.getByRole("button", { name: "Approve proposal" }),
  ).toHaveCount(0);
  const view = await request.get("/api/improvement", {
    headers: handlerHeaders,
  });
  const snapshot = (await view.json()) as {
    current_version: number;
    rounds: {
      id: string;
      reviews: { choice: string; handler_id: string }[];
      version: number;
    }[];
  };
  expect(snapshot.current_version).toBe(1);
  expect(snapshot.rounds.find((r) => r.id === id)).toMatchObject({
    version: 2,
    reviews: [
      expect.objectContaining({ choice: "approve", handler_id: "HND-01" }),
    ],
  });
  await control.getByRole("button", { name: "Stop round" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Stop round" })
    .click();
  await expect(
    control.getByRole("button", { name: "Start round" }),
  ).toBeVisible();
});
