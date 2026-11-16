CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS rooms (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    capacity    INTEGER NOT NULL CHECK (capacity > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bookings (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id     BIGINT NOT NULL REFERENCES rooms(id),
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    start_at    TIMESTAMPTZ NOT NULL,
    end_at      TIMESTAMPTZ NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (start_at < end_at),
    CONSTRAINT bookings_no_overlap EXCLUDE USING gist (
        room_id WITH =,
        tstzrange(start_at, end_at) WITH &&
    ) WHERE (status = 'active')
);

CREATE INDEX IF NOT EXISTS bookings_room_start_idx ON bookings (room_id, start_at);
CREATE INDEX IF NOT EXISTS bookings_user_idx ON bookings (user_id);
