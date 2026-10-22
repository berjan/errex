-- Alerts commit atomically with their events, survive restarts and retry.
CREATE TABLE webhook_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX webhook_outbox_due ON webhook_outbox(next_attempt);
