# Room booking service

FastAPI + PostgreSQL. Auth is simplified: send `X-User-Id: <any string>` (missing -> 401).

## Run
```bash
docker compose up --build        # app on :8000, Swagger on http://localhost:8000/docs
```
Without Docker: start Postgres, `pip install -r requirements.txt`, then
```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/booking
python main.py migrate && python main.py seed
uvicorn main:app --reload
```
Migrations (`migrations/*.sql`) also run automatically on app start.
In compose: `docker compose exec app python main.py seed`.

## Tests
```bash
pip install -r requirements-dev.txt
createdb booking_test            # or set TEST_DATABASE_URL
pytest -q
```
Integration tests use a real Postgres (including a 16-way concurrent booking race).
The test DB is truncated between tests — never point it at real data.

## Examples
```bash
H='-H X-User-Id:alice -H Content-Type:application/json'
curl $H -X POST localhost:8000/rooms -d '{"name":"Atlas","capacity":6}'
curl $H -X POST localhost:8000/bookings -d '{"roomId":1,"title":"Sync","startAt":"2026-11-20T10:00:00+02:00","endAt":"2026-11-20T11:00:00+02:00"}'
# weekly x4, all-or-nothing
curl $H -X POST localhost:8000/bookings -d '{"roomId":1,"title":"Weekly","startAt":"2026-11-23T10:00:00+02:00","endAt":"2026-11-23T11:00:00+02:00","repeat":{"weekly":true,"count":4}}'
# idempotent retry
curl $H -H 'Idempotency-Key: abc' -X POST localhost:8000/bookings -d '{...}'
curl $H 'localhost:8000/bookings?roomId=1&mine=true&page=1&limit=10'
curl $H localhost:8000/rooms/1/availability?date=2026-11-20
curl $H 'localhost:8000/rooms/1/stats?from=2026-11-16&to=2026-11-22'
curl $H -X DELETE localhost:8000/bookings/1
```

## Design
- Times in `timestamptz` (UTC); rules evaluated in `Europe/Kyiv` via `zoneinfo` (DST-safe).
- Concurrency: Postgres `EXCLUDE USING gist` on `(room_id, tstzrange)` for active bookings —
  works across multiple instances. Intervals are half-open, so back-to-back is allowed.
- `GET /bookings` hides cancelled by default; use `status=cancelled|all`.
- Idempotency: key is scoped per user; only successful (201) results are stored, a different body
  with the same key -> 422. Failed attempts (400/409) are not cached, so they can be retried.
- Cancelling an already cancelled booking is a no-op (200).
- Layout is intentionally a single `main.py` + `migrations/`.

## Extras done
A (weekly repeat), B (stats, computed in SQL), C (Idempotency-Key), E (Swagger `/docs`),
F (GitHub Actions CI), G (`.claude/commands/new-endpoint.md` + ruff hook in `.claude/settings.json`:
keeps style consistent automatically after each AI edit and gives the model immediate lint feedback).

## Not done
- D: React UI (time).
- Alembic: plain SQL migrations with a tiny runner instead (no ORM in the project).
