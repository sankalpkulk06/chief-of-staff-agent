-- Distinguish calories eaten (intake) from calories burned in a workout, in the same
-- append-only table. Existing rows are intake by default. Local SQLite parity is handled
-- in app/storage/sql_schema.sql + SQLiteRegistry._migrate_columns().

ALTER TABLE calorie_entries ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'intake';
