import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Improvement } from "./Improvement";
import type { ImprovementView, Round, Proposal } from "./improvement-types";

const reason = "Clarify the evidence needed for a decision.";
const proposal: Proposal = {
  kind: "configuration",
  id: "proposal-2",
  previous: { model: "luna", reasoning_effort: "low" },
  args: {
    instructions: "Read the receipt.",
    reason,
    model: "luna",
    reasoning_effort: "medium",
  },
  diff: ["-Read the claim.", "+Read the receipt."],
  created_at: "2026-11-20T10:01:00Z",
};
const before = {
  accuracy: 0.7,
  grade: 1.5,
  cost_per_claim: 0.0012,
  seconds_per_claim: 8,
};
const margins = {
  accuracy: 0.1,
  grade: 0.15,
  cost_per_claim: 0.15,
  seconds_per_claim: 0.15,
};
const round: Round = {
  id: "round-1",
  status: "configuration_review",
  progress: 0,
  base_version: 1,
  version: null,
  recipe_version: 1,
  settings: {
    budget: 0.5,
    concurrency: 25,
    objectives: { accuracy: 0.6, grade: 1 },
    noise: margins,
    tolerance: margins,
  },
  reason: "",
  before,
  after: null,
  verdict: null,
  why: "",
  started_at: "2026-11-20T10:00:00Z",
  started_by: "Dana Whitfield",
  ended_at: null,
  proposal,
  reviews: [],
  costs: { total: 0.012, improver: 0.01, claim: 0.001, judge: 0.001 },
};
const view: ImprovementView = {
  rounds: [round],
  current_version: 1,
  recipes: [
    {
      version: 1,
      recipe: "Use the results.",
      reason: "Seeded recipe",
      diff: [],
      approved_by: "Seed",
      created_at: round.started_at,
    },
  ],
};

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("delivers a rejection reason only after confirmation and preserves it while polling", async () => {
  const fetch = vi.fn((_: string, init: RequestInit) =>
    Promise.resolve(
      Response.json(init.method === "POST" ? { ok: true } : view),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  render(<Improvement writable />);
  const reasonInput = await screen.findByLabelText("Rejection reason");
  const reject = screen.getByRole("button", { name: "Reject proposal" });
  expect(reject).toHaveProperty("disabled", true);
  fireEvent.change(reasonInput, {
    target: { value: "Explain how the rule generalizes." },
  });
  expect(
    within(screen.getByRole("region", { name: "Current round" })).getByText(
      reason,
    ),
  ).toBeDefined();
  await waitFor(
    () => {
      expect(fetch.mock.calls.length).toBeGreaterThanOrEqual(2);
    },
    { timeout: 2500 },
  );
  expect(reasonInput).toHaveProperty(
    "value",
    "Explain how the rule generalizes.",
  );
  fireEvent.click(reject);
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain("Explain how the rule generalizes.");
  expect(
    fetch.mock.calls.filter(([, init]) => init.method === "POST"),
  ).toHaveLength(0);
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(reasonInput).toHaveProperty(
    "value",
    "Explain how the rule generalizes.",
  );
  fireEvent.click(reject);
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Reject proposal",
    }),
  );
  expect(fetch).toHaveBeenCalledWith(
    "/api/improvement/rounds/round-1/review",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        proposal_id: "proposal-2",
        choice: "reject",
        reason: "Explain how the rule generalizes.",
      }),
    }),
  );
});

it("shows a recipe diff to a read-only handler", async () => {
  const recipe = {
    ...proposal,
    kind: "recipe",
    args: { recipe: "Generalize rules.", reason: "Avoid overfitting." },
    previous: "Use the results.",
    diff: ["+Generalize rules."],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        Response.json({
          ...view,
          rounds: [{ ...round, status: "recipe_review", proposal: recipe }],
        }),
      ),
    ),
  );
  render(<Improvement writable={false} />);
  const active = await screen.findByRole("region", { name: "Current round" });
  expect(within(active).getByText("Recipe awaiting review")).toBeDefined();
  expect(screen.getByLabelText("Text changes").textContent).toContain(
    "+Generalize rules.",
  );
  expect(screen.queryByRole("button", { name: "Approve proposal" })).toBeNull();
  expect(screen.getByText(/Only a senior handler/)).toBeDefined();
});

it("charts the version tried and shows its decisions, costs, and frozen objectives", async () => {
  const completed: Round = {
    ...round,
    status: "finished",
    reason,
    proposal: null,
    version: 2,
    after: { ...before, grade: null },
    verdict: "rolled_back",
    why: "No graded claims",
    ended_at: proposal.created_at,
    reviews: [
      {
        proposal,
        choice: "approve",
        handler_id: "HND-01",
        reason: "",
        created_at: proposal.created_at,
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(Response.json({ ...view, rounds: [completed] })),
    ),
  );
  render(<Improvement writable />);
  expect(
    await screen.findByRole("img", {
      name: "Held-out accuracy and grade by configuration version",
    }),
  ).toBeDefined();
  fireEvent.click(screen.getByText("Round 1"));
  expect(screen.getByText(/No graded claims/)).toBeDefined();
  expect(screen.getByRole("table", { name: "Held-out results" })).toBeDefined();
  fireEvent.click(screen.getByText("Settings at start"));
  expect(screen.getByText(/Required accuracy 60%/)).toBeDefined();
  fireEvent.click(screen.getByText("Review decisions (1)"));
  expect(screen.getByText("Approved")).toBeDefined();
});

it("starts a round after confirmation and reports a busy service", async () => {
  const fetch = vi.fn((_: string, init: RequestInit) =>
    Promise.resolve(
      init.method === "POST"
        ? Response.json({ detail: "A dispatch is running" }, { status: 409 })
        : Response.json({ ...view, rounds: [] }),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  render(<Improvement writable />);
  fireEvent.click(await screen.findByRole("button", { name: "Start round" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Start round",
    }),
  );
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "A dispatch is running",
  );
});
