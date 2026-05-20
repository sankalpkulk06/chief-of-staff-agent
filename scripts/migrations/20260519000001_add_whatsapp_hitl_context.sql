CREATE TABLE IF NOT EXISTS whatsapp_hitl_context (
    phone_number TEXT PRIMARY KEY,
    hitl_id      TEXT NOT NULL,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
