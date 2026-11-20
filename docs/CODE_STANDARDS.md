# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. Keep dispatch and the claim harness separate. The model-free LangGraph dispatcher calls one harness function for a claim and its run. Each claim and each improver round has its own durable thread. The improver has a separate HTTP client for round results and approvals. Claim tools can fetch their run configuration and claim evidence only.
8. Build dispatch summaries and evaluation scores from backend records. Use stub models in tests and keep handler references out of claim operations.
9. Pin the configuration at run admission. Use recorded decision thresholds for payment.
10. Keep the version verdict and budget checks in the backend and use exact monetary comparisons at admission.
11. Store approval, rejection and setting changes with a verified handler identity.
12. Use a local judge and signed Auth fixtures in automated checks.
13. Keep Supabase sign-in in the dashboard Auth client. Use the shared API helper for business requests with the current bearer token.
14. Write the improver's instructions as goals. Keep its versioned recipe as data and enforce tool permissions in code.
