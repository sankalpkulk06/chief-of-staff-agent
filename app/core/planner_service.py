"""PlannerService — builds a time-blocked daily plan and stages it behind the HITL gate.

Surface-agnostic: used by ChatService for both web and WhatsApp. The LLM only
*proposes* blocks; overlap validation and the create/patch/soft-cancel decision are
done by the pure-Python engine in app/core/plan_diff.py.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.core import timezone_util as tzu
from app.core.habit_service import HabitService
from app.core.plan_diff import (
    CurrentBlock,
    FixedBlock,
    Op,
    ProposedBlock,
    build_plan_diff,
    hhmm_to_minutes,
    minutes_to_hhmm,
)

log = logging.getLogger(__name__)

CALENDAR_ACCOUNT_TYPE = "google_calendar"
PLAN_HITL_ACTION = "apply_calendar_plan"


@dataclass
class CheckinStart:
    connected: bool
    plan_date: date
    message: str


@dataclass
class PlanResult:
    message: str
    hitl_id: Optional[str] = None
    hitl_pending: bool = False


class PlannerService:
    def __init__(
        self,
        registry: Any,
        calendar_service: Any,
        chat_provider: Any,
        default_timezone: str = "UTC",
        assistant_name: str = "Sage",
    ) -> None:
        self._registry = registry
        self._calendar = calendar_service
        self._provider = chat_provider
        self._default_tz = default_timezone
        self._assistant_name = assistant_name

    @property
    def available(self) -> bool:
        return self._calendar is not None

    # ------------------------------------------------------------------
    # Step 1: /plan bootstrap — gather context and ask the check-in question
    # ------------------------------------------------------------------

    def start_checkin(self, user_id: str, plan_date_arg: str) -> CheckinStart:
        tz = self._resolve_tz(user_id)
        plan_date = self._resolve_plan_date(plan_date_arg, tz)

        token = self._token(user_id)
        if token is None:
            return CheckinStart(
                connected=False,
                plan_date=plan_date,
                message=(
                    "Your Google Calendar isn't connected yet. "
                    "Go to **Settings → Connect Google Calendar** to link it, then run `/plan` again."
                ),
            )

        try:
            fixed = self._fixed_events(user_id, plan_date, tz, token)
        except Exception as exc:
            log.warning("PlannerService.start_checkin calendar read failed: %s", exc)
            return CheckinStart(
                connected=True, plan_date=plan_date, message=self._calendar_error_message(exc)
            )
        try:
            todos = self._registry.list_todos(user_id)
        except Exception:
            todos = []
        tasks = self._google_tasks(user_id, token)
        habits = self._habit_names(user_id)

        label = self._date_label(plan_date, tz)
        bits: List[str] = []
        if fixed:
            bits.append(f"{len(fixed)} calendar event(s)")
        open_task_count = len(todos) + len(tasks)
        if open_task_count:
            bits.append(f"{open_task_count} open task(s)")
        if habits:
            bits.append(f"{len(habits)} habit(s)")
        context = ", ".join(bits) if bits else "nothing on the books yet"

        message = (
            f"Planning **{label}**. You have {context}.\n\n"
            "What else do you have planned, and any fixed times I should work around? "
            "(e.g. \"gym 8-9:30, deep work in the morning, dentist at 3\")"
        )
        return CheckinStart(connected=True, plan_date=plan_date, message=message)

    # ------------------------------------------------------------------
    # Step 2: build the plan from gathered notes → HITL batch
    # ------------------------------------------------------------------

    def build_plan(
        self, user_id: str, session_id: str, plan_date: date, notes: List[str]
    ) -> PlanResult:
        tz = self._resolve_tz(user_id)
        token = self._token(user_id)
        if token is None:
            return PlanResult(
                message="Your Google Calendar isn't connected. Connect it in Settings and try `/plan` again."
            )

        plan_date_str = plan_date.isoformat()
        try:
            fixed_events = self._fixed_events(user_id, plan_date, tz, token)
        except Exception as exc:
            log.warning("PlannerService.build_plan calendar read failed: %s", exc)
            return PlanResult(message=self._calendar_error_message(exc))
        todos = self._registry.list_todos(user_id)
        tasks = self._google_tasks(user_id, token)
        habits = self._habit_names(user_id)
        current = self._current_blocks(user_id, plan_date_str, tz)

        candidates = self._merge_candidates(todos, tasks)

        proposed_raw = self._propose_blocks(
            plan_date=plan_date, tz=tz, fixed=fixed_events,
            candidates=candidates, habits=habits, current=current, notes=notes,
        )
        if proposed_raw is None:
            return PlanResult(
                message="I couldn't put a plan together just now — the planner model didn't return a usable schedule. Try `/plan` again."
            )

        proposed = self._to_proposed_blocks(proposed_raw, candidates)
        fixed_blocks = [FixedBlock(e["title"], e["start_min"], e["end_min"]) for e in fixed_events]
        diff = build_plan_diff(proposed, fixed_blocks, current)

        if not diff.operations:
            body = "Your plan for that day already matches — nothing to change."
            if diff.conflicts:
                body += "\n\n" + "\n".join(f"- {c}" for c in diff.conflicts)
            return PlanResult(message=body)

        payload = self._operations_payload(diff.operations, plan_date_str, tz, "primary", diff.summary)
        hitl_id = str(uuid.uuid4())
        self._registry.create_hitl_request(
            id=hitl_id, user_id=user_id,
            action_type=PLAN_HITL_ACTION, action_payload=payload, session_id=session_id,
        )
        message = self._render_proposal(diff, plan_date, tz)
        return PlanResult(message=message, hitl_id=hitl_id, hitl_pending=True)

    # ------------------------------------------------------------------
    # Input gathering
    # ------------------------------------------------------------------

    def _token(self, user_id: str) -> Optional[Dict]:
        return self._registry.get_email_token(user_id, CALENDAR_ACCOUNT_TYPE)

    def _resolve_tz(self, user_id: str) -> ZoneInfo:
        # 1. Honor an explicitly stored user timezone.
        stored = tzu.get_user_timezone_name(self._registry, user_id, default="")
        if stored and tzu.is_valid_timezone(stored):
            return ZoneInfo(stored)
        # 2. Auto-detect from the connected Google Calendar and persist it, so
        #    dates and event times are computed in the user's real timezone (not UTC).
        token = self._token(user_id)
        if token is not None:
            try:
                tzname, refreshed = self._calendar.get_calendar_timezone(token)
                self._persist_refreshed(user_id, refreshed)
                if tzname and tzu.is_valid_timezone(tzname):
                    try:
                        tzu.set_user_timezone(self._registry, user_id, tzname)
                    except Exception:
                        pass
                    return ZoneInfo(tzname)
            except Exception as exc:
                log.warning("PlannerService: timezone auto-detect failed: %s", exc)
        # 3. Fall back to the configured default.
        return ZoneInfo(self._default_tz)

    def _resolve_plan_date(self, arg: str, tz: ZoneInfo) -> date:
        today = tzu.now_local(tz).date()
        a = (arg or "").strip().lower()
        if a in ("", "tomorrow"):
            return today + timedelta(days=1)
        if a == "today":
            return today
        try:
            return date.fromisoformat(a)
        except ValueError:
            return today + timedelta(days=1)

    _MONTHS = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
        "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
        "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    }

    def resolve_plan_date_from_text(self, text: str, user_id: str) -> date:
        """Best-effort date extraction from a natural-language request (defaults to tomorrow)."""
        tz = self._resolve_tz(user_id)
        t = (text or "").lower()
        today = tzu.now_local(tz).date()

        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
        if "day after tomorrow" in t:
            return today + timedelta(days=2)
        if "today" in t or "tonight" in t:
            return today
        if "tomorrow" in t:
            return today + timedelta(days=1)

        months = "|".join(self._MONTHS.keys())
        m = (re.search(rf"({months})\.?\s+(\d{{1,2}})", t)
             or re.search(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({months})", t))
        if m:
            g1, g2 = m.group(1), m.group(2)
            mon_str = g1 if g1[0].isalpha() else g2
            day_str = g2 if g1[0].isalpha() else g1
            month_num = self._MONTHS.get(mon_str)
            if month_num:
                try:
                    d = date(today.year, month_num, int(day_str))
                    if d < today:
                        d = date(today.year + 1, month_num, int(day_str))
                    return d
                except ValueError:
                    pass
        return today + timedelta(days=1)  # default: tomorrow

    def _fixed_events(self, user_id: str, plan_date: date, tz: ZoneInfo, token: Dict) -> List[Dict[str, Any]]:
        """User-owned timed events for the day → immovable obstacles. Excludes all-day + Sage events."""
        time_min, time_max = tzu.day_bounds_rfc3339(plan_date, tz)
        events, refreshed = self._calendar.list_events(
            token, time_min_rfc3339=time_min, time_max_rfc3339=time_max
        )
        self._persist_refreshed(user_id, refreshed)
        out: List[Dict[str, Any]] = []
        for ev in events:
            if ev.all_day or ev.sage_managed or ev.status == "cancelled":
                continue
            bounds = self._to_minutes(ev.start, ev.end, plan_date, tz)
            if bounds is None:
                continue
            out.append({"title": ev.title or "(busy)", "start_min": bounds[0], "end_min": bounds[1]})
        return out

    def _current_blocks(self, user_id: str, plan_date_str: str, tz: ZoneInfo) -> List[CurrentBlock]:
        rows = self._registry.list_managed_calendar_events(user_id, plan_date_str, "active")
        plan_date = date.fromisoformat(plan_date_str)
        out: List[CurrentBlock] = []
        for r in rows:
            bounds = self._to_minutes(r.get("start_local"), r.get("end_local"), plan_date, tz)
            if bounds is None:
                continue
            out.append(CurrentBlock(
                block_id=r["id"], title=r.get("title", ""),
                start_min=bounds[0], end_min=bounds[1],
                google_event_id=r.get("google_event_id"), etag=r.get("etag"),
                source_kind=r.get("source_kind") or "conversation", source_ref=r.get("source_ref"),
            ))
        return out

    def _google_tasks(self, user_id: str, token: Dict) -> List[Dict[str, Any]]:
        try:
            tasks, refreshed = self._calendar.list_open_tasks(token)
        except Exception as exc:
            log.warning("PlannerService: Google Tasks unavailable: %s", exc)
            return []
        self._persist_refreshed(user_id, refreshed)
        return [
            {"title": t.title, "due": t.due, "id": t.id, "tasklist_id": t.tasklist_id}
            for t in tasks
        ]

    def _habit_names(self, user_id: str) -> List[str]:
        try:
            habit_svc = HabitService(self._registry, user_id=user_id)
            return [h.name for h in habit_svc._get_all_active()]
        except Exception:
            return []

    def _merge_candidates(
        self, todos: List[Dict[str, Any]], tasks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Unify Sage todos + Google Tasks; dedup by normalized title (prefer the Sage todo)."""
        seen: set[str] = set()
        candidates: List[Dict[str, Any]] = []
        for t in todos:
            title = (t.get("title") or "").strip()
            key = " ".join(title.lower().split())
            if not title or key in seen:
                continue
            seen.add(key)
            candidates.append({
                "title": title, "source_kind": "todo",
                "source_ref": str(t.get("id")), "due": t.get("due_at"),
            })
        for t in tasks:
            title = (t.get("title") or "").strip()
            key = " ".join(title.lower().split())
            if not title or key in seen:
                continue
            seen.add(key)
            # Composite ref carries the tasklist so the block can later be linked back
            # to the exact Google Task (needed to mark it complete on removal).
            source_ref = f"{t.get('tasklist_id', '')}::{t.get('id')}"
            candidates.append({
                "title": title, "source_kind": "google_task",
                "source_ref": source_ref, "due": t.get("due"),
            })
        # Prioritize: due-dated first (earliest), then undated.
        candidates.sort(key=lambda c: (c["due"] is None, str(c["due"]) if c["due"] else ""))
        return candidates

    # ------------------------------------------------------------------
    # LLM proposal
    # ------------------------------------------------------------------

    def _propose_blocks(
        self, *, plan_date: date, tz: ZoneInfo, fixed: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]], habits: List[str],
        current: List[CurrentBlock], notes: List[str],
    ) -> Optional[List[Dict[str, Any]]]:
        fixed_lines = "\n".join(
            f"- {e['title']}: {minutes_to_hhmm(e['start_min'])}–{minutes_to_hhmm(e['end_min'])} (FIXED, do not move)"
            for e in fixed
        ) or "- (none)"
        cand_lines = "\n".join(
            f"- [{c['source_kind']}:{c['source_ref']}] {c['title']}"
            + (f" (due {c['due']})" if c.get("due") else "")
            for c in candidates
        ) or "- (none)"
        habit_lines = "\n".join(f"- {h} (habit)" for h in habits) or "- (none)"
        current_lines = "\n".join(
            f"- {c.title}: {minutes_to_hhmm(c.start_min)}–{minutes_to_hhmm(c.end_min)}"
            for c in current
        ) or "- (none)"
        notes_text = "\n".join(f"- {n}" for n in notes if n.strip()) or "- (none)"

        prompt = f"""You are {self._assistant_name}, planning a time-blocked schedule for {plan_date.isoformat()}.

FIXED calendar events (immovable — never overlap these):
{fixed_lines}

The user's current Sage-planned blocks for this day (you may keep, move, or drop these):
{current_lines}

Open tasks that could be scheduled (prioritize by due date; you do NOT have to schedule all of them — only what realistically fits the free time):
{cand_lines}

Daily habits to fit in if there's room:
{habit_lines}

What the user just told you about this day:
{notes_text}

Produce a realistic, non-overlapping schedule. Rules:
- The schedule you output REPLACES the current Sage-planned blocks. To KEEP an
  existing block, re-emit it (same title) at its time. To MOVE it, emit it at the
  new time. To REMOVE/DROP it, simply OMIT it from your output.
- If the user explicitly asks to remove, drop, cancel, or delete something, you MUST
  NOT include it — even if it also appears in the open-tasks list. Their instruction wins.
- Only make the changes the user asked for; leave every other existing block unchanged.
- Times are 24h local "HH:MM". Every block needs start < end.
- NEVER overlap a FIXED event or another block you create.
- Leave gaps; do not pack the whole day. Prefer mornings for deep work.
- Use source_kind "todo" or "google_task" (with its source_ref) for task blocks,
  "habit" for habits, "conversation" for anything the user mentioned.

Output ONLY a JSON array, no prose:
[{{"title": "Gym", "start": "08:00", "end": "09:30", "source_kind": "habit", "source_ref": null}}]
"""
        try:
            raw = self._provider.generate(prompt)
        except Exception as exc:
            log.warning("PlannerService: LLM generate failed: %s", exc)
            return None
        return self._parse_blocks(raw)

    @staticmethod
    def _parse_blocks(raw: str) -> Optional[List[Dict[str, Any]]]:
        text = raw.strip()
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, list) else None

    def _to_proposed_blocks(
        self, raw_blocks: List[Dict[str, Any]], candidates: List[Dict[str, Any]]
    ) -> List[ProposedBlock]:
        # Authoritative title → (source_kind, source_ref) map from the candidate list, so
        # task linkage is reliable even when the LLM omits or garbles source_ref. This is
        # what lets a removed task-block be tied back to the exact Google Task.
        cand_by_title = {
            self._norm(c["title"]): (c["source_kind"], c["source_ref"]) for c in candidates
        }
        out: List[ProposedBlock] = []
        for b in raw_blocks:
            if not isinstance(b, dict):
                continue
            try:
                start_min = hhmm_to_minutes(str(b["start"]))
                end_min = hhmm_to_minutes(str(b["end"]))
            except (KeyError, ValueError, TypeError):
                continue
            title = str(b.get("title") or "Untitled").strip()
            match = cand_by_title.get(self._norm(title))
            if match is not None:
                kind, ref = match
            else:
                kind = str(b.get("source_kind") or "conversation")
                ref = b.get("source_ref")
                ref = str(ref) if ref else None
            out.append(ProposedBlock(
                title=title, start_min=start_min, end_min=end_min,
                source_kind=kind, source_ref=ref,
            ))
        return out

    @staticmethod
    def _norm(title: str) -> str:
        return " ".join((title or "").strip().lower().split())

    # ------------------------------------------------------------------
    # Serialization + rendering
    # ------------------------------------------------------------------

    def _operations_payload(
        self, operations: List[Op], plan_date_str: str, tz: ZoneInfo,
        calendar_id: str, summary: str,
    ) -> Dict[str, Any]:
        ops: List[Dict[str, Any]] = []
        for op in operations:
            entry: Dict[str, Any] = {
                "action": op.action, "title": op.title,
                "source_kind": op.source_kind, "source_ref": op.source_ref,
                "block_id": op.block_id or str(uuid.uuid4()),
                "google_event_id": op.google_event_id, "etag": op.etag,
            }
            if op.start_min is not None:
                entry["start_min"] = op.start_min
            if op.end_min is not None:
                entry["end_min"] = op.end_min
            ops.append(entry)
        return {
            "operations": ops, "plan_date": plan_date_str,
            "timezone": str(tz), "calendar_id": calendar_id, "summary": summary,
        }

    def _render_proposal(self, diff, plan_date: date, tz: ZoneInfo) -> str:
        label = self._date_label(plan_date, tz)
        lines = [f"Here's my proposed plan for **{label}** ({diff.summary}):", ""]
        for op in sorted(diff.operations, key=lambda o: (o.start_min is None, o.start_min or 0)):
            if op.action == "create":
                lines.append(f"➕ {minutes_to_hhmm(op.start_min)}–{minutes_to_hhmm(op.end_min)}  {op.title}")
            elif op.action == "patch":
                lines.append(f"✏️ {minutes_to_hhmm(op.start_min)}–{minutes_to_hhmm(op.end_min)}  {op.title} (moved)")
            elif op.action == "soft_cancel":
                lines.append(f"➖ {op.title} (removing)")
        if diff.conflicts:
            lines.append("")
            lines.append("Couldn't fit:")
            lines.extend(f"- {c}" for c in diff.conflicts)
        lines.append("")
        lines.append("Approve to write these to your calendar?")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    def _to_minutes(self, start, end, plan_date: date, tz: ZoneInfo):
        """Parse RFC3339/date strings → clamped minute-of-day interval on plan_date. None if unparseable/all-day-only."""
        if not start or not end:
            return None
        try:
            start_dt = tzu.parse_rfc3339(str(start))
            end_dt = tzu.parse_rfc3339(str(end))
        except (ValueError, TypeError):
            return None
        # date-only (all-day) values parse to midnight naive — skip as obstacles
        if len(str(start)) == 10:  # 'YYYY-MM-DD'
            return None
        if start_dt.tzinfo is not None:
            start_dt = start_dt.astimezone(tz)
        if end_dt.tzinfo is not None:
            end_dt = end_dt.astimezone(tz)
        day_start = tzu.local_datetime(plan_date, "00:00", tz)
        start_min = max(0, int((start_dt - day_start).total_seconds() // 60))
        end_min = min(1440, int((end_dt - day_start).total_seconds() // 60))
        if end_min <= start_min:
            return None
        return (min(start_min, 1440), end_min)

    def _date_label(self, plan_date: date, tz: ZoneInfo) -> str:
        today = tzu.now_local(tz).date()
        if plan_date == today:
            rel = "today"
        elif plan_date == today + timedelta(days=1):
            rel = "tomorrow"
        else:
            rel = plan_date.strftime("%A")
        return f"{rel}, {plan_date.strftime('%b %-d')}"

    @staticmethod
    def _calendar_error_message(exc: Exception) -> str:
        """Turn a Google API failure into an actionable message instead of a silent empty day."""
        text = str(exc)
        low = text.lower()
        if "accessnotconfigured" in low or "has not been used in project" in low or "is disabled" in low:
            return (
                "I connected to your Google account, but the **Google Calendar API** (and/or "
                "**Google Tasks API**) isn't enabled for this app yet. Enable them in Google Cloud "
                "Console → *APIs & Services → Enable APIs*, wait a minute, then run `/plan` again."
            )
        if "insufficient" in low or "insufficientpermissions" in low or "403" in low:
            return (
                "I couldn't read your calendar — the connection may be missing the calendar "
                "permission. Disconnect and reconnect Google Calendar in Settings, then try `/plan` again."
            )
        if "invalid_grant" in low or "token has been expired or revoked" in low:
            return (
                "Your Google Calendar connection has expired. Reconnect it in Settings, then run `/plan` again."
            )
        return (
            "I couldn't read your Google Calendar just now. Please try `/plan` again in a moment. "
            f"(details: {text[:180]})"
        )

    def _persist_refreshed(self, user_id: str, refreshed: Optional[Dict]) -> None:
        if refreshed:
            try:
                self._registry.upsert_email_token(user_id, refreshed, CALENDAR_ACCOUNT_TYPE)
            except Exception as exc:
                log.warning("PlannerService: failed to persist refreshed token: %s", exc)
