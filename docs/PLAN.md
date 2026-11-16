# Plan

1. **Setup** — FastAPI app, ruff, pytest, docker compose (app + Postgres), CLAUDE.md.
2. **Data model** — `rooms`, `bookings` (timestamptz, UTC), SQL migrations + runner, seed script.
   Overlap guard: `EXCLUDE USING gist (room_id WITH =, tstzrange(start_at,end_at) WITH &&) WHERE status='active'`.
3. **Rooms** — POST/GET, unique name -> 409, uniform error format.
4. **Create booking** — pure `validate_slot` (offset required, order, not past, 15m-4h, 15-min grid,
   Mon-Fri 08-20 Kyiv); insert; `ExclusionViolation` -> 409.
5. **Cancel/list** — soft delete (author only, `start_at <= now` => 409); filters + pagination,
   `total` computed with the same WHERE.
6. **Availability** — pure `free_intervals` over Kyiv working window, gaps only (auto-glued).
7. **Tests** — unit (time rules, DST, weekend-by-Kyiv-date), integration on real DB, 16-way race test.
8. **Extras** — A weekly repeat (all-or-nothing, savepoint per slot, conflict dates),
   B stats in SQL, C Idempotency-Key (claim row in same tx), E Swagger `/docs`,
   F GitHub Actions, G `/new-endpoint` command + ruff hook.
9. **Docs** — README, AI_LOG.

## Not done
- D: React UI.
