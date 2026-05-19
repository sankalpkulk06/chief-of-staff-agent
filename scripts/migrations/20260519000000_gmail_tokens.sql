-- Gmail OAuth tokens stored per user in Supabase.
-- Replaces the old file-based token storage (data/credentials/{user_id}/personal_token.json).

CREATE TABLE IF NOT EXISTS user_email_tokens (
    user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    account_type TEXT NOT NULL DEFAULT 'personal',
    token_json   JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, account_type)
);

-- Temporary state store for the OAuth CSRF token.
-- Rows expire after 10 minutes — cleaned up lazily on insert.
CREATE TABLE IF NOT EXISTS oauth_states (
    state      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '10 minutes')
);
