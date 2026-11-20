import { useEffect, useState, type SubmitEvent } from "react";
import { App } from "./App";
import { api, message } from "./api";
import { SignOut } from "./SignOut";
import { authConfigured, signIn, watchSession } from "./auth";
import type { Handler } from "./types";

function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: SubmitEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(email.trim(), password);
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form
        className="panel login-form"
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <span className="eyebrow">UnderCover Travel Insurance</span>
        <h1>Handler sign-in</h1>
        <label>
          Email
          <input
            required
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
            }}
          />
        </label>
        <label>
          Password
          <input
            required
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
            }}
          />
        </label>
        {error && <p role="alert">{error}</p>}
        <button disabled={busy} className="primary" type="submit">
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

function HandlerDesk() {
  const [handler, setHandler] = useState<Handler | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    void api<Handler>("/me")
      .then((profile) => {
        if (active) setHandler(profile);
      })
      .catch((failure: unknown) => {
        if (active) setError(message(failure));
      });
    return () => {
      active = false;
    };
  }, [attempt]);
  if (handler) return <App handler={handler} />;
  if (!error) return <main role="status">Loading handler profile…</main>;
  return (
    <main className="login">
      <section className="panel">
        <p role="alert">{error}</p>
        <button
          className="secondary"
          onClick={() => {
            setError("");
            setAttempt((n) => n + 1);
          }}
        >
          Try again
        </button>
        <SignOut onError={setError} />
      </section>
    </main>
  );
}

export function HandlerSession() {
  const [userId, setUserId] = useState<string | null>();
  useEffect(() => watchSession(setUserId), []);
  if (!authConfigured)
    return (
      <main role="alert">
        Handler sign-in is unavailable. Set VITE_SUPABASE_URL and
        VITE_SUPABASE_PUBLISHABLE_KEY and restart the dashboard.
      </main>
    );
  if (userId === undefined)
    return <main role="status">Restoring your session…</main>;
  return userId ? <HandlerDesk key={userId} /> : <SignIn />;
}
