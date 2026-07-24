# Sage — Roadmap & Status

_Last updated: 2026-07-24 · branch `feat/enhancements`_

A single view of what's shipped, what's intentionally excluded, and what's left. The detailed
CLI-vs-web gap table lives in [`cli-feature-parity.md`](./cli-feature-parity.md); this file is
the higher-level program status.

## Legend
- ✅ **Done** — built, tested, committed, pushed
- ⏸ **Won't do** — intentionally out of scope (doesn't fit the CLI / not a CLI action)
- 🔜 **To be done** — follow-ups and infra; not blocking
- 🧭 **Future / optional** — nice-to-have enhancements, not scheduled

---

## ✅ Done

### CLI-native roadmap — 100% of actionable gaps complete
| # | Feature | Surface |
|---|---------|---------|
| 1 | CLI identity / auth gate | `sage login` · `logout` · `whoami` (persisted session; auto local-user when auth disabled) |
| 2 | Gmail OAuth connect | `sage email connect [--work]` · `status` · `disconnect` (localhost loopback OAuth) |
| 3 | Google Calendar/Tasks connect | `sage calendar connect` · `status` · `disconnect` (shared loopback helper) |
| 9 | HITL approvals in the CLI | `[y/n]` prompt in `sage chat` → shared `execute_approved_by_type` dispatch |
| 4 | Analytics | Dedicated web Analytics page (SVG charts) + `sage stats` + LLM insights digest |
| 6 | Session management | `sage sessions list` · `rename` · `delete` · `resume` (prefix ids, ownership-checked) |
| 5 | Profile view/delete | `sage profile show` · `delete` (guarded; shared `profile_service` with the web) |

### Bonus fixes & improvements shipped along the way
| Area | What changed |
|------|--------------|
| Email (CLI) | Fixed `TypeError` crash in `email-personal`/`email-work` + the in-chat email handlers (stale file-based API → per-user DB tokens) |
| Email (quality) | Fetch real email **bodies** + **instruction-aware** LLM summaries (was fixed ACTION/FYI triage only); orchestrator routes summarize/follow-ups to `email_agent` |
| Chat UX | Assistant replies render as **Markdown** in the terminal (no raw `**`/`*`) |
| Analytics | Unified two divergent analytics code paths into one `AnalyticsService` engine; real 7/30/90-day windows; new `agent_invocations` feature-usage logging |
| Profile | Extracted shared `profile_service` so web + CLI never drift |
| Sessions | `delete_session` now clears the `named_sessions` alias (orphan-safe) |
| Dev ergonomics | `.env` `DATABASE_URL` commented out → CLI + container default to local SQLite; `data/session.json` gitignored |
| Docs | `cli-feature-parity.md` tracker; `docs/tasks/cli-oauth-connect.md` spec; this roadmap |

## ⏸ Won't do (out of scope by design)
| # | Feature | Reason |
|---|---------|--------|
| 7 | Live trace streaming (SSE) | Web-native; the CLI already prints agent steps inline |
| 10 | Todo reminders (push) | Requires the scheduler + WhatsApp — not a CLI action |
| 11 | Habit nightly nudges (push) | Scheduled push, WhatsApp-only |
| 12 | Morning briefing + news (push) | Scheduled push, WhatsApp-only |
| 8 | Web document upload | Already covered by `sage ingest` — no real gap |

## 🔜 To be done (infra / follow-ups — not blocking)
| Item | Notes |
|------|-------|
| Apply `agent_invocations` migration to Supabase | SQL exists (`scripts/migrations/20260723090000_agent_invocations.sql`) and auto-applies on SQLite; run `mcp__supabase__apply_migration` once Supabase is reachable |
| Fix the Supabase project | Currently paused/unreachable ("tenant/user not found") — unpause or recreate, then re-point `DATABASE_URL` in `.env` |
| Rebuild the prod container | Session-delete + profile refactor + analytics are server-facing; rebuild so the running server matches the latest code (`docker compose up -d --build`) |
| Open the PR | `feat/enhancements` → `main` when ready |
| Pre-existing test flakes | 6 unrelated failing tests predate this work (HF embeddings, usage-count, webhook nudges) — worth fixing separately |

## 🧭 Future / optional (nice-to-have, not scheduled)
| Item | Notes |
|------|-------|
| Analytics: HITL approval-pattern charts | `hitl_requests` supports approve/reject rate, by action_type, time-to-decide |
| Analytics: calendar-planning charts | `sage_calendar_events`: planned time by source, planned vs cancelled |
| Analytics: security-events timeline | `security_events` over time |
| Analytics: conversational `analytics_agent` | Ask "how are my habits trending?" in chat |
| Per-user WhatsApp usage | Today the counter is global-per-day, not per-user |
| Legacy chat-command audit | Sweep remaining in-chat commands for stale patterns |

---

## Storage-backend note
The app runs on **local SQLite + ChromaDB** today (Supabase is paused). Everything above works
on SQLite. The Postgres/pgvector path stays in parity — new registry methods and the migration
were mirrored to `pg_registry.py` — and will light up when `DATABASE_URL` is restored.

## Commit trail (this workstream, on `feat/enhancements`)
`f357bda` P0 identity → `80610fc` P0 smoke test → `708c686` parity doc → `9dac49c` OAuth spec →
`41640b3` email/calendar connect → `a1b5eef` instruction-aware email → `317075e` richer email →
`3ecc4d1` HITL + Markdown → `d7fbb20` analytics engine → `ebd593d` analytics UI → `80c98ef`
sessions → `98daf3a` profile.
