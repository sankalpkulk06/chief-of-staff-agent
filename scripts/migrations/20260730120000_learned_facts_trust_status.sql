-- Passive fact-learning: provenance/trust tiers + dedup/supersede bookkeeping on learned_facts.
-- SQLite parity lives in SQLiteRegistry._migrate_columns().

ALTER TABLE learned_facts ADD COLUMN IF NOT EXISTS trust         TEXT NOT NULL DEFAULT 'high';
ALTER TABLE learned_facts ADD COLUMN IF NOT EXISTS status        TEXT NOT NULL DEFAULT 'confirmed';
ALTER TABLE learned_facts ADD COLUMN IF NOT EXISTS content_key   TEXT;
ALTER TABLE learned_facts ADD COLUMN IF NOT EXISTS superseded_by TEXT;

-- Dedup/supersede lookups hit (user_id, content_key); status filters exclude superseded rows.
CREATE INDEX IF NOT EXISTS idx_learned_facts_content_key ON learned_facts (user_id, content_key);
CREATE INDEX IF NOT EXISTS idx_learned_facts_status      ON learned_facts (user_id, status);
