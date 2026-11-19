import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { approved, fixture, people, wording } from "../test-data";
import type { ClaimDetail } from "./types";
import { Workspace } from "./Workspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each([
  {
    status: "waiting" as const,
    decision: null,
    message: "This evaluation has no saved decision for the claim.",
  },
  {
    status: "waiting_sign_off" as const,
    decision: { ...approved, payout_amount: 1250 },
    message: "Approval held for sign-off",
  },
])(
  "shows evaluation copy in $status without start or sign-off controls",
  async ({ status, decision, message }) => {
    const claimId = "EVAL-abc";
    const detail: ClaimDetail = {
      ...fixture,
      claim: { ...fixture.claim, claim_id: claimId },
      evaluation_copy: true,
      status,
      decision,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve(Response.json(url.endsWith("/runs") ? [] : detail)),
      ),
    );

    render(
      <Workspace
        claimId={claimId}
        handlers={people}
        wording={wording}
        onChange={() => Promise.resolve()}
        queueRunning={false}
      />,
    );

    expect(await screen.findByText(message)).toBeDefined();
    expect(screen.getByText("Evaluation copy")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Start agent" })).toBeNull();
    expect(screen.queryByLabelText("Handler")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign off" })).toBeNull();
  },
);
