import { useState } from "react";
import { HandlerIdentity } from "./HandlerIdentity";
import { Diff } from "./Review";
import { RemotePanel } from "./RemotePanel";
import { usePageData } from "./usePageData";

interface Version {
  version: number;
  instructions: string;
  model: string;
  reasoning_effort: string;
  reason: string;
  proposed_by: string;
  approved_by: string | null;
  created_at: string;
  diff: string[];
  previous_model: string | null;
  previous_reasoning_effort: string | null;
  held_out: {
    accuracy: number;
    grade: number | null;
    cost_per_claim: number | null;
    seconds_per_claim: number | null;
  } | null;
}
interface Configurations {
  current: number;
  versions: Version[];
}

function VersionDetails({ version }: Readonly<{ version: Version }>) {
  const previous = version.previous_model;
  const fields = [
    [
      "Model",
      previous && previous !== version.model
        ? `${previous} → ${version.model}`
        : version.model,
    ],
    [
      "Reasoning effort",
      version.previous_reasoning_effort &&
      version.previous_reasoning_effort !== version.reasoning_effort
        ? `${version.previous_reasoning_effort} → ${version.reasoning_effort}`
        : version.reasoning_effort,
    ],
    ["Proposed by", version.proposed_by],
    ["Approved by", version.approved_by ?? "Seed configuration"],
    ["Created", new Date(version.created_at).toLocaleString("en-US")],
  ];
  return (
    <section className="panel version-detail">
      <h2>Version {version.version}</h2>
      <p>{version.reason}</p>
      <dl className="facts">
        {fields.map(([name, value]) => (
          <div key={name}>
            <dt>{name}</dt>
            <dd>
              {name === "Proposed by" || name === "Approved by" ? (
                <HandlerIdentity id={value} />
              ) : (
                value
              )}
            </dd>
          </div>
        ))}
      </dl>
      {version.version > 1 && (
        <>
          <h3>Instruction changes</h3>
          <Diff lines={version.diff} />
        </>
      )}
      <h3>Instructions</h3>
      <pre className="instruction-body">{version.instructions}</pre>
    </section>
  );
}

export function Configuration() {
  const remote = usePageData<Configurations>("/configuration");
  const [selected, setSelected] = useState<number>();
  if (!remote.data) return <RemotePanel page="versions" {...remote} />;
  const { current, versions } = remote.data;
  if (!versions.length)
    return (
      <section className="panel">
        No configuration versions yet. Seed the platform to create version 1.
      </section>
    );
  const displayed =
    versions.find((v) => v.version === (selected ?? current)) ?? versions[0];
  return (
    <div className="platform-page">
      <section className="panel">
        <h2>Configuration versions</h2>
        <div className="table-scroll">
          <table className="platform-table">
            <thead>
              <tr>
                <th>Version</th>
                <th>Model</th>
                <th>Effort</th>
                <th className="numeric">Held-out accuracy</th>
                <th className="numeric">Grade / 3</th>
                <th className="numeric">USD / claim</th>
                <th className="numeric">Seconds / claim</th>
              </tr>
            </thead>
            <tbody>
              {[...versions]
                .sort((a, b) => b.version - a.version)
                .map((v) => (
                  <tr key={v.version}>
                    <th scope="row">
                      <button
                        aria-pressed={displayed.version === v.version}
                        onClick={() => {
                          setSelected(v.version);
                        }}
                      >
                        Version {v.version}
                      </button>
                      {v.version === current && (
                        <span className="status decided">Current</span>
                      )}
                    </th>
                    <td>{v.model}</td>
                    <td>{v.reasoning_effort}</td>
                    {v.held_out ? (
                      <>
                        <td className="numeric">
                          {(100 * v.held_out.accuracy).toFixed(1)}%
                        </td>
                        <td className="numeric">
                          {v.held_out.grade?.toFixed(2) ?? "Ungraded"}
                        </td>
                        <td className="numeric">
                          {v.held_out.cost_per_claim?.toFixed(5) ??
                            "Unavailable"}
                        </td>
                        <td className="numeric">
                          {v.held_out.seconds_per_claim?.toFixed(2) ??
                            "Unavailable"}
                        </td>
                      </>
                    ) : (
                      <td colSpan={4} className="muted">
                        Awaiting evaluation
                      </td>
                    )}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </section>
      <VersionDetails version={displayed} />
    </div>
  );
}
