PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    parser_name TEXT NOT NULL,
    content_length INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    source_type TEXT NOT NULL DEFAULT 'local',
    source_url TEXT,
    ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    document_checksum_sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents (document_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_documents_checksum ON documents (checksum_sha256);
CREATE INDEX IF NOT EXISTS idx_documents_source_path ON documents (source_path);
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks (document_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_document_index ON chunks (document_id, chunk_index);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions (session_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_turns_session ON chat_turns (session_id, turn_index);

CREATE TABLE IF NOT EXISTS learned_facts (
    fact_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    confidence_score REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TEXT,
    usage_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_learned_facts_category ON learned_facts (category);
CREATE INDEX IF NOT EXISTS idx_learned_facts_created ON learned_facts (created_at);

CREATE TABLE IF NOT EXISTS whatsapp_sessions (
    phone_number  TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_active   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS whatsapp_usage_daily (
    usage_date  TEXT PRIMARY KEY,
    sent_count  INTEGER NOT NULL DEFAULT 0,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS whatsapp_usage_alerts (
    usage_date  TEXT NOT NULL,
    threshold   INTEGER NOT NULL,
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (usage_date, threshold)
);

CREATE TABLE IF NOT EXISTS habits (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL COLLATE NOCASE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    reminder_time   TEXT DEFAULT '21:00',
    active          INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS habit_logs (
    id          TEXT PRIMARY KEY,
    habit_id    TEXT NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    logged_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    status      TEXT DEFAULT 'done',
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_id ON habit_logs(habit_id);
CREATE INDEX IF NOT EXISTS idx_habit_logs_logged_at ON habit_logs(logged_at);

CREATE TABLE IF NOT EXISTS todos (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    list_name       TEXT,
    due_at          DATETIME,
    completed_at    DATETIME,
    notified_at     DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_todos_due_at ON todos(due_at);
CREATE INDEX IF NOT EXISTS idx_todos_pending ON todos(completed_at, notified_at, due_at);

CREATE TABLE IF NOT EXISTS nudge_context (
    phone_number    TEXT PRIMARY KEY,
    habit_id        TEXT NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    sent_at         DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS named_sessions (
    name       TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
