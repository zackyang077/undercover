# Tech stack

- `backend/`: FastAPI and SQLite. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount.
- `agent/`: LangChain's `create_agent` on OpenRouter, with `run(claim_id, thread_id)` for claim work and `run_queue(queue_run_id, thread_id)` for queue planning/delegation. It reaches claims and its instructions only through the backend's API. It uses `deepseek/deepseek-v4.1-flash` with low reasoning effort by default, or the model in the environment variable `MODEL_NAME` if set, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`.

The queue and claim agents use our own HTTP tools and LangChain middleware for
bounded retries, deadlines, and activity. Each claim helper starts with fresh
messages. The backend owns section lookup, cap admission, locks, attempt counts,
to-dos, and result verification. SQLite persists monitoring and business records;
execution remains in one local backend process. Restart marks interrupted work
and requires an explicit new run; this build adds no durable agent graph.

Guidance lookup searches numbered Markdown sections without embeddings. New
decisions include manual/wording references and versions. Existing pilot records
remain readable. `MODEL_NAME` overrides the default for both roles; low reasoning
is applied when using the default, including when explicitly selected.
