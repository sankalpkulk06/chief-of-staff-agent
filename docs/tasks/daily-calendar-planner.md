# Daily Calendar Planner for Sage

## Context

Sage is a personal chief-of-staff agent. We want it to **plan the user's day on Google Calendar**:

- On demand (`/plan tomorrow`), Sage asks what the user has planned, then proposes a
  time-blocked schedule (e.g. *gym 08:00–09:30, work till 12:00*) built from the user's
  **existing calendar events + open todos + tracked habits + Google Tasks + what they say
  in the check-in**. (Apple Reminders is a deferred follow-up — see future work.)
- Every calendar write goes through Sage's existing **HITL approval gate** — nothing is
  written without an explicit "approve".
- When the user later says *"I changed my plan"*, Sage **re-plans and reconciles**: it adds
  new blocks and **soft-cancels** removed ones (Google `status:"cancelled"`, never a hard
  delete), and it **only ever touches events Sage itself created** — never the user's own
  events.

Four of the five needed primitives already exist (per-user Google OAuth with refresh, a HITL
DB gate, a shared agent entry point, and a multi-turn "answer the agent's question" pattern).
The genuinely new work is a Calendar service, a Planner agent with a **deterministic diff
engine**, per-user timezone support, and generalizing the multi-turn state so it works on
**both web and WhatsApp**.

### Locked product decisions
- **Surface: both** web UI and WhatsApp → the multi-turn state lives in `ChatService`, not the webhook.
- **Trigger: on-demand first** via `/plan` (a nightly per-user cron is explicit future work — see end).
- **Plan source:** auto-pull calendar + Sage todos + habits + **Google Tasks**, then layer in
  the check-in conversation. **Apple Reminders deferred** (no cloud API; Sage runs on Cloud Run
  with no Mac — the only server-side path is CalDAV/iCloud, decided as a follow-up).
- **Task scope: all open tasks** are scheduling candidates (not just those due by the plan date).
  The planner prioritizes (due date, then priority) and schedules a sensible subset; **it must
  not force every task into the day** — overflow tasks are reported as "didn't fit", never crammed.
- **Task sources are read-only** — Sage does not mark Google Tasks / todos complete; it only
  reads them as scheduling input. (Marking done on block completion is future work.)
- **Deletes are soft**, and Sage never touches an event lacking its provenance tag.

### Two corrections surfaced during design (important)
1. **Two** resolve paths hard-code `ActionAgent`, not one: `app/api/hitl.py` **and**
   `app/webhook/server.py` `_resolve_hitl_whatsapp` (~`:302-318`). Both must dispatch by
   `action_type` or WhatsApp approval of a calendar batch will misfire.
2. The OAuth **token/oauth-state methods and tables exist only in `PostgresRegistry`** —
   `SQLiteRegistry` has none. Calendar-on-local-dev requires adding them to SQLite too (or
   documenting `/plan` as Postgres-only). This plan adds them.

---

## Design at a glance

```
/plan tomorrow ─┐
                ▼
     _maybe_start_plan()  ── gathers events+todos+habits+Google Tasks, sets pending_prompt, asks
                ▼  (user replies "gym earlier, drop the 3pm")
  answer_in_session() ── pending-prompt interception (BEFORE orchestrator) captures the reply
                ▼
        PlannerAgent ── LLM proposes TimeBlock[]  (JSON, house regex+json.loads convention)
                ▼
   plan_diff.py (PURE PYTHON) ── overlap-validate vs fixed events → reconcile desired vs
                ▼                  current Sage events → {create, patch, soft_cancel} ops
        HITL batch row ── action_type="apply_calendar_plan", action_payload={operations:[...]}
                ▼  (user approves — web or WhatsApp)
   hitl_dispatch → CalendarPlanExecutor ── etag re-check, sage_managed re-assert, apply ops
                ▼
         Google Calendar (only Sage-tagged events touched)
```

The LLM only proposes blocks. **All interval/overlap arithmetic and the create/patch/cancel
decision are pure Python** — never trusted to the model.

---

## Work items

### 1. Timezone prerequisite (do first — pure, unit-testable)
Google needs tz-aware RFC3339 datetimes; the app currently has **no timezone support**
(~39 naive `datetime.now()` calls). Isolate this in one module rather than touching the rest.

- New `app/core/timezone_util.py` (stdlib `zoneinfo`, no I/O): `resolve_tz(registry, user_id) -> ZoneInfo`,
  `now_local(tz)`, `to_rfc3339(local_dt, tz)`, `parse_rfc3339(s) -> aware dt`.
- Store the user's IANA tz in the **existing `user_settings` k/v table** (key `"timezone"`) via
  `get_user_setting`/`set_user_setting` (`sqlite_registry.py:680`, PG sibling) — **no column migration**.
  Fall back to a new `settings.default_timezone` (default `"UTC"`).
- First `/plan` with tz unset asks once and persists it.

### 2. SQLite token/oauth parity (unblocks local dev)
Add to `SQLiteRegistry` the tables + six methods that today live only in `PostgresRegistry`
(`pg_registry.py:768-835`): tables `user_email_tokens`, `oauth_states` (DDL in `_migrate_columns`,
`sqlite_registry.py:46-128`); methods `get_email_token`, `upsert_email_token`, `has_email_token`,
`delete_email_token`, `store_oauth_state`, `pop_oauth_state` — mirror the PG signatures exactly
(SQLite `?` + `json.dumps` TEXT).

### 3. CalendarService — `app/services/calendar_service.py` (clone `email_service.py`)
Auth half is a near-line-for-line copy; calendar ops are new.

- `GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.events",
  "https://www.googleapis.com/auth/tasks.readonly"]` — **request Calendar *and* Google Tasks
  in the same consent** so one authorization covers both. (Tasks is read-only.)
- **Separate token row**, `account_type="google_calendar"` — the existing Gmail token is
  `gmail.readonly` only and won't authorize Calendar/Tasks; **existing users re-consent once**
  for the combined scope set.
- Auth methods mirror `EmailService` (`email_service.py:50-122`): `build_service` returns
  `(service, refreshed_or_None)`; `get_oauth_url` (`access_type=offline, prompt=consent`);
  `exchange_code` normalizes to the authorized-user dict.
- Calendar ops (each returns `(result, refreshed_or_None)`; caller persists refreshed token via
  `upsert_email_token(user_id, refreshed, account_type="google_calendar")`):
  - `list_events(time_min, time_max)` — `singleEvents=True, orderBy="startTime"` → **fixed user events**.
  - `list_managed_events(...)` — `events().list(privateExtendedProperty="sage_managed=true")` → **only Sage's events**.
  - `list_open_tasks()` — Google Tasks API (`build("tasks","v1")` with the *same* token):
    enumerate task lists (`tasklists().list()`), then `tasks().list(tasklist=id, showCompleted=False)`
    for each → returns all incomplete tasks with `{title, due?, notes?, id, tasklist}`. Read-only.
    (Google Tasks `due` is date-only, no time — treated as an all-day scheduling hint.)
  - `insert_event(block_id, title, start, end, tz)` — sets
    `extendedProperties.private = {"sage_managed":"true","sage_block_id":block_id}`.
  - `patch_event(google_event_id, etag, fields)` — send `If-Match: etag` → **412 on drift** → raise `CalendarConflictError`.
  - `soft_cancel_event(google_event_id, etag)` — `events().patch(body={"status":"cancelled"})`, **never `delete`**.
- **Every mutation asserts the target carries `sage_managed=true`** before writing (defense in depth).
- New OAuth HTTP endpoints in `app/api/calendar.py` mirroring `app/api/email.py`
  (`/calendar/oauth/start`, `/callback`, `/status`, `/disconnect`); register in `app/api/router.py`.
- Construct `CalendarService` in `create_chat_service` (`commands_ask.py`) and thread it
  through `ChatService.__init__` → `AgentRunner.__init__` → `_rebuild_agents`, exactly mirroring
  the optional `email_service` thread.

### 4. Provenance mirror table `sage_calendar_events` (both registries, three-touch)
Local mirror of Sage's events (Google stays source-of-truth for content; this stores the
`google_event_id ↔ block_id` map + `etag` + soft-delete status for fast reconciliation).

Columns: `id` (PK, = `sage_block_id`), `user_id`, `google_event_id` (nullable until created),
`calendar_id` (default `'primary'`), `plan_date`, `title`, `start_local`, `end_local`,
`source_kind` (`todo|habit|conversation|existing|google_task`), `source_ref`, `etag`,
`status` (`active|cancelled`, default `active`), `created_at`, `updated_at`, `cancelled_at`.
Index `(user_id, plan_date, status)`. Soft delete = `status='cancelled'` + `cancelled_at`, filtered out of list queries (mirrors `hitl_requests.status` / `todos.completed_at IS NULL`).

**Three touches** (storage parity rule): (a) DDL in `app/storage/sql_schema.sql`;
(b) `CREATE TABLE IF NOT EXISTS` in `SQLiteRegistry._migrate_columns`; (c) new
`scripts/migrations/YYYYMMDDHHMMSS_add_sage_calendar_events.sql` (Postgres, `TIMESTAMPTZ`/`DATE`/`TEXT`).
CRUD on **both** registries: `insert_calendar_event`, `set_calendar_event_google_id`,
`update_calendar_event`, `soft_cancel_calendar_event`, `list_managed_calendar_events`, `get_calendar_event`.

### 5. `plan_diff.py` — the diff engine (pure Python, heavily unit-tested)
`app/core/plan_diff.py`. **Zero LLM.** Two stages:

- **Stage A — overlap validation.** Convert proposed `TimeBlock`s and fixed user events to
  integer minute-of-day `[start, end)` intervals. Drop any proposed block overlapping a fixed
  event (`a.start < b.end and b.start < a.end`) or another proposed block; record each as a
  `conflict` string surfaced to the user. `end == start` (touching) is allowed.
- **Stage B — reconcile desired vs current.** `current` = active Sage-managed rows for the
  `plan_date` (each with `google_event_id` + `etag`); `desired` = validated blocks. Match on
  `(source_kind, source_ref)` when present, else normalized title (time is a mutable attribute,
  not identity). Emit ops: **create** (desired, no match), **patch** (matched, time/title changed),
  **soft_cancel** (current, no desired match), no-op (unchanged).
- Output `PlanDiff(operations, conflicts, summary)`; `operations` is exactly the HITL payload.

### 6. PlannerAgent — `app/agents/planner_agent.py`
Duck-typed like the others. Gathers inputs deterministically for the target `plan_date`:
`list_events` (fixed obstacles), `list_managed_events` + local mirror (current plan),
`registry.list_todos` (all incomplete), `calendar_service.list_open_tasks()` (all incomplete
Google Tasks), `habit_service` summary, plus the conversation notes.

**Merge + dedup (pure Python, before the LLM):** unify Sage todos + Google Tasks into one
candidate list; dedup across sources by normalized title (a Google Task the user also has as a
Sage todo shouldn't be scheduled twice — prefer the Sage-todo row, keep the `google_task`
`source_ref` for traceability). Sort candidates by (has-due-date, due-date, priority). Because
**all open tasks** are candidates, the candidate list can be large — the prompt hands the LLM the
prioritized list and instructs it to schedule what realistically fits the free gaps and leave the
rest; unscheduled candidates are surfaced to the user as "didn't fit today", never force-packed.

LLM's **only** job: emit `TimeBlock[]` as JSON
(`@dataclass TimeBlock{title, start"HH:MM", end"HH:MM", source_kind, source_ref}` where
`source_kind ∈ todo|habit|conversation|google_task`), parsed with
the house convention (code-fence strip + `re.search(r"\{.*\}")` + `json.loads`, per
`action_agent.py:109-114`). Then run `plan_diff` and, if ops exist, raise HITL exactly as
`ActionAgent._dispatch` does (`metadata={"hitl_pending":True,"hitl_id":...}`).

Wiring: add `"planner_agent"` to `orchestrator.VALID_AGENTS` (`orchestrator.py:11`) and
`runner._VALID_AGENTS` (`runner.py:173-175`); construct in `runner._rebuild_agents` guarded by
`if self._calendar_service` (mirror `EmailAgent` at `:154-163`); register in `_resolve_agent`
(`:575-582`) and add a branch in `_execute_step` (`:504-537`); describe it in
`app/agents/prompts/orchestrator_plan.txt` so the planner LLM routes plan/re-plan intents.
Adding to `agent_model_specs` is **optional** (default provider fallback works, same as email).

### 7. Multi-turn pending-prompt state machine (both surfaces)
Generalize the phone-keyed `nudge_context` into a **session-keyed** `pending_prompts` table
(both registries, three-touch): `session_id` (PK), `user_id`, `prompt_type` (`plan_checkin`),
`state_json` (`{plan_date, gathered_notes:[...], stage}`), `created_at`, `expires_at` (+30 min).
Methods: `set_pending_prompt`, `get_pending_prompt` (respects expiry), `clear_pending_prompt`,
`append_pending_prompt_note`.

**Interception point (the key change):** in `ChatService.answer_in_session`, **after** slash
handling (`chat_service.py:162-172`) and **before** the orchestrator run (`:174`):
```
pending = self._registry.get_pending_prompt(session_id)
if pending and pending["prompt_type"] == "plan_checkin":
    return self._continue_plan_checkin(session_id, question, pending, ...)
```
`_continue_plan_checkin` appends the reply to `gathered_notes`, invokes PlannerAgent directly
(bypassing the orchestrator — this is a captured answer, not a fresh intent), runs the diff,
returns a `QAResult` with `hitl_pending`/`hitl_id` set. Because it lives in `answer_in_session`,
**web + WhatsApp + CLI inherit it for free**.

Capture-vs-fallthrough: while a pending prompt is live, a bare reply ("gym earlier") is consumed
as the answer and never reaches the orchestrator; once expired/cleared, `get_pending_prompt`
returns `None` and the same text flows to the orchestrator normally.

### 8. `/plan` slash command
Handle as `_maybe_start_plan(session_id, question, user_id, response_style)` placed beside the
pending-prompt interception in `answer_in_session` (it already has `session_id`; `_answer_direct_command`
does not — avoid a signature change). Behavior: resolve `plan_date` from the arg ("tomorrow"
default / "today" / ISO); ensure calendar connected (`has_email_token(user_id,"google_calendar")`)
and tz set (ask once if not); gather inputs so the opening question is specific ("You have 2
meetings and 3 open todos tomorrow — anything else?"); `set_pending_prompt(...)`; return the
question. Add `/plan` to the help listings.

### 9. HITL batch + dispatch-by-type
Payload under new `action_type="apply_calendar_plan"`:
```json
{"operations":[
  {"action":"create","block_id":"...","fields":{...}},
  {"action":"patch","block_id":"...","google_event_id":"...","etag":"...","fields":{...}},
  {"action":"soft_cancel","block_id":"...","google_event_id":"...","etag":"..."}],
 "plan_date":"2026-07-23","calendar_id":"primary","summary":"..."}
```
PlannerAgent calls `registry.create_hitl_request(..., action_type="apply_calendar_plan", action_payload={...})` — same shape as `action_agent.py:137-142`; runner HITL plumbing is already generic.

New `app/agents/hitl_dispatch.py`: `execute_approved_by_type(row, user_id, ...)` switches on
`action_type` — existing action types → `ActionAgent.execute_approved` (unchanged);
`apply_calendar_plan` → new `CalendarPlanExecutor`. **Update both resolve paths** to call it:
`app/api/hitl.py:40-65` and `app/webhook/server.py` `_resolve_hitl_whatsapp` (~`:302-318`).

New `app/agents/calendar_plan_executor.py` `apply(hitl_id, user_id)`: load calendar token
(friendly reconnect message if missing); **on each patch/soft_cancel, re-check etag / `If-Match`
→ 412 means the user changed it since the proposal → skip that op and report it**; re-assert
`sage_managed=true` on every target; execute creates → patches → soft_cancels, persisting each to
the local mirror and any refreshed token; **partial-batch tolerant** (collect applied/skipped/failed,
never whole-batch abort); return a summary AgentResult; `resolve_hitl_request(hitl_id,"approved")`.
Also re-run Stage A overlap check on `create` ops against a fresh `list_events` (a meeting may
have been booked in the interim). Rejection path unchanged — no writes.

### 10. Re-plan ("I changed my plan")
Arrives with no pending prompt → falls through to the orchestrator → routes to `planner_agent`
(via the roster prompt). Planner sees existing active rows for the `plan_date`; if the message
already contains the change, it produces a diff + HITL immediately; otherwise it sets a fresh
`plan_checkin` pending prompt. Both converge on the same diff → HITL machinery.

---

## Critical files
- `app/core/chat_service.py` — pending-prompt interception + `/plan` bootstrap in `answer_in_session` (`:150-182`)
- `app/services/email_service.py` → clone into new `app/services/calendar_service.py`
- `app/api/email.py` → clone into new `app/api/calendar.py`; register in `app/api/router.py`
- `app/agents/planner_agent.py`, `app/core/plan_diff.py`, `app/core/timezone_util.py`,
  `app/agents/hitl_dispatch.py`, `app/agents/calendar_plan_executor.py` — new
- `app/agents/runner.py` — construct/register PlannerAgent (`:120-163, 173-175, 504-537, 575-582`)
- `app/agents/orchestrator.py:11` + `app/agents/prompts/orchestrator_plan.txt` — roster/routing
- `app/api/hitl.py` **and** `app/webhook/server.py` (`_resolve_hitl_whatsapp`) — dispatch by `action_type`
- `app/storage/sqlite_registry.py` + `app/storage/pg_registry.py` + `app/storage/sql_schema.sql`
  + `scripts/migrations/` — token/oauth SQLite parity, `sage_calendar_events`, `pending_prompts`

## Build order (early testable milestone at step 3)
1. `timezone_util.py` + `user_settings` timezone. 2. SQLite token/oauth parity.
3. **CalendarService auth (Calendar+Tasks scopes) + `list_events` + `list_open_tasks` + OAuth
   endpoints ← first manual milestone: connect Google, list tomorrow's events *and* your open
   Google Tasks, no writes.** 4. `sage_calendar_events` + CRUD.
5. CalendarService writes (`insert`/`patch`/`soft_cancel`/`list_managed`) + provenance guard.
6. `plan_diff.py` + unit tests. 7. PlannerAgent (incl. task merge/dedup) + runner/orchestrator wiring.
8. `pending_prompts` + `answer_in_session` interception + `/plan` bootstrap (test 2-turn on web).
9. HITL batch: `hitl_dispatch.py` + `CalendarPlanExecutor` + both resolve paths (E2E approve/reject).
10. WhatsApp parity pass. 11. Re-plan intent.

## Edge cases (handled above)
Token missing/expired/revoked · tz unset / DST-transition day (derive from wall-clock via
`zoneinfo`, never a fixed offset) · overlapping blocks (dropped + reported) · user edits/deletes
a Sage event in Google (412 / 404 → skip + report) · user strips the `sage_managed` tag (event
treated as user-owned, left alone) · HITL expiry (410, no writes) · concurrent state drift (re-check
creates at apply time) · partial batch failure (per-op, no abort) · double-approval (409 status guard)
· `/plan` with no todos/habits/events (runs on conversation alone) · SQLite without token methods (degrade gracefully)
· **Google Task with no due date** (still a candidate under "all open"; scheduled only if it fits)
· **large open-task backlog** (planner selects a fitting subset, reports overflow — never overpacks)
· **duplicate task across sources** (same title in Sage todos + Google Tasks → deduped, scheduled once)
· **Google Tasks API disabled / no task lists** (empty candidate set → plan proceeds without tasks).

## Verification
- **Unit (highest value, no I/O):** `plan_diff` overlap math (identical/touching/inside/straddling/
  multi-overlap/empty), reconciliation (create-only / cancel-only / no-op / patch / rename keeps
  identity), `timezone_util` RFC3339 round-trip + DST widths, PlannerAgent JSON parse of messy
  LLM output, CalendarService `sage_managed` guard (mock Google client), hitl-dispatch routing,
  registry parity (calendar CRUD + `pending_prompts` + tokens identical on both backends),
  **task merge/dedup** (Sage-todo vs Google-Task same title → one candidate; sort by due/priority;
  overflow beyond a full day left unscheduled).
- **Manual E2E:** milestone read (step 3, incl. listing open Google Tasks); web full flow
  `/plan tomorrow` → answer → approve →
  verify only tagged events created and a real user event untouched; re-plan (patch + soft_cancel,
  cancelled not deleted); WhatsApp parity approving with `yes` (regression for corrected dispatch);
  concurrency (edit a Sage block mid-flight → op skipped + reported); provenance safety (personal
  event never patched/cancelled — conflict reported instead).

## Explicit future work (not in this iteration)
- **Apple Reminders as a plan source** — no public cloud API, and Sage runs on Cloud Run (no
  Mac / no EventKit server-side). The two viable paths are (a) **CalDAV against iCloud** with an
  Apple app-specific password (reads VTODOs server-side, but stores iCloud creds and is
  unofficial/fragile) or (b) a **local Mac bridge** (EventKit helper the user runs that POSTs
  reminders to Sage). Path undecided; both slot in as an additional read source feeding the same
  merge/dedup step in the PlannerAgent — no change to the diff/HITL machinery.
- **Nightly automated per-user check-in** — the current APScheduler is single-user and
  WhatsApp-only; a per-user, timezone-aware nightly fan-out (Cloud Scheduler → authenticated
  endpoint) is the correct approach and is deferred.
- **Write-back to task sources** — marking a Google Task / Sage todo complete when its time-block
  is done (this iteration reads tasks only).
- Post-approval it applies immediately; no separate "morning briefing reads back the plan" step yet.

---
*On approval, a copy of this plan will be saved to `docs/tasks/`.*
