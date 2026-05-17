# Phase 2 — Cloud Migration

Move from local-only storage (SQLite + local ChromaDB) to cloud-hosted databases
so the deployed app has persistent state. Keep the storage abstraction layer so
local dev still works with SQLite (controlled by env var).

**Chosen stack:**
- **Supabase** (managed Postgres) → replaces SQLite
- **Qdrant Cloud** → replaces local ChromaDB (free tier, no card needed, better cloud story)
- Alternatively: keep ChromaDB but run it as a sidecar on Cloud Run (simpler but less scalable)

---

## 2.1 Abstract the Storage Layer

Before touching any cloud service, add storage interfaces so the same code
works locally and in the cloud.

**Tasks:**
- [ ] Create `app/storage/base.py` — define `RegistryStore` protocol (all methods currently on `SQLiteRegistry`)
- [ ] Create `app/storage/vector_store_base.py` — define `VectorStore` protocol (all methods on `ChromaStore`)
- [ ] Update all imports across `app/` to use the protocols, not concrete classes
- [ ] Add `STORAGE_BACKEND` env var: `sqlite` (default) | `postgres`
- [ ] Add `VECTOR_BACKEND` env var: `chroma` (default) | `qdrant`
- [ ] Create `app/storage/factory.py` that returns the right implementation based on env vars

---

## 2.2 Migrate SQLite → Supabase (Postgres)

**Tasks:**
- [ ] Set up a free Supabase project at supabase.com
- [ ] Add `asyncpg>=0.29` and `sqlalchemy[asyncio]>=2.0` to `requirements.txt`
- [ ] Create `app/storage/postgres_registry.py` implementing `RegistryStore`
  - Port all table definitions from `app/storage/sql_schema.sql` to SQLAlchemy models
  - Tables: `documents`, `chunks`, `chat_sessions`, `chat_turns`, `learned_facts`,
    `todos`, `habits`, `habit_logs`, `nudge_context`, `whatsapp_sessions`,
    `whatsapp_usage_daily`, `whatsapp_usage_alerts`, `named_sessions`, `security_events`
  - Use `asyncpg` driver for async queries
- [ ] Add to `app/config/settings.py`:
  - `database_url: str = ""` (Supabase connection string, e.g. `postgresql+asyncpg://...`)
  - `storage_backend: str = "sqlite"` — `sqlite` | `postgres`
- [ ] Write a one-shot migration script `scripts/migrate_to_postgres.py`:
  - Reads all rows from local SQLite
  - Inserts them into Supabase Postgres
  - Idempotent (uses upsert on primary keys)
- [ ] Verify all existing API endpoints work with Postgres backend (same integration tests)

---

## 2.3 Migrate Vector Store → Qdrant Cloud (or hosted ChromaDB)

**Option A — Qdrant Cloud (recommended for the assignment demo):**
- Free tier: 1 GB, no credit card
- REST API, Python client, easy setup

**Option B — Keep ChromaDB, run as Cloud Run sidecar:**
- No code changes to retrieval layer
- ChromaDB persistent volume on Cloud Run (not recommended for production)

**Tasks (Option A — Qdrant):**
- [ ] Sign up for Qdrant Cloud, create a cluster, copy API key + URL
- [ ] Add `qdrant-client>=1.9` to `requirements.txt`
- [ ] Create `app/storage/qdrant_store.py` implementing `VectorStore` protocol
  - Map ChromaDB upsert/query interface to Qdrant equivalents
  - Collection name: `sage_documents` (one collection, metadata field for content type)
- [ ] Add to `app/config/settings.py`:
  - `qdrant_url: str = ""`
  - `qdrant_api_key: str = ""`
  - `vector_backend: str = "chroma"` — `chroma` | `qdrant`
- [ ] Write migration script `scripts/migrate_vectors_to_qdrant.py`:
  - Reads all vectors from local ChromaDB
  - Upserts them into Qdrant
- [ ] Update embeddings: if using Groq (no embeddings API), fall back to Gemini text-embedding-004
  or keep Ollama embeddings locally during dev; use Gemini in cloud

---

## 2.4 Environment & Secrets

**Tasks:**
- [ ] Document all required env vars in `.env.example` (never commit actual keys)
- [ ] Verify `.gitignore` excludes `.env`, `data/credentials/`, `data/sqlite/`, `data/chroma/`
- [ ] Create `docs/env-vars.md` listing every env var, its purpose, and where to get it
- [ ] For local dev: `.env` file
- [ ] For cloud: Cloud Run environment variables (set via `gcloud` CLI or GCP Console)

**Required env vars for cloud deployment:**
```
LLM_PROVIDER=groq (or gemini)
GROQ_API_KEY=...
GEMINI_API_KEY=...
EMBEDDINGS_PROVIDER=gemini
STORAGE_BACKEND=postgres
DATABASE_URL=postgresql+asyncpg://...supabase...
VECTOR_BACKEND=qdrant
QDRANT_URL=...
QDRANT_API_KEY=...
TAVILY_API_KEY=...
SAGE_PASSPHRASE=...
TWILIO_ACCOUNT_SID=... (optional)
TWILIO_AUTH_TOKEN=... (optional)
```
