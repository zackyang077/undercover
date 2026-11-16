import { useEffect, useState } from "react";
import { api, label, message } from "./api";
import type { QueueRun } from "./types";

type Props = Readonly<{
  waiting: number;
  onRefresh: () => Promise<void>;
  onRunningChange: (running: boolean) => void;
  onSelect: (claimId: string) => void;
}>;

function Progress({
  run,
  onSelect,
}: Readonly<{ run: QueueRun; onSelect: Props["onSelect"] }>) {
  const finished = run.items.filter(
    (item) =>
      item.status === "completed" ||
      item.status === "failed" ||
      item.status === "skipped",
  ).length;
  const failed = run.items.filter((item) => item.status === "failed");
  const skipped = run.items.filter((item) => item.status === "skipped").length;
  const current = run.items.find((item) => item.status === "running");
  const complete = run.items.filter(
    (item) => item.status === "completed",
  ).length;
  return (
    <div className="queue-progress" role="status">
      <div className="queue-progress-title">
        <strong>
          {run.status === "running"
            ? "Processing queue"
            : `Queue ${label(run.status).toLowerCase()}`}
        </strong>
        <span className="mono">
          {finished} / {run.items.length} processed
        </span>
      </div>
      <progress
        aria-label="Queue progress"
        max={run.items.length}
        value={finished}
      />
      <p>
        {complete} completed · {failed.length} failed · {skipped} skipped
      </p>
      {current && (
        <button
          className="queue-current"
          onClick={() => {
            onSelect(current.claim_id);
          }}
        >
          View current claim: {current.claim_id}
        </button>
      )}
      {run.stop_requested && run.status === "running" && (
        <p>Stopping after the current claim finishes.</p>
      )}
      {run.error && <p className="queue-failure">{run.error}</p>}
      {failed.length > 0 && (
        <details>
          <summary>Failed claims ({failed.length})</summary>
          {failed.map((item) => (
            <div className="queue-failed-item" key={item.claim_id}>
              <button
                className="queue-current"
                onClick={() => {
                  onSelect(item.claim_id);
                }}
              >
                {item.claim_id}
              </button>
              <p>{item.error}</p>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

export function QueueControl({
  waiting,
  onRefresh,
  onRunningChange,
  onSelect,
}: Props) {
  const [run, setRun] = useState<QueueRun | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fetchError, setFetchError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await api<QueueRun | null>("/queue/runs/current");
        await onRefresh();
        if (cancelled) return;
        setRun(next);
        setLoaded(true);
        setFetchError("");
        onRunningChange(next?.status === "running");
        if (next?.status === "running")
          timer = setTimeout(() => {
            void refresh();
          }, 700);
      } catch (failure) {
        if (!cancelled) {
          setFetchError(message(failure));
          setLoaded(true);
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
  }, [revision, onRefresh, onRunningChange]);

  async function act(path: string) {
    setBusy(true);
    setError("");
    try {
      const next = await api<QueueRun>(
        path,
        path === "/queue/runs" ? { mode: "all" } : {},
      );
      setRun(next);
      onRunningChange(next.status === "running");
      if (path === "/queue/runs") onSelect(next.items[0].claim_id);
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
      setRevision((value) => value + 1);
    }
  }
  const running = run?.status === "running";
  return (
    <section className="panel queue-control" aria-label="Queue processing">
      <div className="queue-control-heading">
        <div>
          <h2>Process the queue</h2>
          <p className="muted">
            Run the agent on waiting claims, one at a time. Payouts over $1,000
            still need sign-off.
          </p>
        </div>
        {running ? (
          <button
            className="secondary"
            disabled={busy || run.stop_requested}
            onClick={() => {
              void act(`/queue/runs/${run.queue_run_id}/stop`);
            }}
          >
            Stop after current claim
          </button>
        ) : (
          <button
            className="primary"
            disabled={!loaded || busy || waiting === 0}
            onClick={() => {
              void act("/queue/runs");
            }}
          >
            Start queue ({waiting})
          </button>
        )}
      </div>
      {(error || fetchError) && (
        <p role="alert" className="error-banner">
          {error || fetchError}
        </p>
      )}
      {run && <Progress run={run} onSelect={onSelect} />}
    </section>
  );
}
