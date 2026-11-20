# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. Keep dispatch and the claim harness separate. The model-free LangGraph dispatcher calls one harness function for a claim and its run. Each claim has its own durable thread.
8. Build dispatch summaries and evaluation scores from backend records. Use stub models in tests and keep handler references out of claim operations.
