import { useEffect, useState } from "react";
import { api, label, message } from "./api";
import type { QueueRun, Todo } from "./types";

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
      (item.status === "failed" &&
        run.todos.some(
          (t) => t.claim_id === item.claim_id && t.status === "needs_handler",
        )) ||
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
          {finished} / {run.target} processed
        </span>
      </div>
      <progress aria-label="Queue progress" max={run.target} value={finished} />
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
      <ol className="todo-list" aria-label="Run to-do list">
        {run.todos.map((task) => (
          <li key={task.claim_id} className={`todo-${task.status}`}>
            <button
              className="queue-current"
              onClick={() => {
                onSelect(task.claim_id);
              }}
            >
              View {task.claim_id}
            </button>
            <span>{task.text}</span>
            <strong>{todoLabel(task.status)}</strong>
          </li>
        ))}
      </ol>
      {run.summary && <p className="run-summary">{run.summary}</p>}
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

function RunOptions({
  scope,
  limit,
  busy,
  onScope,
  onLimit,
}: Readonly<{
  scope: string;
  limit: string;
  busy: boolean;
  onScope: (value: string) => void;
  onLimit: (value: string) => void;
}>) {
  return (
    <div className="run-options">
      <label>
        Run scope
        <select
          value={scope}
          onChange={(event) => {
            onScope(event.target.value);
          }}
          disabled={busy}
        >
          <option value="capped">Limited trial</option>
          <option value="all">All waiting claims</option>
        </select>
      </label>
      {scope === "capped" && (
        <label>
          Claim limit
          <input
            type="number"
            min="1"
            step="1"
            value={limit}
            disabled={busy}
            onChange={(event) => {
              onLimit(event.target.value);
            }}
          />
        </label>
      )}
    </div>
  );
}

function validStart(scope: string, limit: string) {
  return (
    scope === "all" || (Number.isInteger(Number(limit)) && Number(limit) > 0)
  );
}

function startBody(scope: string, limit: string) {
  if (scope === "all") return { mode: "all" };
  return { mode: "capped", max_claims: Number(limit) };
}

function todoLabel(status: Todo["status"]) {
  const labels = {
    pending: "To do",
    in_progress: "In progress",
    done: "Done",
    needs_handler: "Left for a handler",
  };
  return labels[status];
}

export function QueueControl({
  waiting,
  onRefresh,
  onRunningChange,
  onSelect,
}: Props) {
  const [scope, setScope] = useState("capped");
  const [limit, setLimit] = useState("5");
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
      const body = path === "/queue/runs" ? startBody(scope, limit) : {};
      const next = await api<QueueRun>(path, body);
      setRun(next);
      onRunningChange(next.status === "running");
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
      setRevision((value) => value + 1);
    }
  }
  const running = run?.status === "running";
  const planned =
    scope === "all" ? waiting : Math.min(waiting, Number(limit) || 0);
  return (
    <section className="panel queue-control" aria-label="Queue processing">
      {!running && (
        <RunOptions
          scope={scope}
          limit={limit}
          busy={busy}
          onScope={setScope}
          onLimit={setLimit}
        />
      )}
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
            disabled={
              !loaded || busy || waiting === 0 || !validStart(scope, limit)
            }
            onClick={() => {
              void act("/queue/runs");
            }}
          >
            Start queue ({planned})
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
