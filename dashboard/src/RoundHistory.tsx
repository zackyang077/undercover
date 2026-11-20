import { Diff } from "./Review";
import { ComparisonTable } from "./RoundScores";
import {
  cost,
  dateLabel,
  roundStage,
  roundVerdict,
  type Round,
  type ImprovementView,
  type Measures,
} from "./improvement-types";

function SettingsSnapshot({
  settings,
}: Readonly<{ settings: Round["settings"] }>) {
  const fields: {
    key: keyof Measures;
    label: string;
    unit: string;
    scale: number;
  }[] = [
    { key: "accuracy", label: "Accuracy", unit: "pp", scale: 100 },
    { key: "grade", label: "Grade", unit: "pts", scale: 1 },
    { key: "cost_per_claim", label: "Cost per claim", unit: "%", scale: 100 },
    {
      key: "seconds_per_claim",
      label: "Time per claim",
      unit: "%",
      scale: 100,
    },
  ];
  return (
    <details>
      <summary>Settings at start</summary>
      <p>
        {cost(settings.budget)} budget · {settings.concurrency} concurrent
        agents
      </p>
      <p>
        Required accuracy {(settings.objectives.accuracy * 100).toFixed(0)}% ·
        minimum grade {settings.objectives.grade.toFixed(2)} / 3
      </p>
      <table className="platform-table compact-table">
        <thead>
          <tr>
            <th>Measure</th>
            <th className="numeric">Noise</th>
            <th className="numeric">Tolerance</th>
          </tr>
        </thead>
        <tbody>
          {fields.map(({ key, label, unit, scale }) => (
            <tr key={key}>
              <th scope="row">{label}</th>
              <td className="numeric">
                {(Number(settings.noise[key]) * scale).toFixed(2)} {unit}
              </td>
              <td className="numeric">
                {(Number(settings.tolerance[key]) * scale).toFixed(2)} {unit}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function RoundAudit({ round: r }: Readonly<{ round: Round }>) {
  return (
    <div className="round-audit">
      <p>
        {r.reason ||
          (r.proposal?.args.reason ?? "No configuration change proposed.")}
      </p>
      <p>
        <strong>{r.verdict ? roundVerdict(r) : roundStage(r)}</strong>
        {(r.stop_reason ?? r.why) && " · "}
        {r.stop_reason ?? r.why}
      </p>
      <dl className="facts">
        <div>
          <dt>Started by</dt>
          <dd>
            {r.started_by} · {dateLabel(r.started_at)}
          </dd>
        </div>
        <div>
          <dt>{r.status === "stopped" ? "Stopped" : "Finished"}</dt>
          <dd>
            {r.ended_at ? dateLabel(r.ended_at) : "In progress"}
            {r.stopped_by && ` · ${r.stopped_by}`}
          </dd>
        </div>
        <div>
          <dt>Recipe</dt>
          <dd>Version {r.recipe_version}</dd>
        </div>
        <div>
          <dt>Configuration</dt>
          <dd>
            Version {r.base_version}
            {r.version === null
              ? " · no candidate"
              : ` → version ${String(r.version)}`}
          </dd>
        </div>
      </dl>
      <ComparisonTable before={r.before} after={r.after} />
      <h3>Round costs</h3>
      <dl className="facts">
        {Object.entries(r.costs).map(([key, value]) => (
          <div key={key}>
            <dt>{key === "claim" ? "Claim agents" : key}</dt>
            <dd className="mono">{cost(value)}</dd>
          </div>
        ))}
      </dl>
      <SettingsSnapshot settings={r.settings} />
      {!!r.reviews.length && (
        <details>
          <summary>Review decisions ({r.reviews.length})</summary>
          <ol>
            {r.reviews.map((review, i) => (
              <li key={review.created_at + String(i)}>
                <strong>
                  {review.choice === "approve" ? "Approved" : "Rejected"}
                </strong>{" "}
                · {review.handler_id} · {dateLabel(review.created_at)}
                {review.reason && <p>{review.reason}</p>}
                {typeof review.proposal === "object" && (
                  <>
                    <p>
                      {review.proposal.kind} · {review.proposal.args.reason}
                    </p>
                    <Diff lines={review.proposal.diff} />
                  </>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

export function Rounds({ rounds }: Readonly<{ rounds: Round[] }>) {
  return (
    <section className="panel">
      <h2>Round history</h2>
      {rounds.length ? (
        rounds.map((r, i) => (
          <details className="round-entry" key={r.id}>
            <summary>
              <span>
                <strong>Round {rounds.length - i}</strong> ·{" "}
                {dateLabel(r.started_at)}
              </span>{" "}
              <span>
                {roundVerdict(r)} · {cost(r.costs.total)}
              </span>
            </summary>
            <RoundAudit round={r} />
          </details>
        ))
      ) : (
        <p className="muted">
          Start a round to record its baseline, decisions, and costs.
        </p>
      )}
    </section>
  );
}

export function Recipes({
  recipes,
}: Readonly<{ recipes: ImprovementView["recipes"] }>) {
  return (
    <section className="panel">
      <h2>Recipe versions</h2>
      {recipes.map((r) => (
        <details key={r.version}>
          <summary>
            Recipe {r.version} · {r.reason}
          </summary>
          <p>
            {r.approved_by} · {dateLabel(r.created_at)}
          </p>
          {r.version > 1 && <Diff lines={r.diff} />}
          <pre className="instruction-body">{r.recipe}</pre>
        </details>
      ))}
    </section>
  );
}
