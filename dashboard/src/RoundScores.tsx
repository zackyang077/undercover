import type { Measures, Round } from "./improvement-types";
import { cost } from "./improvement-types";

const rows = [
  {
    key: "accuracy",
    label: "Accuracy",
    fmt: (n: number) => `${(n * 100).toFixed(1)}%`,
  },
  {
    key: "grade",
    label: "Explanation grade / 3",
    fmt: (n: number) => n.toFixed(2),
  },
  { key: "cost_per_claim", label: "Cost per claim (USD)", fmt: cost },
  {
    key: "seconds_per_claim",
    label: "Time per claim (s)",
    fmt: (n: number) => n.toFixed(2),
  },
] as const;

export function ComparisonTable({
  before,
  after,
}: Readonly<{ before: Measures | null; after: Measures | null }>) {
  return (
    <table className="platform-table compact-table">
      <caption>Held-out results</caption>
      <thead>
        <tr>
          <th>Measure</th>
          <th className="numeric">Before</th>
          <th className="numeric">After</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ key, label, fmt }) => {
          const a = before?.[key],
            b = after?.[key];
          return (
            <tr key={key}>
              <th scope="row">{label}</th>
              <td className="numeric">{a == null ? "—" : fmt(a)}</td>
              <td className="numeric">{b == null ? "—" : fmt(b)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ChartSeries({
  values,
  x,
  name,
}: Readonly<{
  values: (number | null)[];
  x: (index: number) => number;
  name: string;
}>) {
  let pen = false,
    path = "";
  const y = (v: number) => 118 - v * 96;
  for (const [i, value] of values.entries()) {
    if (value === null) {
      pen = false;
      continue;
    }
    path += `${pen ? " L" : " M"}${String(x(i))} ${String(y(value))}`;
    pen = true;
  }
  return (
    <g className={name}>
      <path d={path} fill="none" stroke="currentColor" strokeWidth="2.5" />
      {values.map((v, i) =>
        v === null ? null : (
          <circle
            key={String(i)}
            cx={x(i)}
            cy={y(v)}
            r="3.5"
            fill="currentColor"
          />
        ),
      )}
    </g>
  );
}

export function RoundChart({ rounds }: Readonly<{ rounds: Round[] }>) {
  const versions = new Map<number, Measures | null>();
  for (const r of [...rounds].reverse()) {
    if (r.before && !versions.has(r.base_version))
      versions.set(r.base_version, r.before);
    if (r.version !== null) versions.set(r.version, r.after);
  }
  const points = [...versions.entries()].sort(([a], [b]) => a - b);
  if (!points.length)
    return (
      <p className="muted">
        Held-out results appear here after a version is evaluated.
      </p>
    );
  const width = Math.max(400, 100 + points.length * 55);
  const x = (i: number) =>
    points.length === 1
      ? width / 2
      : 44 + ((width - 88) * i) / (points.length - 1);
  return (
    <div className="trend-chart">
      <p className="chart-key">
        <span className="accuracy-series">Accuracy (%)</span>
        <span className="grade-series">Grade (0–3)</span>
      </p>
      <div className="chart-scroll">
        <svg
          role="img"
          aria-label="Held-out accuracy and grade by configuration version"
          viewBox={`0 0 ${String(width)} 150`}
          style={{ minWidth: width }}
        >
          {[0, 1, 2].map((i) => {
            const y = 118 - i * 48;
            return (
              <g key={i} className="axis">
                <line x1="38" y1={y} x2={width - 38} y2={y} />
                <text x="32" y={y + 4} textAnchor="end">
                  {i * 50}%
                </text>
                <text x={width - 30} y={y + 4}>
                  {i * 1.5}
                </text>
              </g>
            );
          })}
          <ChartSeries
            values={points.map(([, s]) => s?.accuracy ?? null)}
            x={x}
            name="accuracy-series"
          />
          <ChartSeries
            values={points.map(([, s]) =>
              s?.grade == null ? null : s.grade / 3,
            )}
            x={x}
            name="grade-series"
          />
          {points.map(([v], i) => (
            <text className="axis" key={v} x={x(i)} y="140" textAnchor="middle">
              v{v}
            </text>
          ))}
        </svg>
      </div>
    </div>
  );
}
