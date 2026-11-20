import { label } from "./api";
import type { Run } from "./types";

function SectionRead({ text }: Readonly<{ text: string }>) {
  const section = JSON.parse(text) as {
    document: string;
    version: string;
    section_id: string;
    title: string;
    content: string;
  };
  return (
    <details className="section-read">
      <summary>
        {label(section.document)} section {section.section_id}: {section.title}{" "}
        · Version {section.version}
      </summary>
      <pre>{section.content}</pre>
    </details>
  );
}

export function Activity({
  runs,
  decided = false,
}: Readonly<{ runs: Run[]; decided?: boolean }>) {
  return (
    <section className="panel activity" aria-label="Agent activity">
      <div className="section-heading">
        <h2>Agent activity</h2>
        {runs.some((run) => run.status === "running") && (
          <span className="live-dot" aria-label="Agent running" />
        )}
      </div>
      <p className="muted">
        Follow the evidence, decisions, and payment steps.
      </p>
      {runs.length === 0 && (
        <p className="empty">
          {decided
            ? "No activity was recorded for this decision."
            : "Activity will appear when the agent starts."}
        </p>
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
                  {event.kind === "section_read" ? (
                    <SectionRead text={event.text} />
                  ) : (
                    <p>
                      {event.kind === "tool_started"
                        ? "In progress"
                        : event.text}
                    </p>
                  )}
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
