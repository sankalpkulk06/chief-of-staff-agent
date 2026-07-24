# CLI Feature Parity — Web/API vs CLI

_Last updated: 2026-07-22 (branch `feat/enhancements`)_

## Context

Sage exposes the same underlying agent through three channels — the CLI (`sage …`),
the web UI / HTTP API, and the WhatsApp webhook. Because the CLI `chat`, the web chat
endpoint, and the WhatsApp webhook all instantiate the **same** `ChatService` (via
`create_chat_service`, `app/cli/commands_ask.py`), anything the conversational agent can do
is reachable from all three.

The gaps are in the surfaces _around_ that shared agent: account/admin operations, OAuth
setup flows, and proactive/scheduled push. This doc tracks which of those are available in
the CLI, and the plan to close the rest.

### Status legend
- ✅ **Done** — built, tested, committed
- 🔜 **Planned** — on the roadmap, not started
- ⏸ **Not planned / Won't do** — out of scope for the CLI (architectural mismatch or already covered)

## Gap tracker

| # | Priority | Feature (Web/API-only gap) | Status | Notes |
|---|----------|----------------------------|--------|-------|
| 1 | P0 | **Multi-user auth / CLI identity** | ✅ Done | `sage login`/`logout`/`whoami`, persisted `data/session.json` (mode 600, no password on disk), auto local-user mode when auth is disabled, shared `resolve_cli_user`. Commits `f357bda`, `80610fc`. |
| 2 | P1 | **Gmail OAuth connect** (`sage email connect`) | ✅ Done | Localhost loopback OAuth: `sage email connect [--work]` / `status` / `disconnect`. Shared helper `app/cli/oauth_flow.py`. |
| 3 | P2 | **Calendar/Tasks OAuth connect** | ✅ Done | `sage calendar connect` / `status` / `disconnect` on the same shared loopback helper (`account_type="google_calendar"`). |
| 9 | P3 | **CLI HITL resolver** (approve/deny) | 🔜 Planned | Pure local op; no OAuth/push dependency. Independent of #2/#3. |
| 6 | P4 | **Session CRUD** (list/rename/delete) | 🔜 Planned | Local DB ops; quality-of-life. Today the CLI only supports `--resume`. |
| 5 | P5 | **Profile** (get/delete) | 🔜 Planned | Trivial wrap; depends on #1 (done). |
| 4 | P6 | **Analytics / usage** | 🔜 Planned | Read-only `sage stats`; low urgency. |
| 8 | — | **Web document upload** | ✅ N/A (already covered) | `sage ingest` is the CLI equivalent — no real gap. |
| 7 | — | **Live trace streaming (SSE)** | ⏸ Not planned | Web-native; CLI chat already prints agent steps inline. |
| 10 | — | **Todo reminders (push)** | ⏸ Won't do (by design) | Scheduler + WhatsApp; not a CLI action. |
| 11 | — | **Habit nightly nudges (push)** | ⏸ Won't do (by design) | Same — scheduled push. |
| 12 | — | **Morning briefing + news (push)** | ⏸ Won't do (by design) | Same — scheduled push. |

### Bonus fixes (not originally in the gap list)

| Item | Status | Notes |
|------|--------|-------|
| `email-personal` / `email-work` were **crashing** (`TypeError` from the old file-based `EmailService` API) | ✅ Done | Rewired to the per-user DB-token pattern during P0; now identity-gated and fail gracefully. |
| 8 pre-existing `test_chat_command` failures | ✅ Done | Fixed while wiring the new auth resolver (they broke when the auth gate was added in an earlier commit). |

## P0 — what shipped

**Goal:** extract CLI identity into one shared, persisted layer so every identity-dependent
command resolves the same authenticated `user_id` without re-prompting — the prerequisite
for per-user OAuth tokens in #2/#3.

**Delivered**
- `app/cli/session.py` — `resolve_cli_user()` + local session file
  (`data/session.json`, mode 600, `{user_id, username}` only). Auto default local user when
  `SAGE_PASSPHRASE` is unset (matches the server's "empty passphrase disables auth" convention).
- `app/cli/commands_auth.py` — `sage login` / `logout` / `whoami`.
- `chat` now uses the shared resolver (persisted + local-mode aware); removed the inline,
  chat-only `_prompt_auth`.
- `email-personal`/`email-work` rewired to the DB-token pattern used by `email_agent`.
- `get_user_by_id` on both registries (SQLite + Postgres) to validate persisted sessions.

**Behavior**
- **Local mode** (`SAGE_PASSPHRASE=""`): auto local user, no prompt.
- **Auth-enabled** (`SAGE_PASSPHRASE` set): interactive login/signup on a TTY; log in once,
  then all commands reuse the persisted session.
- **Non-interactive** (auth-enabled, no session): clean exit 1 → "Run `sage login`".
- **Stale session** (user deleted from DB): rejected and replaced, never blindly trusted.

**Test:** `bash scripts/test-p0.sh` — one-command, self-isolating (local SQLite + throwaway
temp `DATA_DIR`) smoke test covering all non-interactive P0 checks (12/12 passing). The
interactive login menu needs a real TTY, so it's exercised manually.

## P1 + P2 — what shipped (`sage email connect` / `sage calendar connect`)

Localhost loopback OAuth, no web server. `app/cli/oauth_flow.py:run_loopback_oauth()` is a
shared helper (duck-typed over `EmailService`/`CalendarService`): opens the consent URL,
captures the redirect on a one-shot `http://localhost:8765/callback` server, calls
`exchange_code()`, and stores the token via `upsert_email_token(user_id, …, account_type)`.
Commands in `app/cli/commands_connect.py`, wired as Typer sub-apps in `app/cli/app.py`.

**One-time prerequisite:** add `http://localhost:8765/callback` to the OAuth client's
Authorized redirect URIs in the Google Cloud Console (the same "web" client in
`GOOGLE_CLIENT_SECRETS_JSON`; web clients allow localhost — no "desktop" client needed).

**Tests:** `tests/cli/test_oauth_flow.py` drives the loopback with a stub service (happy path,
user-denied, state-mismatch) — no real Google round-trip.

## CLI command reference (current)

| Command | Gated? | What it does |
|---------|--------|--------------|
| `sage login` / `logout` / `whoami` | — | Manage the persisted CLI session (P0). |
| `sage chat` / `sage` / `sage --resume <id>` | ✅ identity | Full multi-agent chat (RAG, research, todos, habits, facts, email, calendar). |
| `sage ask "<q>"` | — | One-shot RAG Q&A (RAG-only, no tools). |
| `sage ingest -p <path>` | — | Ingest a file/dir into the RAG store. |
| `sage sources` | — | List ingested sources. |
| `sage email connect [--work]` / `status` / `disconnect` | ✅ identity | Connect/manage Gmail via loopback OAuth (P1). |
| `sage calendar connect` / `status` / `disconnect` | ✅ identity | Connect/manage Google Calendar + Tasks via loopback OAuth (P2). |
| `sage email-personal` / `email-work` | ✅ identity | Fetch + AI-triage Gmail (needs `email connect` first). |
| `sage config` | — | Show resolved settings. |
| `sage serve [--port]` | — | Boot the FastAPI server (web UI + API + WhatsApp webhook). |
