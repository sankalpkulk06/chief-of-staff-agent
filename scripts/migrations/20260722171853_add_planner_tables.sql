-- Daily calendar planner: provenance mirror for Sage-created events + session-keyed pending prompts.

-- Local mirror of the events Sage writes to Google Calendar. Google remains the
-- source of truth for content; this stores the block_id <-> google_event_id map,
-- the last-seen etag (optimistic concurrency), and soft-delete status.
CREATE TABLE IF NOT EXISTS sage_calendar_events (
    id              TEXT PRIMARY KEY,                 -- sage_block_id (also written into the event's extendedProperties)
    user_id         TEXT NOT NULL,
    google_event_id TEXT,                             -- null until the event is created in Google
    calendar_id     TEXT NOT NULL DEFAULT 'primary',
    plan_date       DATE NOT NULL,
    title           TEXT NOT NULL,
    start_local     TIMESTAMPTZ,
    end_local       TIMESTAMPTZ,
    source_kind     TEXT,                             -- todo | habit | conversation | existing | google_task
    source_ref      TEXT,
    etag            TEXT,
    status          TEXT NOT NULL DEFAULT 'active',   -- active | cancelled (soft delete)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancelled_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sage_calendar_events_lookup
    ON sage_calendar_events (user_id, plan_date, status);

-- Session-keyed multi-turn prompt state (generalizes the phone-keyed nudge_context).
-- One active pending prompt per session; state_json accumulates the user's answers.
CREATE TABLE IF NOT EXISTS pending_prompts (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    prompt_type TEXT NOT NULL,                        -- e.g. 'plan_checkin'
    state_json  JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 minutes'
);
