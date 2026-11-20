import { useState, type SubmitEvent } from "react";
import { api, message, money } from "./api";
import { RemotePanel } from "./RemotePanel";
import { usePageData } from "./usePageData";
import { Confirm } from "./Review";
import { chooseTheme, preferredTheme, type Theme } from "./theme";

interface Measures {
  accuracy: number;
  grade: number;
  cost_per_claim: number;
  seconds_per_claim: number;
}
interface Values {
  approval_limit: number;
  budget: number;
  concurrency: number;
  objectives: Pick<Measures, "accuracy" | "grade">;
  noise: Measures;
  tolerance: Measures;
}
interface SettingsView {
  settings: Values;
  models: {
    model: string;
    reasoning_efforts: string[];
    input_price: number;
    output_price: number;
  }[];
  history: {
    handler_id: string;
    created_at: string;
    before: Values;
    after: Values;
  }[];
}
interface Field {
  name: keyof Measures;
  label: string;
  scale: number;
  max?: number;
}
const measures: Field[] = [
  {
    name: "accuracy",
    label: "Accuracy (percentage points)",
    scale: 100,
    max: 100,
  },
  { name: "grade", label: "Explanation grade (points)", scale: 1, max: 3 },
  { name: "cost_per_claim", label: "Cost per claim (%)", scale: 100 },
  { name: "seconds_per_claim", label: "Time per claim (%)", scale: 100 },
];
const objectives: Field[] = [
  { name: "accuracy", label: "Minimum accuracy (%)", scale: 100, max: 100 },
  { name: "grade", label: "Minimum explanation grade (0–3)", scale: 1, max: 3 },
];
const limits = [
  { name: "approval_limit", label: "Approval limit (USD)", min: 0, step: 0.01 },
  {
    name: "budget",
    label: "Spending limit per round (USD)",
    min: 0,
    step: "any",
  },
  {
    name: "concurrency",
    label: "Concurrent claim agents",
    min: 1,
    step: 1,
    max: 25,
  },
] as const;
const groups = [
  { name: "objectives", label: "Held-out objectives", fields: objectives },
  { name: "noise", label: "Minimum improvement above noise", fields: measures },
  { name: "tolerance", label: "Maximum accepted regression", fields: measures },
] as const;

function settingsFields(values: Values) {
  const scalar = limits.map((field) => ({
    ...field,
    value: values[field.name],
    group: "Platform limits",
  }));
  const thresholds = groups.flatMap((group) =>
    group.fields.map((field) => ({
      name: `${group.name}.${field.name}`,
      label: field.label,
      group: group.label,
      value: Number(
        (
          Number((values[group.name] as Partial<Measures>)[field.name]) *
          field.scale
        ).toPrecision(12),
      ),
      min: 0,
      step: "any",
      max: field.max,
    })),
  );
  return [...scalar, ...thresholds];
}

function edits(before: Values, after: Values) {
  const baseline = settingsFields(before);
  return settingsFields(after).flatMap((field, index) => {
    const original = baseline[index];
    return field.value === original.value
      ? []
      : [
          `${field.group} · ${field.label}: ${String(original.value)} → ${String(field.value)}`,
        ];
  });
}

function readSettings(form: HTMLFormElement, previous: Values): Values {
  const data = new FormData(form);
  const values = structuredClone(previous);
  for (const field of limits) values[field.name] = Number(data.get(field.name));
  for (const group of groups) {
    const section: Partial<Measures> = values[group.name];
    for (const field of group.fields)
      section[field.name] =
        Number(data.get(`${group.name}.${field.name}`)) / field.scale;
  }
  return values;
}

function Appearance() {
  const [theme, setTheme] = useState<Theme>(preferredTheme);
  const [pending, setPending] = useState<Theme | null>(null);
  return (
    <section className="panel settings-appearance">
      <h2>Appearance</h2>
      <label htmlFor="appearance">Appearance</label>
      <select
        id="appearance"
        value={theme}
        onChange={(event) => {
          setPending(event.target.value as Theme);
        }}
      >
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
      {pending && (
        <Confirm
          title="Change appearance?"
          confirmLabel="Apply appearance"
          onConfirm={() => {
            chooseTheme(pending);
            setTheme(pending);
            setPending(null);
          }}
          onCancel={() => {
            setPending(null);
          }}
        >
          <p>
            This browser will use the{" "}
            {pending === "system"
              ? "computer’s color scheme"
              : pending + " theme"}
            .
          </p>
        </Confirm>
      )}
    </section>
  );
}

function SettingsForm({
  settings,
  writable,
  onSaved,
}: Readonly<{
  settings: Values;
  writable: boolean;
  onSaved: (value: SettingsView) => void;
}>) {
  const [candidate, setCandidate] = useState<Values | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const fields = settingsFields(settings);
  function review(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    const proposed = readSettings(event.currentTarget, settings);
    if (edits(settings, proposed).length) setCandidate(proposed);
    else setNotice("The settings are already up to date.");
  }
  async function save(proposed: Values) {
    setCandidate(null);
    setBusy(true);
    try {
      onSaved(await api<SettingsView>("/settings", proposed, "PUT"));
      setNotice("Settings saved.");
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="panel settings-form" onSubmit={review}>
      <h2>Platform settings</h2>
      {!writable && (
        <p className="muted">
          Only a senior handler can change platform settings.
        </p>
      )}
      {["Platform limits", ...groups.map((g) => g.label)].map((group) => (
        <fieldset disabled={!writable || busy} key={group}>
          <legend>{group}</legend>
          <div className="settings-fields">
            {fields
              .filter((field) => field.group === group)
              .map((field) => (
                <label key={field.name}>
                  {field.label}
                  <input
                    type="number"
                    name={field.name}
                    required
                    defaultValue={field.value}
                    min={field.min}
                    step={field.step}
                    max={"max" in field ? field.max : undefined}
                  />
                </label>
              ))}
          </div>
        </fieldset>
      ))}
      {writable && (
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : "Save settings"}
        </button>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error} Review the values and try again.</p>}
      {candidate && (
        <Confirm
          title="Save platform settings?"
          confirmLabel="Confirm changes"
          onCancel={() => {
            setCandidate(null);
          }}
          onConfirm={() => {
            void save(candidate);
          }}
        >
          <ul>
            {edits(settings, candidate).map((change) => (
              <li key={change}>{change}</li>
            ))}
          </ul>
        </Confirm>
      )}
    </form>
  );
}

export function Settings({ writable }: Readonly<{ writable: boolean }>) {
  const remote = usePageData<SettingsView>("/settings");
  if (!remote.data) return <RemotePanel page="settings" {...remote} />;
  const { settings, models, history } = remote.data;
  return (
    <div className="platform-page">
      <SettingsForm
        settings={settings}
        writable={writable}
        onSaved={remote.setData}
      />
      <section className="panel">
        <h2>Allowed claim models</h2>
        <div className="table-scroll">
          <table className="platform-table">
            <caption>Prices per million tokens in USD</caption>
            <thead>
              <tr>
                <th>Model</th>
                <th>Reasoning efforts</th>
                <th className="numeric">Input</th>
                <th className="numeric">Output</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.model}>
                  <th scope="row">{m.model}</th>
                  <td>{m.reasoning_efforts.join(", ")}</td>
                  <td className="numeric">{money(m.input_price)}</td>
                  <td className="numeric">{money(m.output_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <h2>Change history</h2>
        {history.length ? (
          <ol className="settings-history">
            {history.map((item, index) => (
              <li key={item.created_at + String(index)}>
                <strong>{item.handler_id}</strong> ·{" "}
                <time dateTime={item.created_at}>
                  {new Date(item.created_at).toLocaleString("en-US")}
                </time>
                <ul>
                  {edits(item.before, item.after).map((change) => (
                    <li key={change}>{change}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">
            No changes recorded. The platform is using its initial settings.
          </p>
        )}
      </section>
      <Appearance />
    </div>
  );
}
