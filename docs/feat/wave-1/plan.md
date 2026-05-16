# Feature Build Plan — Web Search · WhatsApp · Habit Tracker

**Status:** In Progress  
**Branch:** `feat/whatsapp-integration`

---

## Summary

Five phases that evolve Sage from a local CLI chatbot into a proactive personal agent accessible from your phone.

| Phase | Feature | Est. Effort | Status |
|-------|---------|-------------|--------|
| 1 | [Web Search](phase1-web-search.md) | 3–4 days | Completed |
| 2 | [WhatsApp Text](phase2-whatsapp-text.md) | 4–5 days | Completed |
| 3 | [Habit Tracker CLI](phase3-habit-tracker.md) | 3–4 days | Not started |
| 4 | [Scheduler & Proactive Reminders](phase4-scheduler-reminders.md) | 4–5 days | Not started |
| 5 | [Voice Notes (Whisper)](phase5-voice-notes.md) | 2–3 days | Not started |

**Total estimated:** 3–4 weeks part-time

---

## Architecture at a Glance

```
app/
  core/
    web_search_service.py       ← Phase 1
    habit_service.py            ← Phase 3
  webhook/
    __init__.py                 ← Phase 2
    server.py                   ← Phase 2
  services/
    whatsapp_service.py         ← Phase 2
    whisper_service.py          ← Phase 5
  scheduler/
    __init__.py                 ← Phase 4
    scheduler.py                ← Phase 4
  cli/
    commands_serve.py           ← Phase 2 (new `sage serve` command)
    commands_habit.py           ← Phase 3 (new `/habit` commands)
  storage/
    sql_schema.sql              ← Phase 2 + 3 (schema additions)
```

---

## Dependency Graph

```
Phase 1 — Web Search          (no deps — start anytime)
Phase 2 — WhatsApp Text       (needs: existing ChatService, SQLiteRegistry)
Phase 3 — Habit Tracker CLI   (needs: SQLiteRegistry schema extension)
Phase 4 — Scheduler/Reminders (needs: Phase 2 + Phase 3)
Phase 5 — Voice Notes         (needs: Phase 2)
```

Phases 1, 2, and 3 can be built in parallel. Phase 4 gates on 2+3. Phase 5 gates on 2.

---

## Key Design Notes

- The PRD uses `sage/` as the package name; the actual package is `app/`. All file paths follow `app/`.
- `SQLiteRegistry` loads schema from `app/storage/sql_schema.sql` via `initialize_schema()`. New tables go in that file.
- `ChatService.__init__` accepts optional service dependencies — new services (HabitService, WebSearchService) follow the same injection pattern.
- The `ToolRegistry` + `ToolExecutor` in `app/core/tools.py` and `app/core/tool_executor.py` is the existing tool system. `web_search` registers as a new tool there.
- `sage serve` needs a new Typer command in `app/cli/` wired into `app/main.py`.
