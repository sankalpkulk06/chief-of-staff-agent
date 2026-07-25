-- Feature/agent usage analytics: one row per agent that handled a turn.
CREATE TABLE IF NOT EXISTS agent_invocations (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default' REFERENCES users(user_id) ON DELETE CASCADE,
    session_id TEXT,
    agent      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_inv_user_time ON agent_invocations (user_id, created_at);
