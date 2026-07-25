# CLI OAuth Connect — `sage email connect` & `sage calendar connect`

_Roadmap: P1 (#2) + P2 (#3) in `docs/cli-feature-parity.md`. Status: ✅ implemented — see `app/cli/oauth_flow.py`, `app/cli/commands_connect.py`, `tests/cli/test_oauth_flow.py`._

## Context

P0 (CLI identity) shipped: `resolve_cli_user()` gives every command a real `user_id`, and
`sage email-personal` already prints _"Gmail isn't connected — run `sage email connect`
(coming soon)"_. Those commands can't do anything until a Google OAuth **token exists for
that user_id** in the `user_email_tokens` table. Today the only way to obtain one is the web
OAuth flow (`/api/v1/email/oauth/start` + `/callback`), so Gmail/Calendar are effectively
web-setup-only even though the CLI can *use* the tokens.

**Goal:** add CLI-native `sage email connect` and `sage calendar connect` that run the Google
consent handshake locally (localhost loopback), obtain the token, and store it per-user — so
Gmail triage and the daily planner work end-to-end from the CLI with no web server.

**Why both together:** they're ~95% identical. Exploration confirmed email and calendar
share the **same** client secrets (`GOOGLE_CLIENT_SECRETS_JSON`), the **same** token table
(`user_email_tokens`, PK `(user_id, account_type)`), and the **same** OAuth trio
(`get_oauth_url` / `exchange_code` / `build_service`). They differ only in:
- `account_type` key: Gmail `"personal"`/`"work"` vs Calendar `"google_calendar"`.
- scopes: owned entirely by each service object (`EmailService` = gmail.readonly;
  `CalendarService` = calendar.events + tasks). The CLI needs no scope logic.

So one shared loopback helper serves both; the commands just pass a different service +
account_type.

## Key facts from exploration (reuse, don't rebuild)

- `EmailService` (`app/services/email_service.py`): `get_oauth_url(redirect_uri, state)` :77,
  `exchange_code(code, redirect_uri, state)` :95 (already sets `OAUTHLIB_INSECURE_TRANSPORT=1`
  so `http://localhost` works), returns a normalized token dict. Constructor
  `EmailService(client_secrets, account_type="personal")` :42.
- `CalendarService` (`app/services/calendar_service.py`): identical `get_oauth_url` :110 /
  `exchange_code` :130; constructor `CalendarService(client_secrets)`. `account_type` is the
  constant `"google_calendar"` (used in `app/api/calendar.py:19`,
  `calendar_plan_executor.py:32`, `planner_service.py:32`).
- Token storage (both registries): `upsert_email_token(user_id, token_json, account_type)`,
  `get_email_token`, `has_email_token`, `delete_email_token` — `app/storage/sqlite_registry.py`
  :779/:770/:807/:800 and pg mirror. Table from
  `scripts/migrations/20260519000000_gmail_tokens.sql`. **No new migration needed.**
- `get_google_client_secrets(settings)` (`app/config/settings.py:132`) decodes env
  `GOOGLE_CLIENT_SECRETS_JSON` (a **"web"**-type credentials.json). Client only uses
  `client_id`/`client_secret`; it does **not** read `redirect_uris`.
- `store_oauth_state`/`pop_oauth_state` are **not needed** — that's CSRF recovery for the
  shared-server web callback. The CLI owns both ends, so it generates its own `state` and
  verifies it in-process.
- User identity: `resolve_cli_user(settings, registry, console)` (`app/cli/session.py:136`).
- **No loopback helper exists today** (grep for `run_local_server`/`HTTPServer`/`127.0.0.1`
  finds nothing). The stale `/configure email` in `commands_chat.py:297` uses the removed
  file-based API — **do not model on it**.

## Approach

### 1. Shared loopback helper — `app/cli/oauth_flow.py`

```python
OAUTH_LOOPBACK_PORT = 8765  # must match the URI registered in Google Cloud Console

def run_loopback_oauth(*, service, account_type, registry, user_id, console,
                       port=OAUTH_LOOPBACK_PORT, timeout=180) -> bool:
    ...
```

`service` is duck-typed (any object with `get_oauth_url` + `exchange_code`) — both
`EmailService` and `CalendarService` qualify. Steps:

1. `redirect_uri = f"http://localhost:{port}/callback"`; `state = secrets.token_urlsafe(24)`.
2. `auth_url = service.get_oauth_url(redirect_uri, state)`.
3. Start a one-shot loopback server: `http.server.HTTPServer(("localhost", port), Handler)`.
   Handler captures `GET /callback?code=…&state=…`, verifies `state` matches, stashes the
   `code` on the server object, and returns a small HTML "✓ Connected — you can close this
   tab." (or an error page on `?error=` / state mismatch). Ignore `/favicon.ico` requests.
   Loop `server.handle_request()` (with `server.timeout`) until the code is captured or the
   deadline passes.
4. `webbrowser.open(auth_url)` **and** print the URL (fallback when no browser can open, e.g.
   the user copies it to a browser on the same machine).
5. On capture: `token_json = service.exchange_code(code, redirect_uri, state)` →
   `registry.upsert_email_token(user_id, token_json, account_type)` → return `True`.

Error handling with clear messages: bind failure (`OSError` → "port {port} busy, pass
`--port`"), user-denied (`?error=access_denied`), state mismatch, timeout, `KeyboardInterrupt`.

### 2. Commands — `app/cli/commands_connect.py`

`CALENDAR_ACCOUNT_TYPE = "google_calendar"` (local constant, matching the three existing
definitions — avoids importing the FastAPI `app/api/calendar.py` into the CLI).

Each builds `settings`, `registry = create_registry(settings.database_url,
paths.sqlite_db_path)`, `user = resolve_cli_user(...)`, and
`client_secrets = get_google_client_secrets(settings)` (error + exit if `None`).

- `email_connect_command(work: bool, port: int)` → `account_type = "work" if work else
  "personal"`; `service = EmailService(client_secrets, account_type=account_type)`;
  `run_loopback_oauth(...)`; on success: "✓ Gmail (personal) connected. Try
  `sage email-personal`."
- `calendar_connect_command(port: int)` → `service = CalendarService(client_secrets)`;
  `run_loopback_oauth(..., account_type=CALENDAR_ACCOUNT_TYPE)`; success message.
- `email_status_command(work)` / `calendar_status_command()` → `has_email_token(...)` →
  "connected"/"not connected".
- `email_disconnect_command(work)` / `calendar_disconnect_command()` →
  `delete_email_token(...)` → confirmation.

### 3. Typer wiring — `app/cli/app.py`

Add two sub-apps (matches the `sage email connect` UX already referenced in the placeholder
message and the roadmap doc); keep the existing flat `email-personal`/`email-work`:

```python
email_app = typer.Typer(help="Gmail integration.")
email_app.command("connect")(...)      # --work, --port
email_app.command("status")(...)
email_app.command("disconnect")(...)
cli.add_typer(email_app, name="email")

calendar_app = typer.Typer(help="Google Calendar + Tasks integration.")
# connect (--port) / status / disconnect
cli.add_typer(calendar_app, name="calendar")
```

### 4. Drop the "(coming soon)" note

Update the message in `app/cli/commands_email.py:38-41` to just point at `sage email connect`.

## External prerequisite (must be documented loudly)

The loopback redirect URI **`http://localhost:8765/callback`** must be added to the OAuth
client's **Authorized redirect URIs** in the Google Cloud Console (the same "web" client whose
JSON is in `GOOGLE_CLIENT_SECRETS_JSON`). Web clients allow `http://localhost` redirects, so
**no switch to a "desktop" client type is required**. The URI must match byte-for-byte
(host, port, `/callback`, no trailing slash). This is a one-time console change the user makes;
the CLI will print a reminder if the exchange fails with a redirect-mismatch error.

_Limitation:_ on a headless/SSH box the browser + loopback are on different machines — the
user must run connect on a machine with a browser (or SSH-forward the port). Documented, not
blocked.

## Files

- **New:** `app/cli/oauth_flow.py`, `app/cli/commands_connect.py`
- **Modified:** `app/cli/app.py` (two Typer sub-apps), `app/cli/commands_email.py` (drop
  "coming soon"), `docs/cli-feature-parity.md` (flip #2/#3 to ✅)

## Out of scope

- Cleaning up the stale `/configure email` handler in `commands_chat.py:297` (separate).
- Moving `email-personal`/`email-work` under the new `email` group (would break existing
  invocations; keep flat).
- Per-user RAG scoping; other roadmap gaps (#4/#5/#6/#9).

## Verification

**Automated** (no Google round-trip):
- Unit-test `run_loopback_oauth` with a **stub service** (returns a fixed `auth_url` and a
  fixed token from `exchange_code`) and `webbrowser.open` monkeypatched to a no-op. Drive the
  callback from a background thread that issues
  `GET http://localhost:<port>/callback?code=X&state=<captured>` — assert the helper calls
  `exchange_code` and `upsert_email_token(user_id, token, account_type)` with the right
  account_type. Localhost sockets are deterministic in CI.
- `email status` / `calendar status` reflect a token seeded via `upsert_email_token`;
  `disconnect` clears it (drive against local SQLite, `DATABASE_URL=""`).
- `bash scripts/test-p0.sh` still green; `email-personal` message no longer says "coming soon".

**Manual** (real consent, one-time):
1. Add `http://localhost:8765/callback` to the OAuth client in Google Cloud Console.
2. `DATABASE_URL="" SAGE_PASSPHRASE="" sage email connect` → browser consent → "✓ connected".
3. `sage email-personal` now fetches + triages real mail.
4. `sage calendar connect` → consent → then a planner flow (`sage chat` → `/plan tomorrow`)
   reads the calendar token.
