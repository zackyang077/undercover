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

function poll<T>(
  read: () => Promise<T>,
  update: (value: T) => boolean,
  onError: (error: string) => void,
  interval: number,
) {
  let active = true;
  let timer: ReturnType<typeof setTimeout>;
  async function refresh() {
    try {
      const value = await read();
      if (!active) return;
      onError("");
      if (!update(value)) return;
      timer = setTimeout(() => {
        void refresh();
      }, interval);
    } catch (failure) {
      if (!active) return;
      onError(message(failure));
      timer = setTimeout(() => {
        void refresh();
      }, 1500);
    }
  }
  void refresh();
  return () => {
    active = false;
    clearTimeout(timer);
  };
}

function isProcessing(detail: ClaimDetail | null) {
  return (
    detail?.status === "in_progress" ||
    (detail?.decision?.outcome === "approve" &&
      !detail.payment &&
      detail.status !== "waiting_sign_off")
  );
}

function claimStartStatus(
  runs: Run[],
  queueRunning: boolean,
  decided: boolean,
) {
  const active = runs.some((run) => run.status === "running");
  if (active) return { active, title: "Agent is working" };
  if (queueRunning) return { active, title: "Queue is running" };
  const failed = runs.some(
    (run) => run.status === "failed" || run.status === "interrupted",
  );
  return { active, title: failed && !decided ? "Retry agent" : "Start agent" };
}

type Settlement = Pick<ClaimDetail, "status" | "sign_off" | "payment">;

function useClaimDetail(
  claimId: string,
  revision: number,
  queueRunning: boolean,
) {
  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [fetchError, setFetchError] = useState("");
  const approved = detail?.decision?.outcome === "approve";
  const paid = !!detail?.payment;
  useEffect(() => {
    const path = `/claims/${claimId}`;
    if (paid) return;
    if (approved) {
      return poll(
        () => api<Settlement>(`${path}/settlement`),
        (settlement) => {
          setDetail((saved) => saved && { ...saved, ...settlement });
          return (
            !settlement.payment && settlement.status !== "waiting_sign_off"
          );
        },
        setFetchError,
        350,
      );
    }
    return poll(
      () => api<ClaimDetail>(path),
      (next) => {
        setDetail(next);
        return queueRunning || isProcessing(next);
      },
      setFetchError,
      350,
    );
  }, [claimId, revision, queueRunning, approved, paid]);
  return { detail, fetchError };
}

export function Workspace({
  claimId,
  handler,
  wording,
  onChange,
  queueRunning,
}: Readonly<{
  claimId: string;
  handler: Handler;
  wording: Wording;
  onChange: () => Promise<void>;
  queueRunning: boolean;
}>) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [activityError, setActivityError] = useState("");
  const [listError, setListError] = useState("");
  const { detail, fetchError } = useClaimDetail(
    claimId,
    revision,
    queueRunning,
  );
  const status = detail?.status;
  const processing = isProcessing(detail);
  useEffect(
    () =>
      poll(
        () => api<Run[]>(`/claims/${claimId}/runs`),
        (activity) => {
          setRuns(activity);
          return (
            queueRunning ||
            processing ||
            activity.some((run) => run.status === "running")
          );
        },
        setActivityError,
        700,
      ),
    [claimId, revision, queueRunning, processing],
  );
  useEffect(() => {
    if (!status) return;
    return poll(onChange, () => false, setListError, 700);
  }, [status, processing, onChange]);

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
  const { evaluation_copy: evaluationCopy } = detail;
  const { active, title: startLabel } = claimStartStatus(
    runs,
    queueRunning,
    decision !== null,
  );
  let statusLabel = label(detail.status);
  if (detail.status === "waiting_sign_off")
    statusLabel = evaluationCopy ? "Held approval" : "Waiting for sign-off";
  const visibleError = [error, fetchError, activityError, listError].find(
    Boolean,
  );
  return (
    <div className="workspace">
      <section className="panel claim-heading">
        <div>
          <span className="eyebrow mono">{claimId}</span>
          {evaluationCopy && <p className="muted">Evaluation copy</p>}
          <h2>{detail.customer.name}</h2>
          <p>{claim.summary}</p>
        </div>
        <div className="claim-action">
          <span className="eyebrow">Claimed amount</span>
          <strong className="claim-amount mono">
            {money(claim.amount_claimed)}
          </strong>
          <span className={`status ${detail.status}`}>{statusLabel}</span>
          <ClaimStart
            visible={!decision && !evaluationCopy}
            status={detail.status}
            label={startLabel}
            disabled={busy || active || queueRunning}
            onStart={() => {
              void act(() => api(`/claims/${claimId}/runs`, {}));
            }}
          />
        </div>
      </section>
      {visibleError && (
        <p className="error-banner" role="alert">
          {visibleError}
        </p>
      )}
      {!evaluationCopy && detail.status === "needs_handler" && (
        <p className="error-banner" role="status">
          Left for a handler after two unsuccessful attempts. Review the
          activity and decide the next action.
        </p>
      )}
      <DecisionPanel detail={detail} handler={handler} busy={busy} act={act} />
      <div className="work-grid">
        <div>
          <Evidence detail={detail} wording={wording} />
        </div>
        <Activity runs={runs} />
      </div>
    </div>
  );
}
