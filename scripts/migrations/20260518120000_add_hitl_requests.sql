-- HITL (Human-in-the-Loop) requests table
-- Stores pending write actions waiting for user approval before execution.
CREATE TABLE IF NOT EXISTS hitl_requests (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    session_id      TEXT,
    action_type     TEXT NOT NULL,
    action_payload  JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | expired
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '10 minutes',
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_hitl_requests_user_id ON hitl_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_hitl_requests_status  ON hitl_requests(status);
