import { useEffect, useState } from "react";
import { api, label, message, money } from "./api";
import { Activity } from "./Activity";
import { DecisionPanel } from "./DecisionPanel";
import { Evidence } from "./Evidence";
import type { ClaimDetail, Handler, Run, Wording } from "./types";

export function Workspace({
  claimId,
  handlers,
  wording,
  onChange,
}: Readonly<{
  claimId: string;
  handlers: Handler[];
  wording: Wording;
  onChange: () => Promise<void>;
}>) {
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [next, activity] = await Promise.all([
          api<ClaimDetail>(`/claims/${claimId}`),
          api<Run[]>(`/claims/${claimId}/runs`),
        ]);
        await onChange();
        if (cancelled) return;
        setDetail(next);
        setRuns(activity);
        if (activity.some((run) => run.status === "running")) {
          timer = setTimeout(() => {
            void refresh();
          }, 350);
        }
      } catch (failure) {
        if (!cancelled) setError(message(failure));
      }
    }
    void refresh();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [claimId, revision, onChange]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
      setRevision((value) => value + 1);
    }
  }
  if (!detail)
    return (
      <section className="panel" aria-live="polite">
        {error ? <p role="alert">{error}</p> : <p>Loading claim…</p>}
      </section>
    );
  const { claim, decision } = detail;
  const active = runs.some((run) => run.status === "running");
  const retryable =
    runs.some(
      (run) => run.status === "failed" || run.status === "interrupted",
    ) && !decision;
  let startLabel = "Start agent";
  if (retryable) startLabel = "Retry agent";
  if (active) startLabel = "Agent is working";
  return (
    <div className="workspace">
      <section className="panel claim-heading">
        <div>
          <span className="eyebrow mono">{claimId}</span>
          <h2>{detail.customer.name}</h2>
          <p>{claim.summary}</p>
        </div>
        <div className="claim-action">
          <strong className="claim-amount mono">
            {money(claim.amount_claimed)}
          </strong>
          <span className={`status ${detail.status}`}>
            {label(
              detail.status === "waiting_sign_off"
                ? "waiting for sign-off"
                : detail.status,
            )}
          </span>
          <button
            className="primary"
            disabled={busy || active || decision !== null}
            onClick={() => {
              void act(() => api(`/claims/${claimId}/runs`, {}));
            }}
          >
            {startLabel}
          </button>
        </div>
      </section>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div className="work-grid">
        <div>
          <DecisionPanel
            detail={detail}
            handlers={handlers}
            busy={busy}
            active={active}
            act={act}
          />
          <Evidence detail={detail} wording={wording} />
        </div>
        <Activity runs={runs} />
      </div>
    </div>
  );
}
