import { label, money } from "./api";
import type { ClaimSummary } from "./types";

function QueueResult({ claim }: Readonly<{ claim: ClaimSummary }>) {
  if (claim.outcome === "approve") {
    let payment = "Payment pending";
    if (claim.payment_status === "paid") payment = "Paid (simulated)";
    if (claim.status === "waiting_sign_off") payment = "Waiting for sign-off";
    return (
      <span className="queue-result">
        <span className="status decided">
          Approved
          {claim.payout_amount !== null && ` · ${money(claim.payout_amount)}`}
        </span>
        <small>{payment}</small>
      </span>
    );
  }
  return (
    <span className={`status ${claim.status}`}>
      {label(claim.outcome || claim.status)}
    </span>
  );
}

export function ClaimQueue({
  claims,
  selected,
  onSelect,
}: Readonly<{
  claims: ClaimSummary[];
  selected: string;
  onSelect: (claimId: string) => void;
}>) {
  return (
    <aside className="panel queue" aria-label="Claims queue">
      <div className="section-heading">
        <h2>Claims queue</h2>
        <span className="count">{claims.length}</span>
      </div>
      <p className="muted queue-caption">Select a claim to review</p>
      <div className="queue-list">
        {claims.map((claim) => (
          <button
            className={`queue-row ${selected === claim.claim_id ? "selected" : ""}`}
            key={claim.claim_id}
            aria-pressed={selected === claim.claim_id}
            onClick={() => {
              onSelect(claim.claim_id);
            }}
          >
            <span className="queue-meta">
              <span className="mono">{claim.claim_id}</span>
              <span className="mono">
                {money(claim.amount_claimed)} claimed
              </span>
            </span>
            <strong className="customer-name">{claim.customer_name}</strong>
            <span className="queue-summary">{claim.summary}</span>
            <QueueResult claim={claim} />
          </button>
        ))}
      </div>
      {claims.length === 0 && <p className="empty">No claims in the queue.</p>}
    </aside>
  );
}
