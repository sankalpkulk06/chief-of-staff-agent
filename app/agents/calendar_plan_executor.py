"""CalendarPlanExecutor — applies an approved calendar-plan HITL batch.

Runs the {create, patch, soft_cancel} operations staged by PlannerService against
Google Calendar. Safety properties:
  - etag / If-Match precondition on every mutation → a block the user changed in
    Google since the proposal is skipped (412), never clobbered.
  - CalendarService re-asserts the sage_managed provenance tag before any mutation.
  - create ops are re-checked against a *fresh* fixed-event pull (a meeting may have
    been booked in the interim).
  - partial-batch tolerant: one failed op never aborts the rest; the local mirror is
    updated per successful op so a later /plan reconciles cleanly.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.agents.base import AgentResult
from app.core import timezone_util as tzu
from app.core.plan_diff import minutes_to_hhmm
from app.services.calendar_service import (
    CalendarConflictError,
    CalendarEventGoneError,
    CalendarNotConnectedError,
    CalendarPermissionError,
)

log = logging.getLogger(__name__)

CALENDAR_ACCOUNT_TYPE = "google_calendar"


class CalendarPlanExecutor:
    def __init__(self, calendar_service: Any, registry: Any) -> None:
        self._calendar = calendar_service
        self._registry = registry

    def apply(self, hitl_id: str, user_id: str) -> AgentResult:
        row = self._registry.get_hitl_request(hitl_id)
        if not row:
            return self._fail("hitl_not_found", "That plan request no longer exists.")
        payload = row.get("action_payload") or {}
        operations: List[Dict[str, Any]] = payload.get("operations") or []
        plan_date_str: str = payload.get("plan_date")
        tz_name: str = payload.get("timezone") or "UTC"
        calendar_id: str = payload.get("calendar_id") or "primary"

        token = self._registry.get_email_token(user_id, CALENDAR_ACCOUNT_TYPE)
        if token is None:
            return self._fail(
                "not_connected",
                "Your Google Calendar isn't connected anymore. Reconnect it in Settings and re-run `/plan`.",
            )

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        plan_date = date.fromisoformat(plan_date_str)

        applied: List[str] = []
        skipped: List[str] = []
        failed: List[str] = []

        # Fresh obstacle set for re-validating create ops against interim bookings.
        busy = self._fresh_busy_minutes(user_id, plan_date, tz, token, calendar_id)

        for op in operations:
            action = op.get("action")
            try:
                if action == "create":
                    self._do_create(user_id, op, plan_date, tz, calendar_id, token, busy, applied, skipped)
                elif action == "patch":
                    self._do_patch(user_id, op, plan_date, tz, calendar_id, token, applied, skipped)
                elif action == "soft_cancel":
                    self._do_soft_cancel(user_id, op, token, calendar_id, applied, skipped)
            except CalendarConflictError:
                skipped.append(f"{op.get('title', 'event')} (you changed it in Google — left as-is)")
            except CalendarEventGoneError:
                # Already gone in Google → reconcile the local mirror and move on.
                if op.get("block_id"):
                    self._safe(lambda: self._registry.soft_cancel_calendar_event(op["block_id"]))
                skipped.append(f"{op.get('title', 'event')} (already removed in Google)")
            except CalendarPermissionError:
                skipped.append(f"{op.get('title', 'event')} (not a Sage event — skipped)")
            except CalendarNotConnectedError:
                return self._fail("not_connected", "Calendar disconnected mid-apply. Reconnect and try again.")
            except Exception as exc:
                log.warning("CalendarPlanExecutor: op %s failed: %s", action, exc)
                failed.append(f"{op.get('title', 'event')} ({exc})")

        return AgentResult(
            agent="calendar_plan_executor",
            task="apply_calendar_plan",
            output=self._summary(applied, skipped, failed),
            success=not failed,
        )

    # ------------------------------------------------------------------
    # Per-op handlers
    # ------------------------------------------------------------------

    def _do_create(self, user_id, op, plan_date, tz, calendar_id, token, busy, applied, skipped):
        start_min, end_min = op["start_min"], op["end_min"]
        # Re-validate against interim bookings.
        for (b_start, b_end) in busy:
            if start_min < b_end and b_start < end_min:
                skipped.append(f"{op.get('title', 'event')} (a calendar event now occupies that slot)")
                return
        start_rfc = tzu.local_datetime(plan_date, minutes_to_hhmm(start_min), tz).isoformat()
        end_rfc = tzu.local_datetime(plan_date, minutes_to_hhmm(end_min), tz).isoformat()
        block_id = op["block_id"]
        created, refreshed = self._calendar.insert_event(
            token, block_id=block_id, title=op["title"],
            start_rfc3339=start_rfc, end_rfc3339=end_rfc, tz=str(tz), calendar_id=calendar_id,
        )
        self._persist_refreshed(user_id, refreshed)
        self._registry.insert_calendar_event({
            "id": block_id, "user_id": user_id, "google_event_id": created.id,
            "calendar_id": calendar_id, "plan_date": plan_date.isoformat(), "title": op["title"],
            "start_local": start_rfc, "end_local": end_rfc,
            "source_kind": op.get("source_kind"), "source_ref": op.get("source_ref"),
            "etag": created.etag, "status": "active",
        })
        busy.append((start_min, end_min))
        applied.append(f"➕ {minutes_to_hhmm(start_min)}–{minutes_to_hhmm(end_min)} {op['title']}")

    def _do_patch(self, user_id, op, plan_date, tz, calendar_id, token, applied, skipped):
        start_min, end_min = op["start_min"], op["end_min"]
        start_rfc = tzu.local_datetime(plan_date, minutes_to_hhmm(start_min), tz).isoformat()
        end_rfc = tzu.local_datetime(plan_date, minutes_to_hhmm(end_min), tz).isoformat()
        updated, refreshed = self._calendar.patch_event(
            token, google_event_id=op["google_event_id"], etag=op.get("etag"),
            fields={"title": op["title"], "start_rfc3339": start_rfc, "end_rfc3339": end_rfc},
            tz=str(tz), calendar_id=calendar_id,
        )
        self._persist_refreshed(user_id, refreshed)
        if op.get("block_id"):
            self._registry.update_calendar_event(
                op["block_id"], title=op["title"],
                start_local=start_rfc, end_local=end_rfc, etag=updated.etag,
            )
        applied.append(f"✏️ {minutes_to_hhmm(start_min)}–{minutes_to_hhmm(end_min)} {op['title']}")

    def _do_soft_cancel(self, user_id, op, token, calendar_id, applied, skipped):
        _, refreshed = self._calendar.soft_cancel_event(
            token, google_event_id=op["google_event_id"], etag=op.get("etag"), calendar_id=calendar_id,
        )
        self._persist_refreshed(user_id, refreshed)
        if op.get("block_id"):
            self._registry.soft_cancel_calendar_event(op["block_id"])
        note = ""
        # Best-effort write-back: if this block came from a Google Task, mark the task
        # complete so it stops re-surfacing as an open task. Never fails the removal.
        if op.get("source_kind") == "google_task" and op.get("source_ref"):
            if self._complete_task(user_id, token, op["source_ref"]):
                note = " · task marked done"
        applied.append(f"➖ {op.get('title', 'event')} (removed{note})")

    def _complete_task(self, user_id, token, source_ref) -> bool:
        try:
            tasklist_id, sep, task_id = str(source_ref).partition("::")
            if not sep:  # legacy ref stored just the task id
                tasklist_id, task_id = "@default", tasklist_id
            if not task_id:
                return False
            _, refreshed = self._calendar.complete_task(
                token, tasklist_id=tasklist_id or "@default", task_id=task_id
            )
            self._persist_refreshed(user_id, refreshed)
            return True
        except Exception as exc:
            log.warning("CalendarPlanExecutor: task completion failed (non-fatal): %s", exc)
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fresh_busy_minutes(self, user_id, plan_date, tz, token, calendar_id):
        """Fixed (user-owned, timed) events on the day, as [start_min,end_min) tuples."""
        try:
            time_min, time_max = tzu.day_bounds_rfc3339(plan_date, tz)
            events, refreshed = self._calendar.list_events(
                token, time_min_rfc3339=time_min, time_max_rfc3339=time_max, calendar_id=calendar_id
            )
            self._persist_refreshed(user_id, refreshed)
        except Exception as exc:
            log.warning("CalendarPlanExecutor: fresh busy pull failed: %s", exc)
            return []
        day_start = tzu.local_datetime(plan_date, "00:00", tz)
        out = []
        for ev in events:
            if ev.all_day or ev.sage_managed or ev.status == "cancelled" or not ev.start or not ev.end:
                continue
            try:
                s = tzu.parse_rfc3339(str(ev.start)).astimezone(tz)
                e = tzu.parse_rfc3339(str(ev.end)).astimezone(tz)
            except (ValueError, TypeError):
                continue
            s_min = max(0, int((s - day_start).total_seconds() // 60))
            e_min = min(1440, int((e - day_start).total_seconds() // 60))
            if e_min > s_min:
                out.append((s_min, e_min))
        return out

    def _persist_refreshed(self, user_id: str, refreshed: Optional[Dict]) -> None:
        if refreshed:
            self._safe(lambda: self._registry.upsert_email_token(user_id, refreshed, CALENDAR_ACCOUNT_TYPE))

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception as exc:
            log.warning("CalendarPlanExecutor: non-fatal registry op failed: %s", exc)

    @staticmethod
    def _summary(applied: List[str], skipped: List[str], failed: List[str]) -> str:
        lines: List[str] = []
        if applied:
            lines.append("Updated your calendar:")
            lines.extend(applied)
        else:
            lines.append("No changes were applied.")
        if skipped:
            lines.append("")
            lines.append("Skipped:")
            lines.extend(f"- {s}" for s in skipped)
        if failed:
            lines.append("")
            lines.append("Failed:")
            lines.extend(f"- {f}" for f in failed)
        return "\n".join(lines)

    @staticmethod
    def _fail(error: str, message: str) -> AgentResult:
        return AgentResult(
            agent="calendar_plan_executor", task="apply_calendar_plan",
            output=message, success=False, error=error,
        )
