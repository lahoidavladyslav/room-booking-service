# Room booking service

## Stack
Python 3.13, FastAPI (sync endpoints), PostgreSQL 16, psycopg 3 + psycopg_pool (raw SQL, no ORM),
pytest, ruff.

## Layout
- `main.py` — app: error handling, business rules (pure functions), routes, migration runner, seed.
- `migrations/NNNN_name.sql` — schema, applied in order by `migrate()`; tracked in `schema_migrations`.
- `test_main.py` — unit tests (pure functions) + integration tests on a real Postgres.
- `frontend/` — React + Vite UI (`npm run dev`, proxies `/api` to the backend; Kyiv time helpers in `src/time.js`).
- `docs/PLAN.md` — implementation plan. `AI_LOG.md` — log of working with AI.

## Commands
- Run: `docker compose up` or `uvicorn main:app --reload`
- Migrate / seed: `python main.py migrate`, `python main.py seed`
- Tests: `pytest -q` (needs `TEST_DATABASE_URL`, default `postgresql://localhost/booking_test`)
- Lint: `ruff check . && ruff format .`

## Rules
- NEVER edit an already-applied migration; add a new numbered file.
- Times are stored as timestamptz (UTC). Business rules use `Europe/Kyiv` via `zoneinfo` —
  never hardcode +02:00/+03:00, never use server-local time, never decide weekday/date in UTC.
- Overlap protection is the DB EXCLUDE constraint; never replace it with SELECT-then-INSERT
  or an in-memory lock (must survive multiple instances).
- Intervals are half-open `[start, end)`: back-to-back bookings are allowed.
- All errors go through `ApiError` -> `{"error": {"code", "message"}}`. Codes: 400 validation,
  401 no X-User-Id, 403 not author, 404 missing, 409 conflict, 422 idempotency key misuse.
- Keep business rules in pure functions, testable without HTTP/DB.
- Tests must assert concrete values (no `is not None` style tests). New feature => new tests.
- No secrets or `.env` in git.
