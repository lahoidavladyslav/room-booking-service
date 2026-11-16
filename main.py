"""Room booking service (FastAPI + PostgreSQL).

Run:     uvicorn main:app --reload
Migrate: python main.py migrate   (also runs automatically on startup)
Seed:    python main.py seed
Env:     DATABASE_URL (default postgresql://postgres:postgres@localhost:5432/booking)

Design notes
- All timestamps are stored as timestamptz (UTC); business rules are checked in Europe/Kyiv.
- Overlap protection lives in the DB: an EXCLUDE constraint on (room_id, tstzrange) for active
  bookings. It works across any number of app instances, unlike an in-memory lock or
  "SELECT then INSERT".
- tstzrange is half-open [start, end), so back-to-back bookings (10-11, 11-12) are allowed.
"""

from __future__ import annotations

import hashlib
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import errors as pgerr
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/booking")

KYIV = ZoneInfo("Europe/Kyiv")
WORK_START = time(8, 0)
WORK_END = time(20, 0)
MIN_DURATION = timedelta(minutes=15)
MAX_DURATION = timedelta(hours=4)
STEP_MINUTES = 15

# --------------------------------------------------------------------------- schema

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

pool: ConnectionPool | None = None


def migrate() -> None:
    """Apply migrations/*.sql in name order, once each. Applied files must never be edited:
    add a new numbered file instead."""
    with get_pool().connection() as conn:
        # Advisory lock: several instances may start at once.
        conn.execute("SELECT pg_advisory_xact_lock(727274)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name not in done:
                conn.execute(f.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))


def get_pool() -> ConnectionPool:
    global pool
    if pool is None:
        pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=20, open=False, kwargs={"row_factory": dict_row}
        )
        pool.open(wait=True, timeout=30)
    return pool


@asynccontextmanager
async def lifespan(_: FastAPI):
    migrate()
    yield
    global pool
    if pool is not None:
        pool.close()
        pool = None


app = FastAPI(title="Room booking", lifespan=lifespan)

# --------------------------------------------------------------------------- errors


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        self.status, self.code, self.message, self.details = status, code, message, details


def error_body(code: str, message: str, details: Any = None) -> dict:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError):
    return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    details = [
        {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()
    ]
    return JSONResponse(error_body("VALIDATION_ERROR", "Invalid request", details), status_code=400)


@app.exception_handler(HTTPException)
async def _http_error(_: Request, exc: HTTPException):
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
    return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code)


def current_user(x_user_id: str | None = Header(default=None)) -> str:
    if x_user_id is None or not x_user_id.strip():
        raise ApiError(401, "UNAUTHORIZED", "X-User-Id header is required")
    return x_user_id.strip()


# --------------------------------------------------------------------------- business rules


def utcnow() -> datetime:
    # Single seam for "now" so tests can freeze time.
    return datetime.now(UTC)


def parse_dt(value: str, field: str) -> datetime:
    """Parse ISO 8601 and require an explicit UTC offset."""
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise ApiError(400, "INVALID_DATETIME", f"{field} is not a valid ISO 8601 datetime")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ApiError(400, "MISSING_TIMEZONE", f"{field} must include a UTC offset, e.g. +02:00")
    return dt


def validate_slot(start: datetime, end: datetime, now: datetime) -> None:
    """Pure business-rule check (no HTTP, no DB). Raises ApiError(400, ...)."""
    if not start < end:
        raise ApiError(400, "INVALID_RANGE", "startAt must be before endAt")
    if start < now:
        raise ApiError(400, "IN_THE_PAST", "Booking cannot start in the past")

    duration = end - start
    if duration < MIN_DURATION or duration > MAX_DURATION:
        raise ApiError(400, "INVALID_DURATION", "Duration must be between 15 minutes and 4 hours")

    ks, ke = start.astimezone(KYIV), end.astimezone(KYIV)
    for dt in (ks, ke):
        if dt.minute % STEP_MINUTES or dt.second or dt.microsecond:
            raise ApiError(400, "NOT_ALIGNED", "Times must be multiples of 15 minutes")

    if ks.weekday() >= 5:
        raise ApiError(400, "OUTSIDE_WORKING_HOURS", "Bookings are only allowed Monday to Friday")
    if ks.date() != ke.date() or ks.time() < WORK_START or ke.time() > WORK_END:
        raise ApiError(
            400, "OUTSIDE_WORKING_HOURS", "Booking must fit within 08:00-20:00 Europe/Kyiv"
        )


def booking_out(row: dict) -> dict:
    def iso(dt: datetime) -> str:
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")

    return {
        "id": row["id"],
        "roomId": row["room_id"],
        "userId": row["user_id"],
        "title": row["title"],
        "startAt": iso(row["start_at"]),
        "endAt": iso(row["end_at"]),
        "status": row["status"],
        "createdAt": iso(row["created_at"]),
        "seriesId": row.get("series_id"),
    }


def room_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "capacity": row["capacity"],
        "createdAt": row["created_at"].astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }


# --------------------------------------------------------------------------- rooms


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    capacity: int = Field(gt=0)


@app.post("/rooms", status_code=201)
def create_room(body: RoomIn, _: str = Depends(current_user)):
    name = body.name.strip()
    if not name:
        raise ApiError(400, "VALIDATION_ERROR", "name must not be blank")
    try:
        with get_pool().connection() as conn:
            row = conn.execute(
                "INSERT INTO rooms (name, capacity) VALUES (%s, %s) RETURNING *",
                (name, body.capacity),
            ).fetchone()
    except pgerr.UniqueViolation:
        raise ApiError(409, "ROOM_NAME_TAKEN", f"Room '{name}' already exists")
    return room_out(row)


@app.get("/rooms")
def list_rooms(_: str = Depends(current_user)):
    with get_pool().connection() as conn:
        rows = conn.execute("SELECT * FROM rooms ORDER BY id").fetchall()
    return [room_out(r) for r in rows]


def fetch_room(conn: psycopg.Connection, room_id: int) -> dict:
    row = conn.execute("SELECT * FROM rooms WHERE id = %s", (room_id,)).fetchone()
    if row is None:
        raise ApiError(404, "ROOM_NOT_FOUND", f"Room {room_id} not found")
    return row


@app.get("/rooms/{room_id}")
def get_room(room_id: int, _: str = Depends(current_user)):
    with get_pool().connection() as conn:
        return room_out(fetch_room(conn, room_id))


# --------------------------------------------------------------------------- bookings


class Repeat(BaseModel):
    weekly: bool
    count: int = Field(ge=1, le=10)


class BookingIn(BaseModel):
    roomId: int
    title: str = Field(min_length=1, max_length=200)
    startAt: str
    endAt: str
    repeat: Repeat | None = None


def occurrences(
    start: datetime, end: datetime, repeat: Repeat | None
) -> list[tuple[datetime, datetime]]:
    """Weekly repeats keep the same Kyiv wall-clock time across DST changes."""
    if repeat is None or not repeat.weekly:
        return [(start, end)]
    ks, ke = start.astimezone(KYIV), end.astimezone(KYIV)
    # aware + timedelta in zoneinfo is wall-clock arithmetic
    return [(ks + timedelta(weeks=i), ke + timedelta(weeks=i)) for i in range(repeat.count)]


@app.post("/bookings", status_code=201)
def create_booking(
    body: BookingIn,
    user: str = Depends(current_user),
    idempotency_key: str | None = Header(default=None),
):
    start = parse_dt(body.startAt, "startAt")
    end = parse_dt(body.endAt, "endAt")
    title = body.title.strip()
    if not title:
        raise ApiError(400, "VALIDATION_ERROR", "title must not be blank")
    slots = occurrences(start, end, body.repeat)
    now = utcnow()
    for s_, e_ in slots:
        validate_slot(s_, e_, now)
    repeated = len(slots) > 1
    series_id = str(uuid.uuid4()) if repeated else None
    key = idempotency_key.strip() if idempotency_key and idempotency_key.strip() else None
    req_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()

    try:
        with get_pool().connection() as conn:  # one transaction: all or nothing
            if key:
                claimed = conn.execute(
                    """INSERT INTO idempotency_keys (user_id, key, request_hash)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1""",
                    (user, key, req_hash),
                ).fetchone()
                if claimed is None:  # concurrent first request is awaited by the unique index
                    saved = conn.execute(
                        "SELECT * FROM idempotency_keys WHERE user_id = %s AND key = %s",
                        (user, key),
                    ).fetchone()
                    if saved["request_hash"] != req_hash:
                        raise ApiError(
                            422, "IDEMPOTENCY_KEY_REUSED", "Key was used with a different request"
                        )
                    return JSONResponse(saved["response"], status_code=saved["status_code"])

            fetch_room(conn, body.roomId)
            rows, conflicts = [], []
            for s_, e_ in slots:
                try:
                    # Savepoint per slot so we can collect every conflicting date. The EXCLUDE
                    # constraint is the real guard against concurrent overlapping inserts.
                    with conn.transaction():
                        rows.append(
                            conn.execute(
                                """INSERT INTO bookings
                                   (room_id, user_id, title, start_at, end_at, series_id)
                                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                                (body.roomId, user, title, s_, e_, series_id),
                            ).fetchone()
                        )
                except pgerr.ExclusionViolation:
                    conflicts.append(s_.astimezone(KYIV).date().isoformat())
            if conflicts:  # raising rolls back every slot (and the idempotency claim)
                raise ApiError(
                    409,
                    "SLOT_TAKEN",
                    "Room is already booked for this time",
                    {"conflicts": conflicts} if repeated else None,
                )
            payload = (
                {"seriesId": series_id, "bookings": [booking_out(r) for r in rows]}
                if repeated
                else booking_out(rows[0])
            )
            if key:
                conn.execute(
                    "UPDATE idempotency_keys SET status_code = 201, response = %s"
                    " WHERE user_id = %s AND key = %s",
                    (Jsonb(payload), user, key),
                )
    except pgerr.DeadlockDetected:  # two overlapping series raced; treat as a conflict
        raise ApiError(409, "SLOT_TAKEN", "Room is already booked for this time")
    except pgerr.ForeignKeyViolation:
        raise ApiError(404, "ROOM_NOT_FOUND", f"Room {body.roomId} not found")
    return payload


@app.delete("/bookings/{booking_id}")
def cancel_booking(booking_id: int, user: str = Depends(current_user)):
    with get_pool().connection() as conn:
        # Lock the row so a concurrent cancel can't interleave with our checks.
        row = conn.execute(
            "SELECT * FROM bookings WHERE id = %s FOR UPDATE", (booking_id,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "BOOKING_NOT_FOUND", f"Booking {booking_id} not found")
        if row["user_id"] != user:
            raise ApiError(403, "FORBIDDEN", "Only the author can cancel a booking")
        if row["status"] == "cancelled":
            return booking_out(row)  # idempotent
        if row["start_at"] <= utcnow():  # already started (not just "ended")
            raise ApiError(409, "BOOKING_STARTED", "Started or past bookings cannot be cancelled")
        row = conn.execute(
            "UPDATE bookings SET status = 'cancelled' WHERE id = %s RETURNING *", (booking_id,)
        ).fetchone()
    return booking_out(row)


@app.get("/bookings")
def list_bookings(
    roomId: int | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    mine: bool = False,
    status: str = Query(default="active", pattern="^(active|cancelled|all)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user: str = Depends(current_user),
):
    where, params = [], []
    if roomId is not None:
        where.append("room_id = %s")
        params.append(roomId)
    if mine:
        where.append("user_id = %s")
        params.append(user)
    if status != "all":
        where.append("status = %s")
        params.append(status)
    # `from`/`to` select bookings overlapping [from, to).
    if from_ is not None:
        where.append("end_at > %s")
        params.append(parse_dt(from_, "from"))
    if to is not None:
        where.append("start_at < %s")
        params.append(parse_dt(to, "to"))
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    with get_pool().connection() as conn:
        # total uses exactly the same filters as the page query
        total = conn.execute(f"SELECT count(*) AS n FROM bookings {clause}", params).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM bookings {clause} ORDER BY start_at, id LIMIT %s OFFSET %s",
            [*params, limit, (page - 1) * limit],
        ).fetchall()
    return {"items": [booking_out(r) for r in rows], "total": total, "page": page, "limit": limit}


# --------------------------------------------------------------------------- availability


def free_intervals(bookings: list[tuple[datetime, datetime]], day: date) -> list[dict[str, str]]:
    """Free gaps inside Kyiv working hours of `day`. Pure function, easy to unit-test."""
    if day.weekday() >= 5:
        return []
    w_start = datetime.combine(day, WORK_START, tzinfo=KYIV)
    w_end = datetime.combine(day, WORK_END, tzinfo=KYIV)

    gaps: list[tuple[datetime, datetime]] = []
    cursor = w_start
    for b_start, b_end in sorted(bookings):
        if b_start > cursor:
            gaps.append((cursor, min(b_start, w_end)))
        cursor = max(cursor, b_end)
        if cursor >= w_end:
            break
    if cursor < w_end:
        gaps.append((cursor, w_end))

    # Adjacent bookings leave no zero-length gap, and gaps are separated by bookings,
    # so the result is already "glued".
    return [
        {"from": a.astimezone(KYIV).strftime("%H:%M"), "to": b.astimezone(KYIV).strftime("%H:%M")}
        for a, b in gaps
        if a < b
    ]


@app.get("/rooms/{room_id}/availability")
def availability(room_id: int, date_: str = Query(alias="date"), _: str = Depends(current_user)):
    try:
        day = date.fromisoformat(date_)
    except ValueError:
        raise ApiError(400, "INVALID_DATE", "date must be YYYY-MM-DD")

    w_start = datetime.combine(day, WORK_START, tzinfo=KYIV)
    w_end = datetime.combine(day, WORK_END, tzinfo=KYIV)
    with get_pool().connection() as conn:
        fetch_room(conn, room_id)
        rows = conn.execute(
            """SELECT start_at, end_at FROM bookings
               WHERE room_id = %s AND status = 'active' AND start_at < %s AND end_at > %s
               ORDER BY start_at""",
            (room_id, w_end, w_start),
        ).fetchall()
    return free_intervals([(r["start_at"], r["end_at"]) for r in rows], day)


# --------------------------------------------------------------------------- stats

# Working windows (Mon-Fri 08-20 Kyiv, DST-aware) generated in SQL; bookings are clipped to them.
STATS_CTE = """
WITH days AS (
    SELECT (d::date + time '08:00') AT TIME ZONE 'Europe/Kyiv' AS ws,
           (d::date + time '20:00') AT TIME ZONE 'Europe/Kyiv' AS we
    FROM generate_series(%(from)s::timestamp, %(to)s::timestamp, interval '1 day') AS d
    WHERE extract(isodow FROM d) <= 5
)
"""


@app.get("/rooms/{room_id}/stats")
def room_stats(
    room_id: int,
    from_: str = Query(alias="from"),
    to: str = Query(),
    _: str = Depends(current_user),
):
    try:
        d_from, d_to = date.fromisoformat(from_), date.fromisoformat(to)
    except ValueError:
        raise ApiError(400, "INVALID_DATE", "from/to must be YYYY-MM-DD")
    if d_to < d_from or (d_to - d_from).days > 366:
        raise ApiError(400, "INVALID_RANGE", "to must be >= from, span at most 366 days")
    p = {"from": d_from, "to": d_to, "room": room_id}

    with get_pool().connection() as conn:
        fetch_room(conn, room_id)
        total = conn.execute(
            STATS_CTE
            + "SELECT coalesce(sum(extract(epoch FROM we - ws)) / 3600.0, 0) AS h FROM days",
            p,
        ).fetchone()["h"]
        users = conn.execute(
            STATS_CTE
            + """SELECT b.user_id,
                        sum(extract(epoch FROM least(b.end_at, days.we) - greatest(b.start_at, days.ws)))
                          / 3600.0 AS h
                 FROM days JOIN bookings b
                   ON b.room_id = %(room)s AND b.status = 'active'
                  AND b.start_at < days.we AND b.end_at > days.ws
                 GROUP BY b.user_id ORDER BY h DESC, b.user_id LIMIT 3""",
            p,
        ).fetchall()
        booked = conn.execute(
            STATS_CTE
            + """SELECT coalesce(sum(extract(epoch FROM least(b.end_at, days.we) - greatest(b.start_at, days.ws))) / 3600.0, 0) AS h
                 FROM days JOIN bookings b
                   ON b.room_id = %(room)s AND b.status = 'active'
                  AND b.start_at < days.we AND b.end_at > days.ws""",
            p,
        ).fetchone()["h"]

    total, booked = float(total), float(booked)
    return {
        "roomId": room_id,
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "workingHours": round(total, 2),
        "bookedHours": round(booked, 2),
        "utilizationPercent": round(100 * booked / total, 2) if total else 0.0,
        "topUsers": [{"userId": u["user_id"], "hours": round(float(u["h"]), 2)} for u in users],
    }


# --------------------------------------------------------------------------- seed


def seed() -> None:
    migrate()
    rooms = [("Alpha", 4), ("Bravo", 8), ("Charlie", 12), ("Delta", 20)]
    with get_pool().connection() as conn:
        ids = {}
        for name, cap in rooms:
            row = conn.execute(
                """INSERT INTO rooms (name, capacity) VALUES (%s, %s)
                   ON CONFLICT (name) DO UPDATE SET capacity = EXCLUDED.capacity RETURNING id""",
                (name, cap),
            ).fetchone()
            ids[name] = row["id"]

        day = (utcnow().astimezone(KYIV) + timedelta(days=1)).date()
        while day.weekday() >= 5:
            day += timedelta(days=1)

        def at(h: int, m: int = 0) -> datetime:
            return datetime.combine(day, time(h, m), tzinfo=KYIV)

        demo = [
            ("Alpha", "alice", "Standup", at(9), at(9, 30)),
            ("Alpha", "bob", "Design review", at(10), at(11, 30)),
            ("Bravo", "alice", "Planning", at(14), at(16)),
            ("Charlie", "carol", "Interview", at(11), at(12)),
        ]
        for room, user, title, s, e in demo:
            conn.execute(
                """INSERT INTO bookings (room_id, user_id, title, start_at, end_at)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (ids[room], user, title, s, e),
            )
    print(f"Seeded {len(rooms)} rooms and {len(demo)} bookings for {day}")


if __name__ == "__main__":
    cmd = sys.argv[1:]
    if cmd == ["migrate"]:
        migrate()
        print("migrations applied")
    elif cmd == ["seed"]:
        seed()
    else:
        print("usage: python main.py migrate|seed   (server: uvicorn main:app)")
