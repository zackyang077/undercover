# Process

`uv sync` and `npm install` at the repo root install everything, including a git pre-commit hook. `npm run dev` starts the backend, dashboard and agent service, passing the development environment to all three.

`npm run check` runs every check:

- Python: ruff (lint and format), pyright, and pytest with 80% coverage
- TypeScript: ESLint, Prettier, tsc, and vitest with 80% coverage
- Playwright end-to-end tests

The pre-commit hook runs the format, lint, type and unit test checks on every commit.

## Planning

When asked to plan, as in "Please plan the pilot", write the plan to `docs/PLAN.md` and write no code.

- Split the work into phases, usually 5 or 6, each building on the last.
- Give each phase detailed success criteria that you can verify yourself: tests that pass, commands and their output, API calls and their responses, and pages you check in the browser with the Playwright MCP server. Write each one as a checkbox.
- Then stop and wait until you are asked to implement the plan.

## Implementing

When asked to implement the plan, as in "Please implement the plan for the pilot", build every phase in order without stopping. For each phase:

1. Build it, with tests.
2. Check every success criterion and fix whatever fails.
3. Tick off its criteria in the approved build plan (for this build, `docs/PLAN_DURABLE.md`) and update `docs/STATUS.md`.
4. Commit. The pre-commit hook runs the checks; if it fails, fix the problem and commit again.

When the last phase is done, run `npm run check` and report the results.

## Rules

- Add tests with each change. Tests never call a model; use a stub model.
- Outside a plan, commit each change once you have checked it.
- Run `npm run check` before you hand work over.
- Never use `git commit --no-verify`.
- Never lower a threshold, turn off a rule or exclude a file to get a green run. If a rule looks wrong, stop and ask.
- Keep `docs/STATUS.md` up to date.
- Never push.
- Don't open `data/evals/` unless you're asked to.
- Everything must work on Windows: write `package.json` scripts as `uv run` commands or `.mjs` files.

## Durable claims acceptance

Run the full checks without a project .env. Pytest isolates cloud credentials.
Playwright uses a temporary SQLite backend on 8010, dashboard on 5174, and a
notification stand-in on 8003. The stand-in reads the committed sign-off;
Python integration tests exercise the actual resume and payment nodes.

For each independent live trial, stop applications using the selected product
database and reseed it first. Start the default model with a cap of five distinct
claims. A deliberate process interruption and restart continues the same trial
from its saved checkpoint. Record claim IDs, attempt counts, the browser sign-off
latency, the postgres connection count from Supabase MCP, and trace IDs on both
sides of the sign-off from the Langfuse API.
