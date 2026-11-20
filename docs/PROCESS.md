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
3. Tick off its verified criteria in the approved build plan (`docs/PLAN_SWARM.md` for the Swarm build) and update `docs/STATUS.md`.
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

## Swarm acceptance

Run the full checks without a project .env. Pytest isolates cloud credentials.
Playwright uses a temporary SQLite backend on 8010, dashboard on 5174, and a
notification stand-in on 8003. The stand-in reads the committed sign-off;
Python integration tests exercise the actual resume and payment nodes.

For the hosted acceptance series, stop the applications and run `npm run seed`
once. Start the default model and use the browser to dispatch at N=3. Stop the
agent service partway, restart `npm run dev`, and dispatch at N=25. Record the
stopped run, original claim threads, overlapping work, and wall time against
total claim time. Use Supabase MCP to sample `postgres` sessions under load,
and measure the time from a browser sign-off to visible payment. Verify the
N=25 dispatch's tag through the Langfuse API. Run the full evaluation set at
N=25 and the held-out tiny set at N=5. Compare the queue before and after both
evaluations and check their Scoreboard measurements. Record all evidence under
the relevant phase in `docs/STATUS.md`.
