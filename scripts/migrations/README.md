# Database Migrations

Migrations are applied to Supabase via the MCP tool (`apply_migration`) and tracked in the `supabase_migrations` table. SQL files here are the source of truth — one file per migration, version-controlled alongside the code.

## File naming

```
YYYYMMDDHHMMSS_description.sql
```

Example: `20260517194807_initial_schema.sql`

The timestamp prefix matches the version Supabase assigns when the migration is applied.

## How to create a new migration

1. **Write the SQL file** in this folder:
   ```
   scripts/migrations/YYYYMMDDHHMMSS_your_description.sql
   ```
   Use `date +%Y%m%d%H%M%S` to generate the timestamp.

2. **Tell Claude to apply it:**
   > "Apply the migration in scripts/migrations/20260518120000_add_priority_to_todos.sql"

   Claude will read the file and call `apply_migration` via the Supabase MCP.

3. **Supabase tracks it** — the migration version is stored in `supabase_migrations`. Running it again is a no-op (Supabase skips already-applied versions).

## Also update SQLite (local dev)

If the migration adds or alters columns, update `_migrate_columns()` in `app/storage/sqlite_registry.py` so local dev stays in sync.

## Applied migrations

| Version              | Description      | Date       |
|----------------------|------------------|------------|
| 20260517194807       | initial_schema   | 2026-05-17 |
