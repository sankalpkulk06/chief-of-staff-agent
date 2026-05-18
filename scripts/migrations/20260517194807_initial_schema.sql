-- Migration: initial_schema
-- Applied: 2026-05-17
-- Creates all base tables and pgvector embeddings index

CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    document_id     TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    source_path     TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    file_type       TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    parser_name     TEXT NOT NULL,
    content_length  INTEGER NOT NULL,
    metadata_json   JSONB NOT NULL DEFAULT '{}',
    source_type     TEXT NOT NULL DEFAULT 'local',
    source_url      TEXT,
    ingested_at     TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id                 TEXT PRIMARY KEY,
    document_id              TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    chunk_index              INTEGER NOT NULL,
    text                     TEXT NOT NULL,
    token_count              INTEGER NOT NULL,
    char_start               INTEGER NOT NULL,
    char_end                 INTEGER NOT NULL,
    source_path              TEXT NOT NULL,
    file_name                TEXT NOT NULL,
    document_checksum_sha256 TEXT NOT NULL,
    metadata_json            JSONB NOT NULL DEFAULT '{}',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

-- pgvector embeddings table (replaces ChromaDB)
-- 768 dims = nomic-embed-text; update if switching embedding model
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id  TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    embedding extensions.vector(768)
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    title      TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id    TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS learned_facts (
    fact_id          TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL DEFAULT 'default',
    content          TEXT NOT NULL,
    category         TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'user',
    confidence_score REAL NOT NULL DEFAULT 1.0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at     TIMESTAMPTZ,
    usage_count      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS whatsapp_sessions (
    phone_number TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    last_active  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatsapp_usage_daily (
    usage_date TEXT PRIMARY KEY,
    sent_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatsapp_usage_alerts (
    usage_date TEXT NOT NULL,
    threshold  INTEGER NOT NULL,
    sent_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (usage_date, threshold)
);

CREATE TABLE IF NOT EXISTS habits (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'default',
    name          TEXT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    reminder_time TEXT DEFAULT '21:00',
    active        INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS habit_logs (
    id        TEXT PRIMARY KEY,
    habit_id  TEXT NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    logged_at TIMESTAMPTZ DEFAULT NOW(),
    status    TEXT DEFAULT 'done',
    note      TEXT
);

CREATE TABLE IF NOT EXISTS todos (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'default',
    title        TEXT NOT NULL,
    list_name    TEXT,
    due_at       TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    notified_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nudge_context (
    phone_number TEXT PRIMARY KEY,
    habit_id     TEXT NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    sent_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS named_sessions (
    name       TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    session_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id       TEXT NOT NULL,
    setting_key   TEXT NOT NULL,
    setting_value TEXT NOT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, setting_key)
);

CREATE TABLE IF NOT EXISTS security_events (
    event_id   TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    event_type TEXT NOT NULL,
    severity   TEXT NOT NULL,
    snippet    TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_documents_checksum      ON documents(checksum_sha256);
CREATE INDEX IF NOT EXISTS idx_documents_source_path   ON documents(source_path);
CREATE INDEX IF NOT EXISTS idx_documents_user_id       ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document_id      ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session      ON chat_turns(session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user      ON chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_learned_facts_category  ON learned_facts(category);
CREATE INDEX IF NOT EXISTS idx_learned_facts_created   ON learned_facts(created_at);
CREATE INDEX IF NOT EXISTS idx_learned_facts_user      ON learned_facts(user_id);
CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_id     ON habit_logs(habit_id);
CREATE INDEX IF NOT EXISTS idx_habit_logs_logged_at    ON habit_logs(logged_at);
CREATE INDEX IF NOT EXISTS idx_habits_user_id          ON habits(user_id);
CREATE INDEX IF NOT EXISTS idx_todos_due_at            ON todos(due_at);
CREATE INDEX IF NOT EXISTS idx_todos_pending           ON todos(completed_at, notified_at, due_at);
CREATE INDEX IF NOT EXISTS idx_todos_user_id           ON todos(user_id);
CREATE INDEX IF NOT EXISTS idx_security_events_user    ON security_events(user_id);
CREATE INDEX IF NOT EXISTS idx_security_events_type    ON security_events(event_type);

-- HNSW index for fast cosine similarity search on embeddings
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_hnsw
    ON chunk_embeddings USING hnsw (embedding extensions.vector_cosine_ops);
