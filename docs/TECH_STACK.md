# Tech stack

Python 3.12 with uv and Node 26 run three local processes. The React, Vite and
strict TypeScript dashboard talks to FastAPI. Every API operation has a unique
operation ID, a one-line docstring and required inputs. The backend owns claim
records, section lookup, payout calculations, sign-offs and simulated payments.
Payment takes the full approved amount and checks the persisted sign-off. The
dashboard can fetch the claim's settlement status without reloading the evidence.
Guidance lookup searches numbered Markdown sections. Decisions store the cited
section numbers and the versions of the wording and manual.

SQLAlchemy Core shares the business queries between SQLite and PostgreSQL.
With DATABASE_URL configured, the backend uses the Supabase session pooler and
the undercover schema. Without it, UNDERCOVER_DB selects a local SQLite file.
The agent accesses business records and instructions only through backend HTTP.
The synchronous LangGraph checkpointer uses the same PostgreSQL schema or a
separate SQLite file alongside the local business database. The backend and
agent processes each pool at most four PostgreSQL connections. An invocation
uses its own saver and checks out a connection for each checkpoint operation.

The model-free dispatcher uses LangGraph `Send` with a requested concurrency
of 1 to 25. Each worker calls one harness function to activate or continue an
independent claim thread. The dispatcher holds no checkpoints. The claim graph saves a synchronous
checkpoint after each step and pauses with interrupt for a held approval. Its
nodes own model calls, bounded retries, evidence lookup, decision recording and
payment. Each active invocation has a 180-second local deadline or a 600-second
deadline with hosted checkpoints. UNDERCOVER_CLAIM_SECONDS overrides the deadline.
An interrupt releases the worker while the claim waits for a handler.
A claim attempt has a stable thread ID and Langfuse session across
processes. OpenRouter usage and active work belong to the executing run when
it has one. Run-owned Langfuse traces carry that run ID as a tag. The agent
service admits dispatches and evaluations, and accepts wake and resume requests at UNDERCOVER_AGENT
(default http://127.0.0.1:8002). BACKEND_URL selects the claims API.
When the service has already observed an interrupt, it checks the saved
sign-off and matching thread before issuing the resume command. After a restart
it discovers the thread and reads its persisted checkpoint.

The initial claim configuration uses `openai/gpt-6-luna` on OpenRouter with low reasoning.
`MODEL_NAME` selects an override with no reasoning effort set. The development
launcher supplies `OPENROUTER_API_KEY` and optional Langfuse credentials.
The backend seeds held-out sources and handler references into dedicated
tables. Its Scoreboard read path joins the references to claim copies and scores
them. The agent reads copy evidence through the same claim tools. Automated
tests use temporary databases, scripted models and local HTTP stand-ins.

## Sign-in, explanations and improvements

The dashboard authenticates with Supabase Auth using the project's URL and publishable key. FastAPI verifies asymmetric bearer tokens against the project's JWKS, requires audience `authenticated` and looks up the handler by email. Senior permissions come from the business record. Normal agent-service writes use the internal service routes.

The backend keeps immutable configurations, recipes and review history alongside business records. The service pins the model, effort and instructions for each run. Saved decisions carry their approval limit. The dashboard stores only the appearance preference locally as a platform setting.

The backend calls Jev through the OpenRouter Decisions SDK at evaluation close. It reserves copies before calling the judge in parallel, with a ten-second timeout and retries disabled. It persists scores, grade letters, match probabilities and reported judge cost. `MODEL_NAME` selects a single allowed model with default effort and disables judging.

The improver uses LangChain create_agent and HumanInTheLoopMiddleware. Each round has a unique thread, a synchronous PostgresSaver on the shared session pool (SQLite locally) and one Langfuse session. A review resumes a checkpoint. Active evaluation workers check round admission before starting claims and model calls. Traces retain the claim session and carry the run and round tags. The backend applies the business thresholds to complete whole-set round trials. The Scoreboard also accepts manual evaluations on isolated copies.
