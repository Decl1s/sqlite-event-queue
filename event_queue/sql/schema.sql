-- Очередь и результат находятся в одном файле: эффект и ACK можно принять атомарно.
BEGIN IMMEDIATE;
PRAGMA application_id = 1163284785;
PRAGMA user_version = 1;
CREATE TABLE messages (
    event_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('ready','leased','done','dead')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    available_at INTEGER NOT NULL,
    lease_until INTEGER,
    last_error TEXT,
    CHECK ((state = 'leased') = (lease_until IS NOT NULL))
) STRICT;
CREATE INDEX eligible ON messages(state, available_at, event_id);
CREATE TABLE effects (
    event_id TEXT PRIMARY KEY REFERENCES messages(event_id),
    category TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents BETWEEN 1 AND 9999999)
) STRICT;
COMMIT;
