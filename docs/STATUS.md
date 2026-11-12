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
