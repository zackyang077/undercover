import { useState } from "react";
import { api, label, money } from "./api";
import type { ClaimDetail, Handler } from "./types";

type Props = Readonly<{
  detail: ClaimDetail;
  handlers: Handler[];
  busy: boolean;
  active: boolean;
  act: (action: () => Promise<unknown>) => Promise<void>;
}>;

function Payment({ detail, handlers, busy, active, act }: Props) {
  const [handler, setHandler] = useState("");
  const { decision, payment, sign_off: signed, claim } = detail;
  const needsSignOff = detail.status === "waiting_sign_off";
  const pay = () =>
    api(`/claims/${claim.claim_id}/payment`, {
      amount: decision?.payout_amount,
    });
  return (
    <div className="payment">
      <p>
        <strong>
          {payment ? "Simulated payment recorded" : "Payment pending"}
        </strong>
        {payment && ` · ${money(payment.amount)}`}
      </p>
      {signed && <p className="muted">Signed off by {signed.handler_name}</p>}
      {needsSignOff && (
        <>
          <p>
            Payouts over {money(detail.approval_limit)} require a handler's
            sign-off.
          </p>
          <label htmlFor="handler">Handler</label>
          <select
            id="handler"
            value={handler}
            disabled={busy}
            onChange={(event) => {
              setHandler(event.target.value);
            }}
          >
            <option value="">Select a handler</option>
            {handlers
              .filter((h) => h.can_approve_over_limit)
              .map((h) => (
                <option key={h.handler_id} value={h.handler_id}>
                  {h.name}
                </option>
              ))}
          </select>
          <button
            className="primary"
            disabled={!handler || busy}
            onClick={() => {
              void act(async () => {
                await api(`/claims/${claim.claim_id}/sign-off`, {
                  handler_id: handler,
                });
                await pay();
              });
            }}
          >
            Sign off and pay
          </button>
        </>
      )}
      {!needsSignOff && !payment && (
        <button
          className="secondary"
          disabled={busy || active}
          onClick={() => {
            void act(pay);
          }}
        >
          Record payment
        </button>
      )}
    </div>
  );
}

export function DecisionPanel(props: Props) {
  const { decision } = props.detail;
  return (
    <section
      className={`panel decision ${decision?.outcome === "approve" ? "decision-approved" : ""}`}
      aria-label="Agent decision"
      aria-live="polite"
    >
      <div className="decision-overview">
        <div>
          <p className="eyebrow">Agent decision</p>
          <h2>
            {decision?.outcome === "approve"
              ? "Claim approved"
              : "Agent decision"}
          </h2>
          {decision?.outcome !== "approve" && (
            <span className="eyebrow">
              {decision ? label(decision.outcome) : "Ready to review"}
            </span>
          )}
        </div>
        {decision?.outcome === "approve" && (
          <div className="approved-amount">
            <span>Approved payout</span>
            <strong className="mono">{money(decision.payout_amount)}</strong>
          </div>
        )}
      </div>
      {decision ? (
        <>
          {decision.outcome === "approve" && <Payment {...props} />}
          <h3>Decision reason</h3>
          <p className="decision-reason">{decision.explanation}</p>
          <h3>Calculation</h3>
          <p className="calculation">{decision.calculation}</p>
          <p className="muted">
            Policy sections {decision.policy_references.join(", ")}
          </p>
          {decision.manual_references && (
            <p className="muted">
              Manual sections {decision.manual_references.join(", ")}
              {decision.manual_version &&
                ` · Version ${decision.manual_version}`}
            </p>
          )}
        </>
      ) : (
        <p className="empty">
          {props.detail.status === "needs_handler"
            ? "A handler needs to review this claim."
            : "Start the agent to review the evidence and record a decision."}
        </p>
      )}
    </section>
  );
}
