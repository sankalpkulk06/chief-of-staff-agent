-- Itemized meal breakdown + macro nutrition on calorie entries. Local SQLite parity is in
-- app/storage/sql_schema.sql + SQLiteRegistry._migrate_columns().

ALTER TABLE calorie_entries ADD COLUMN IF NOT EXISTS dish      TEXT;
ALTER TABLE calorie_entries ADD COLUMN IF NOT EXISTS protein_g REAL DEFAULT 0;
ALTER TABLE calorie_entries ADD COLUMN IF NOT EXISTS carbs_g   REAL DEFAULT 0;
ALTER TABLE calorie_entries ADD COLUMN IF NOT EXISTS fat_g     REAL DEFAULT 0;
