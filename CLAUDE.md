# Chief of Staff Agent — Claude Code Context

## Project overview

Personal AI agent ("Sage") with RAG, habit tracking, WhatsApp integration, and a web UI. FastAPI backend, deployed to GCP Cloud Run.

## Storage backends

The app switches between local and cloud storage via `DATABASE_URL`:

| Env var set?   | Registry        | Vector store   |
|----------------|-----------------|----------------|
| No (local dev) | SQLiteRegistry  | ChromaDB       |
| Yes (cloud)    | PostgresRegistry| PgVectorStore  |

Factory is in `app/storage/factory.py`. Never instantiate `SQLiteRegistry` or `ChromaStore` directly — always use `create_registry()` / `create_vector_store()`.

## Database migrations

Migrations live in `scripts/migrations/` as timestamped SQL files and are applied to Supabase via MCP.

**Workflow for schema changes:**

1. Write the SQL file: `scripts/migrations/YYYYMMDDHHMMSS_description.sql`
2. Apply via MCP — tell Claude: "apply the migration in scripts/migrations/<file>"
3. Claude calls `mcp__supabase__apply_migration` — Supabase tracks it in `supabase_migrations`
4. If adding/altering columns, also update `_migrate_columns()` in `app/storage/sqlite_registry.py` for local dev parity

**Never apply schema changes directly in the Supabase dashboard** without also saving the SQL to `scripts/migrations/` — otherwise the repo and DB get out of sync.

## Environment variables

Key vars (see `.env`):

```
DATABASE_URL=postgresql://postgres:[password]@db.qhzitilsywqtfxuzyioy.supabase.co:5432/postgres
GROQ_API_KEY=...
ORCHESTRATOR_CHAT_MODEL=groq:llama-3.3-70b-versatile
ACTION_CHAT_MODEL=groq:llama-3.3-70b-versatile
```

## Supabase project

- Project ref: `qhzitilsywqtfxuzyioy`
- URL: `https://qhzitilsywqtfxuzyioy.supabase.co`
- MCP server configured in `.mcp.json`

## LLM providers

- **Local**: Ollama (`llama3.2:3b` for chat, `nomic-embed-text` for embeddings)
- **Cloud**: Groq (`llama-3.3-70b-versatile`) — no embeddings, Ollama still needed for vectors

Provider factory: `app/providers/factory.py`. Model specs use `provider:model` format (e.g. `groq:llama-3.3-70b-versatile`).

## Running locally

```bash
docker compose up          # includes Ollama
sage serve --port 8000     # or without Docker
```

## Deployment target

GCP Cloud Run. See deployment plan in `docs/`.
