-- Calorie counter: append-only meal log. "Today's total" is derived at read time
-- via DATE(eaten_at) = today. The per-user daily budget lives in user_settings
-- (key 'calorie_budget'), so no schema column is needed for it.
--
-- Local SQLite parity is handled by app/storage/sql_schema.sql +
-- SQLiteRegistry._migrate_columns(). This file is the cloud (Supabase/Postgres) copy.

CREATE TABLE IF NOT EXISTS calorie_entries (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    description TEXT NOT NULL,
    calories    INTEGER NOT NULL,
    items_json  TEXT,
    eaten_at    TIMESTAMPTZ DEFAULT NOW(),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calorie_entries_user_id ON calorie_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_calorie_entries_eaten_at ON calorie_entries(eaten_at);
