import { label } from "./api";
import type { Run } from "./types";

export function Activity({ runs }: Readonly<{ runs: Run[] }>) {
  return (
    <section className="panel activity" aria-label="Agent activity">
      <div className="section-heading">
        <h2>Agent activity</h2>
        <span className="live-dot" />
      </div>
      <p className="muted">
        Follow the evidence, decisions, and payment steps.
      </p>
      {runs.length === 0 && (
        <p className="empty">Activity will appear when the agent starts.</p>
      )}
      {runs.map((run, index) => (
        <div key={run.thread_id}>
          <h3>
            Run {index + 1} <span className="eyebrow">{label(run.status)}</span>
          </h3>
          <ol className="timeline">
            {run.events.map((event) => (
              <li
                key={event.id}
                className={event.kind === "failed" ? "event-error" : ""}
              >
                <span className="event-dot" />
                <div>
                  <strong>
                    {event.tool_name
                      ? label(event.tool_name)
                      : label(event.kind)}
                  </strong>
                  <p>
                    {event.kind === "tool_started" ? "In progress" : event.text}
                  </p>
                  <time dateTime={event.created_at}>
                    {new Date(event.created_at).toLocaleTimeString("en-US")}
                  </time>
                </div>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </section>
  );
}
