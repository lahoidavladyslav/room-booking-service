"""Tests. Need a real Postgres: TEST_DATABASE_URL (default: local db `booking_test`)."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest

os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", "postgresql://localhost/booking_test")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from main import KYIV, ApiError, free_intervals, validate_slot  # noqa: E402

H = {"X-User-Id": "alice"}
NOW = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)  # Monday


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def code_of(start: str, end: str, now: datetime = NOW) -> str | None:
    try:
        validate_slot(dt(start), dt(end), now)
    except ApiError as e:
        return e.code
    return None


# ----------------------------------------------------------------- unit: validate_slot


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-11-20T10:00:00+02:00", "2026-11-20T11:00:00+02:00", None),
        ("2026-11-20T10:00:00+02:00", "2026-11-20T10:00:00+02:00", "INVALID_RANGE"),
        ("2026-11-20T11:00:00+02:00", "2026-11-20T10:00:00+02:00", "INVALID_RANGE"),
        ("2026-11-16T07:00:00+02:00", "2026-11-16T08:00:00+02:00", "IN_THE_PAST"),
        ("2026-11-20T10:00:00+02:00", "2026-11-20T10:10:00+02:00", "INVALID_DURATION"),
        ("2026-11-20T10:00:00+02:00", "2026-11-20T10:15:00+02:00", None),  # min duration ok
        ("2026-11-20T10:00:00+02:00", "2026-11-20T14:00:00+02:00", None),  # max duration ok
        ("2026-11-20T10:00:00+02:00", "2026-11-20T14:15:00+02:00", "INVALID_DURATION"),
        ("2026-11-20T10:05:00+02:00", "2026-11-20T11:00:00+02:00", "NOT_ALIGNED"),
        ("2026-11-20T10:00:30+02:00", "2026-11-20T11:00:00+02:00", "NOT_ALIGNED"),
        ("2026-11-20T07:45:00+02:00", "2026-11-20T08:45:00+02:00", "OUTSIDE_WORKING_HOURS"),
        ("2026-11-20T08:00:00+02:00", "2026-11-20T09:00:00+02:00", None),  # opening edge
        ("2026-11-20T19:00:00+02:00", "2026-11-20T20:00:00+02:00", None),  # closing edge
        ("2026-11-20T19:15:00+02:00", "2026-11-20T20:15:00+02:00", "OUTSIDE_WORKING_HOURS"),
        ("2026-11-21T10:00:00+02:00", "2026-11-21T11:00:00+02:00", "OUTSIDE_WORKING_HOURS"),  # Sat
        ("2026-11-22T10:00:00+02:00", "2026-11-22T11:00:00+02:00", "OUTSIDE_WORKING_HOURS"),  # Sun
    ],
)
def test_validate_slot(start, end, expected):
    assert code_of(start, end) == expected


def test_timezone_is_kyiv_not_utc():
    # 08:00 Kyiv (winter, +02:00) == 06:00 UTC -> valid, even though 06:00 "looks" too early.
    assert code_of("2026-11-20T06:00:00Z", "2026-11-20T07:00:00Z") is None
    # 08:00 UTC == 10:00 Kyiv, valid; 18:30 UTC == 20:30 Kyiv, invalid
    assert code_of("2026-11-20T18:00:00Z", "2026-11-20T19:00:00Z") == "OUTSIDE_WORKING_HOURS"


def test_dst_summer_offset():
    now = datetime(2026, 7, 1, tzinfo=UTC)
    # July: Kyiv is +03:00, so 08:00 Kyiv == 05:00 UTC
    assert code_of("2026-07-08T05:00:00Z", "2026-07-08T06:00:00Z", now) is None
    assert code_of("2026-07-08T04:00:00Z", "2026-07-08T05:00:00Z", now) == "OUTSIDE_WORKING_HOURS"


def test_weekend_determined_by_kyiv_date_not_utc():
    # Mon 2026-11-23 01:00 Kyiv is still Sunday in UTC; must be rejected as too early anyway,
    # but the weekday check must use Kyiv date: Sat 2026-11-21 23:00 UTC == Sun 01:00 Kyiv.
    assert code_of("2026-11-22T21:00:00Z", "2026-11-22T22:00:00Z") == "OUTSIDE_WORKING_HOURS"
    # Fri 2026-11-20 22:30 UTC == Sat 00:30 Kyiv -> weekend
    assert code_of("2026-11-20T22:30:00Z", "2026-11-20T23:00:00Z") == "OUTSIDE_WORKING_HOURS"


def test_non_hour_offset_aligned_in_kyiv():
    # +05:30 offset: 13:30+05:30 == 08:00 UTC == 10:00 Kyiv -> aligned and in hours
    assert code_of("2026-11-20T13:30:00+05:30", "2026-11-20T14:30:00+05:30") is None


# ----------------------------------------------------------------- unit: free_intervals


def b(day: str, a: str, z: str):
    return (
        datetime.fromisoformat(f"{day}T{a}:00").replace(tzinfo=KYIV),
        datetime.fromisoformat(f"{day}T{z}:00").replace(tzinfo=KYIV),
    )


def test_free_intervals_glue_and_gaps():
    day = date(2026, 11, 20)
    got = free_intervals(
        [b("2026-11-20", "10:00", "11:00"), b("2026-11-20", "11:00", "11:30")], day
    )
    assert got == [{"from": "08:00", "to": "10:00"}, {"from": "11:30", "to": "20:00"}]


def test_free_intervals_empty_day_weekend_and_full_day():
    assert free_intervals([], date(2026, 11, 20)) == [{"from": "08:00", "to": "20:00"}]
    assert free_intervals([], date(2026, 11, 21)) == []
    assert (
        free_intervals(
            [b("2026-11-20", "08:00", "12:00"), b("2026-11-20", "12:00", "20:00")],
            date(2026, 11, 20),
        )
        == []
    )


# ----------------------------------------------------------------- integration (real DB)


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_db(client):
    with main.get_pool().connection() as conn:
        conn.execute("TRUNCATE bookings, rooms, idempotency_keys RESTART IDENTITY CASCADE")
    main.utcnow = lambda: datetime.now(UTC)  # reset any freezing


def next_weekday(offset_days: int = 7) -> date:
    d = (datetime.now(KYIV) + timedelta(days=offset_days)).date()
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def slot(h1: int, h2: int, day: date | None = None, m1: int = 0, m2: int = 0) -> dict:
    day = day or next_weekday()

    def f(h, m):
        return datetime(day.year, day.month, day.day, h, m, tzinfo=KYIV).isoformat()

    return {"startAt": f(h1, m1), "endAt": f(h2, m2)}


def mkroom(client, name="Alpha") -> int:
    r = client.post("/rooms", json={"name": name, "capacity": 4}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def book(client, room, h1, h2, user="alice", **kw):
    return client.post(
        "/bookings",
        json={"roomId": room, "title": "t", **slot(h1, h2, **kw)},
        headers={"X-User-Id": user},
    )


def test_auth_required(client):
    assert client.get("/rooms").status_code == 401
    assert client.get("/rooms", headers={"X-User-Id": "  "}).status_code == 401
    assert client.get("/rooms").json()["error"]["code"] == "UNAUTHORIZED"


def test_rooms_crud_and_validation(client):
    rid = mkroom(client)
    assert client.get(f"/rooms/{rid}", headers=H).json()["name"] == "Alpha"
    assert len(client.get("/rooms", headers=H).json()) == 1
    dup = client.post("/rooms", json={"name": "Alpha", "capacity": 2}, headers=H)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "ROOM_NAME_TAKEN"
    assert client.post("/rooms", json={"name": "X", "capacity": 0}, headers=H).status_code == 400
    assert client.post("/rooms", json={"capacity": 3}, headers=H).status_code == 400
    assert client.get("/rooms/999", headers=H).status_code == 404


def test_booking_requires_offset_and_valid_room(client):
    rid = mkroom(client)
    body = {
        "roomId": rid,
        "title": "t",
        "startAt": "2026-11-20T10:00:00",
        "endAt": "2026-11-20T11:00:00",
    }
    r = client.post("/bookings", json=body, headers=H)
    assert r.status_code == 400 and r.json()["error"]["code"] == "MISSING_TIMEZONE"
    assert book(client, 999, 10, 11).status_code == 404


def test_booking_created_and_stored_utc(client):
    rid = mkroom(client)
    r = book(client, rid, 10, 11)
    assert r.status_code == 201
    assert r.json()["startAt"].endswith("Z") and r.json()["status"] == "active"


def test_overlap_rules(client):
    rid = mkroom(client)
    assert book(client, rid, 10, 12).status_code == 201
    assert book(client, rid, 12, 13).status_code == 201  # back-to-back after
    assert book(client, rid, 9, 10).status_code == 201  # back-to-back before
    for h1, h2 in [(10, 12), (9, 11), (11, 13), (10, 11), (11, 12)]:  # equal/partial/inside
        assert book(client, rid, h1, h2).status_code == 409, (h1, h2)
    assert book(client, rid, 9, 13).status_code == 409  # fully encloses existing ones
    other = mkroom(client, "Bravo")
    assert book(client, other, 10, 12).status_code == 201  # other room is independent


def test_cancelled_booking_frees_slot(client):
    rid = mkroom(client)
    bid = book(client, rid, 10, 11).json()["id"]
    assert client.delete(f"/bookings/{bid}", headers=H).json()["status"] == "cancelled"
    assert book(client, rid, 10, 11, user="bob").status_code == 201


def test_cancel_permissions_and_started(client):
    rid = mkroom(client)
    bid = book(client, rid, 10, 11).json()["id"]
    assert client.delete(f"/bookings/{bid}", headers={"X-User-Id": "bob"}).status_code == 403
    assert client.delete("/bookings/9999", headers=H).status_code == 404

    # Freeze time to 10:30 Kyiv: the booking is in progress (started, not yet ended).
    day = next_weekday()
    main.utcnow = lambda: datetime(day.year, day.month, day.day, 10, 30, tzinfo=KYIV)
    r = client.delete(f"/bookings/{bid}", headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "BOOKING_STARTED"


def test_list_filters_total_pagination(client):
    r1, r2 = mkroom(client), mkroom(client, "Bravo")
    for h in (9, 10, 11):
        book(client, r1, h, h + 1)
    book(client, r2, 9, 10, user="bob")
    book(client, r1, 14, 15, user="bob")

    def get(**q):
        return client.get("/bookings", params=q, headers=H).json()

    assert get()["total"] == 5
    assert get(roomId=r1)["total"] == 4
    assert get(mine="true")["total"] == 3
    assert get(roomId=r1, mine="true")["total"] == 3
    page = get(roomId=r1, limit=2, page=2)
    assert page["total"] == 4 and len(page["items"]) == 2  # total ignores pagination, not filters
    starts = [i["startAt"] for i in get()["items"]]
    assert starts == sorted(starts)
    day = next_weekday()
    frm = datetime(day.year, day.month, day.day, 10, 0, tzinfo=KYIV).isoformat()
    to = datetime(day.year, day.month, day.day, 11, 0, tzinfo=KYIV).isoformat()
    got = client.get("/bookings", params={"roomId": r1, "from": frm, "to": to}, headers=H).json()
    assert got["total"] == 1  # only 10-11; 9-10 and 11-12 merely touch the window
    assert client.get("/bookings", params={"limit": 0}, headers=H).status_code == 400


def test_availability(client):
    rid = mkroom(client)
    day = next_weekday()
    book(client, rid, 10, 11)
    book(client, rid, 11, 12)
    cancelled = book(client, rid, 14, 15).json()["id"]
    client.delete(f"/bookings/{cancelled}", headers=H)
    r = client.get(f"/rooms/{rid}/availability", params={"date": day.isoformat()}, headers=H)
    assert r.json() == [{"from": "08:00", "to": "10:00"}, {"from": "12:00", "to": "20:00"}]
    saturday = day + timedelta(days=(5 - day.weekday()) % 7)
    r = client.get(f"/rooms/{rid}/availability", params={"date": saturday.isoformat()}, headers=H)
    assert r.json() == []
    assert (
        client.get(f"/rooms/{rid}/availability", params={"date": "nope"}, headers=H).status_code
        == 400
    )
    assert (
        client.get(
            "/rooms/999/availability", params={"date": day.isoformat()}, headers=H
        ).status_code
        == 404
    )


def test_concurrent_bookings_exactly_one_wins(client):
    rid = mkroom(client)
    n = 16

    def attempt(i: int) -> int:
        return book(client, rid, 10, 11, user=f"user{i}").status_code

    with ThreadPoolExecutor(max_workers=n) as ex:
        codes = list(ex.map(attempt, range(n)))
    assert codes.count(201) == 1, codes
    assert codes.count(409) == n - 1, codes
    with main.get_pool().connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM bookings").fetchone()["n"] == 1


# ----------------------------------------------------------------- migrations


def test_migrations_applied_once(client):
    main.migrate()  # second run must be a no-op
    with main.get_pool().connection() as conn:
        names = [
            r["name"] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name")
        ]
    assert names == sorted(p.name for p in main.MIGRATIONS_DIR.glob("*.sql"))


# ----------------------------------------------------------------- A: repeating bookings


def repeat_body(rid, count, h1=10, h2=11, day=None):
    return {
        "roomId": rid,
        "title": "weekly",
        "repeat": {"weekly": True, "count": count},
        **slot(h1, h2, day=day),
    }


def test_repeat_creates_series_same_kyiv_time(client):
    rid = mkroom(client)
    r = client.post("/bookings", json=repeat_body(rid, 4), headers=H)
    assert r.status_code == 201
    items = r.json()["bookings"]
    assert len(items) == 4 and len({i["seriesId"] for i in items}) == 1
    for i in items:  # always 10:00 Kyiv, even across DST
        assert datetime.fromisoformat(i["startAt"]).astimezone(KYIV).hour == 10


def test_repeat_is_all_or_nothing_and_lists_conflicts(client):
    rid = mkroom(client)
    day = next_weekday()
    assert book(client, rid, 10, 11, day=day + timedelta(weeks=1)).status_code == 201
    assert book(client, rid, 10, 11, day=day + timedelta(weeks=3)).status_code == 201
    r = client.post("/bookings", json=repeat_body(rid, 5, day=day), headers=H)
    assert r.status_code == 409
    assert r.json()["error"]["details"]["conflicts"] == [
        (day + timedelta(weeks=1)).isoformat(),
        (day + timedelta(weeks=3)).isoformat(),
    ]
    with main.get_pool().connection() as conn:  # only the two pre-existing rows remain
        assert conn.execute("SELECT count(*) AS n FROM bookings").fetchone()["n"] == 2


@pytest.mark.parametrize("count", [0, 11])
def test_repeat_count_bounds(client, count):
    rid = mkroom(client)
    assert client.post("/bookings", json=repeat_body(rid, count), headers=H).status_code == 400


# ----------------------------------------------------------------- C: idempotency


def test_idempotency_key_replays_same_result(client):
    rid = mkroom(client)
    body = {"roomId": rid, "title": "t", **slot(10, 11)}
    h = {**H, "Idempotency-Key": "k1"}
    first = client.post("/bookings", json=body, headers=h)
    second = client.post("/bookings", json=body, headers=h)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    with main.get_pool().connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM bookings").fetchone()["n"] == 1


def test_idempotency_key_different_body_rejected(client):
    rid = mkroom(client)
    h = {**H, "Idempotency-Key": "k2"}
    assert (
        client.post(
            "/bookings", json={"roomId": rid, "title": "t", **slot(10, 11)}, headers=h
        ).status_code
        == 201
    )
    r = client.post("/bookings", json={"roomId": rid, "title": "t", **slot(12, 13)}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_idempotency_key_is_scoped_per_user(client):
    rid = mkroom(client)
    a = client.post(
        "/bookings",
        json={"roomId": rid, "title": "t", **slot(10, 11)},
        headers={"X-User-Id": "a", "Idempotency-Key": "same"},
    )
    b = client.post(
        "/bookings",
        json={"roomId": rid, "title": "t", **slot(10, 11)},
        headers={"X-User-Id": "b", "Idempotency-Key": "same"},
    )
    assert (a.status_code, b.status_code) == (201, 409)


def test_idempotency_concurrent_same_key_creates_one(client):
    rid = mkroom(client)
    body = {"roomId": rid, "title": "t", **slot(10, 11)}

    def go(_):
        return client.post("/bookings", json=body, headers={**H, "Idempotency-Key": "race"})

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(go, range(8)))
    assert {r.status_code for r in res} == {201}
    assert len({r.json()["id"] for r in res}) == 1


# ----------------------------------------------------------------- B: stats


def test_stats(client):
    rid = mkroom(client)
    day = next_weekday()
    while day.weekday() != 0:  # use a Monday so the week is Mon..Fri
        day += timedelta(days=1)
    book(client, rid, 8, 12, day=day, user="alice")  # 4h
    book(client, rid, 12, 14, day=day, user="bob")  # 2h
    book(client, rid, 9, 10, day=day + timedelta(days=1), user="alice")  # 1h
    book(client, rid, 10, 11, day=day + timedelta(days=1), user="carol")  # 1h
    cancelled = book(client, rid, 14, 18, day=day + timedelta(days=2), user="dave").json()["id"]
    client.delete(f"/bookings/{cancelled}", headers={"X-User-Id": "dave"})
    sunday = day + timedelta(days=6)
    r = client.get(
        f"/rooms/{rid}/stats", params={"from": day.isoformat(), "to": sunday.isoformat()}, headers=H
    )
    d = r.json()
    assert d["workingHours"] == 60  # 5 weekdays * 12h, weekend excluded
    assert d["bookedHours"] == 8
    assert d["utilizationPercent"] == round(800 / 60, 2)
    assert d["topUsers"] == [
        {"userId": "alice", "hours": 5.0},
        {"userId": "bob", "hours": 2.0},
        {"userId": "carol", "hours": 1.0},
    ]
    bad = client.get(
        f"/rooms/{rid}/stats", params={"from": sunday.isoformat(), "to": day.isoformat()}, headers=H
    )
    assert bad.status_code == 400
