# Status

Pilot phase 1 is built: SQLite imports the 25 queue claims and linked policies,
customers, plans, and handlers once. Read APIs expose full claim evidence and
policy wording. Evaluation and manual files are excluded from loading.

Run `uv sync` and `npm install` at the root, then `npm run dev`.
The API is at http://127.0.0.1:8000 and dashboard at http://localhost:5173.
The default local database is `undercover.sqlite3` (ignored by git).
Set `UNDERCOVER_DB` to use another database.

Validation: phase 1 has five passing Python tests with 99.49% coverage.
The dashboard and agent still have their skeleton behavior.

Phase 2: final decisions, handler sign-off, and simulated payments are built.
The backend blocks unsigned payouts above $1,000, mismatched amounts, and
duplicate payments, including concurrent attempts. Decisions and payments persist.
Validation: 13 Python tests pass, 99.67% coverage; full npm run check passes.

Phase 3: the claims agent reads instructions and evidence through backend HTTP
tools, records a policy-based decision, and separately requests simulated payment.
Tools are bound to one claim; no sign-off tool is exposed. Automated tests use
scripted models only. Default model and MODEL_NAME override are tested.
Validation: npm run check passes; 18 Python tests, 99.70% coverage.

Phase 4: asynchronous local runs, incremental tool activity, explicit retries,
and restart interruption are built. One active run is allowed per claim.
Failures preserve saved decisions/payments. Runs execute in one backend process;
there is no durable execution or automatic resumption.
Validation: npm run check passes; 26 Python tests, 99.57% coverage.

Phase 5: branded responsive claims desk, full evidence, read-only instructions,
activity, explicit retry, and sign-off/payment flows are built. Selected claims
survive reload via the URL. Fonts are bundled locally through Fontsource.
Validation: 9 Vitest tests, 98.31% statement coverage; browser scenarios and
desktop/mobile screenshots pass. Playwright MCP is unavailable, and CUA returned
Browser access host unavailable. Manual MCP inspection remains unverified;
automated Playwright and screenshot review were used instead.
