-- Tracks the days the morning briefing was sent, so a missed briefing can be
-- caught up on startup (idempotently) without double-sending.
CREATE TABLE IF NOT EXISTS briefing_log (
    briefing_date TEXT PRIMARY KEY,
    sent_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
