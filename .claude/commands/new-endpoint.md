---
description: Add a new API endpoint with tests following project conventions
argument-hint: <METHOD /path> <what it does>
---
Add the endpoint: $ARGUMENTS

Follow CLAUDE.md. Checklist:
1. Pure business rules go in a plain function (no HTTP/DB) next to `validate_slot` / `free_intervals`.
2. Route in main.py: `Depends(current_user)`, sync `def`, SQL via `get_pool().connection()`,
   errors only via `ApiError(status, CODE, message)`.
3. Schema change => NEW file in migrations/ (next number). Never edit applied migrations.
4. Tests in test_main.py: a unit test for the pure function and an integration test on the real DB,
   including at least one edge case and one error code.
5. Run `ruff check . && ruff format . && pytest -q` and report the result.
