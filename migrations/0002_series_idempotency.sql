ALTER TABLE bookings ADD COLUMN series_id TEXT;
CREATE INDEX bookings_series_idx ON bookings (series_id) WHERE series_id IS NOT NULL;

CREATE TABLE idempotency_keys (
    user_id       TEXT NOT NULL,
    key           TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    status_code   INTEGER,
    response      JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);
