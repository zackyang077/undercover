import { useEffect, useState } from "react";
import { api, label, message, money } from "./api";
import { Activity } from "./Activity";
import { DecisionPanel } from "./DecisionPanel";
import { Evidence } from "./Evidence";
import type { ClaimDetail, Handler, Run, Wording } from "./types";

function ClaimStart({
  visible,
  status,
  label: title,
  disabled,
  onStart,
}: Readonly<{
  visible: boolean;
  status: string;
  label: string;
  disabled: boolean;
  onStart: () => void;
}>) {
  if (!visible || status === "needs_handler") return null;
  return (
    <button className="primary" disabled={disabled} onClick={onStart}>
      {title}
    </button>
  );
}

export function Workspace({
  claimId,
  handlers,
  wording,
  onChange,
  queueRunning,
}: Readonly<{
  claimId: string;
  handlers: Handler[];
  wording: Wording;
  onChange: () => Promise<void>;
  queueRunning: boolean;
}>) {
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState("");
  const [fetchError, setFetchError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let polling = queueRunning;
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
        setFetchError("");
        polling =
          queueRunning ||
          next.status === "in_progress" ||
          activity.some((run) => run.status === "running");
        if (polling) {
          timer = setTimeout(() => {
            void refresh();
          }, 350);
        }
      } catch (failure) {
        if (!cancelled) {
          setFetchError(message(failure));
          if (polling)
            timer = setTimeout(() => {
              void refresh();
            }, 1500);
        }
      }
    }
    void refresh();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [claimId, revision, onChange, queueRunning]);

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
        {fetchError ? <p role="alert">{fetchError}</p> : <p>Loading claim…</p>}
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
  if (queueRunning && !active) startLabel = "Queue is running";
  return (
    <div className="workspace">
      <section className="panel claim-heading">
        <div>
          <span className="eyebrow mono">{claimId}</span>
          <h2>{detail.customer.name}</h2>
          <p>{claim.summary}</p>
        </div>
        <div className="claim-action">
          <span className="eyebrow">Claimed amount</span>
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
          <ClaimStart
            visible={!decision}
            status={detail.status}
            label={startLabel}
            disabled={busy || active || queueRunning}
            onStart={() => {
              void act(() => api(`/claims/${claimId}/runs`, {}));
            }}
          />
        </div>
      </section>
      {(error || fetchError) && (
        <p className="error-banner" role="alert">
          {error || fetchError}
        </p>
      )}
      {detail.status === "needs_handler" && (
        <p className="error-banner" role="status">
          Left for a handler after two unsuccessful attempts. Review the
          activity and decide the next action.
        </p>
      )}
      <DecisionPanel
        detail={detail}
        handlers={handlers}
        busy={busy}
        act={act}
      />
      <div className="work-grid">
        <div>
          <Evidence detail={detail} wording={wording} />
        </div>
        <Activity runs={runs} />
      </div>
    </div>
  );
}
