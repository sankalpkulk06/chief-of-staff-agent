"""Pure-Python plan reconciliation — the heart of the daily planner.

The LLM only *proposes* time blocks. Every decision with real consequences —
whether two blocks overlap, and whether a proposed schedule means "create",
"patch", or "soft-cancel" a Google Calendar event — is made here, deterministically,
with zero LLM involvement. All time arithmetic is on integer minutes-of-day.

Two stages:
  A. validate_overlaps — drop proposed blocks that collide with a fixed (user-owned)
     event or with each other; report each drop as a human-readable conflict.
  B. reconcile — diff the validated desired blocks against the currently active
     Sage-managed events for the day, emitting create / patch / soft_cancel ops.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def hhmm_to_minutes(hhmm: str) -> int:
    """'08:30' -> 510. Raises ValueError on malformed input or out-of-range values."""
    parts = hhmm.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid HH:MM time: {hhmm!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range: {hhmm!r}")
    return hour * 60 + minute


def minutes_to_hhmm(total: int) -> str:
    """510 -> '08:30'."""
    return f"{total // 60:02d}:{total % 60:02d}"


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """True if [a_start, a_end) and [b_start, b_end) overlap. Touching (end == start) is allowed."""
    return a_start < b_end and b_start < a_end


@dataclass
class ProposedBlock:
    title: str
    start_min: int
    end_min: int
    source_kind: str = "conversation"
    source_ref: Optional[str] = None


@dataclass
class FixedBlock:
    """A user-owned timed calendar event — an immovable obstacle. All-day events are excluded upstream."""
    title: str
    start_min: int
    end_min: int


@dataclass
class CurrentBlock:
    """An active Sage-managed event already on the calendar for the day."""
    block_id: str
    title: str
    start_min: int
    end_min: int
    google_event_id: Optional[str] = None
    etag: Optional[str] = None
    source_kind: str = "conversation"
    source_ref: Optional[str] = None


@dataclass
class Op:
    action: str  # 'create' | 'patch' | 'soft_cancel'
    title: str
    start_min: Optional[int] = None
    end_min: Optional[int] = None
    source_kind: Optional[str] = None
    source_ref: Optional[str] = None
    block_id: Optional[str] = None          # existing Sage block id (patch / soft_cancel)
    google_event_id: Optional[str] = None
    etag: Optional[str] = None


@dataclass
class PlanDiff:
    operations: List[Op] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    summary: str = ""


def _identity_key(title: str, source_kind: Optional[str], source_ref: Optional[str]) -> str:
    """Stable identity for matching desired<->current, keyed on the normalized title.

    The LLM reliably reproduces a block's title but NOT its internal source_ref, so
    title is the only dependable identity signal. Time is a mutable *attribute*, never
    part of identity — a block that only moves is the same block (patch, not cancel+create).
    A title change reads as a different block (cancel + create), which still reaches the
    correct end state.
    """
    return f"title::{_normalize_title(title)}"


def _normalize_title(title: str) -> str:
    return " ".join(title.strip().lower().split())


def validate_overlaps(
    proposed: List[ProposedBlock], fixed: List[FixedBlock]
) -> Tuple[List[ProposedBlock], List[str]]:
    """Drop proposed blocks that collide with a fixed event or an earlier-kept proposed block.

    Returns (kept, conflicts). Deterministic: proposed blocks are processed in
    (start, end) order, so an earlier block wins a mutual collision.
    """
    conflicts: List[str] = []
    kept: List[ProposedBlock] = []

    ordered = sorted(proposed, key=lambda b: (b.start_min, b.end_min, b.title))
    for block in ordered:
        if block.end_min <= block.start_min:
            conflicts.append(
                f"Dropped '{block.title}' ({minutes_to_hhmm(block.start_min)}–"
                f"{minutes_to_hhmm(block.end_min)}): end is not after start."
            )
            continue

        clash = _first_fixed_clash(block, fixed)
        if clash is not None:
            conflicts.append(
                f"Couldn't schedule '{block.title}' ({minutes_to_hhmm(block.start_min)}–"
                f"{minutes_to_hhmm(block.end_min)}): overlaps your '{clash.title}' "
                f"({minutes_to_hhmm(clash.start_min)}–{minutes_to_hhmm(clash.end_min)})."
            )
            continue

        prior = _first_kept_clash(block, kept)
        if prior is not None:
            conflicts.append(
                f"Dropped '{block.title}' ({minutes_to_hhmm(block.start_min)}–"
                f"{minutes_to_hhmm(block.end_min)}): overlaps planned '{prior.title}' "
                f"({minutes_to_hhmm(prior.start_min)}–{minutes_to_hhmm(prior.end_min)})."
            )
            continue

        kept.append(block)

    return kept, conflicts


def _first_fixed_clash(block: ProposedBlock, fixed: List[FixedBlock]) -> Optional[FixedBlock]:
    for f in fixed:
        if _overlaps(block.start_min, block.end_min, f.start_min, f.end_min):
            return f
    return None


def _first_kept_clash(block: ProposedBlock, kept: List[ProposedBlock]) -> Optional[ProposedBlock]:
    for k in kept:
        if _overlaps(block.start_min, block.end_min, k.start_min, k.end_min):
            return k
    return None


def reconcile(desired: List[ProposedBlock], current: List[CurrentBlock]) -> List[Op]:
    """Diff validated desired blocks against active Sage-managed events → create/patch/soft_cancel ops."""
    current_by_key: Dict[str, CurrentBlock] = {}
    for c in current:
        current_by_key.setdefault(_identity_key(c.title, c.source_kind, c.source_ref), c)

    ops: List[Op] = []
    matched_keys: set[str] = set()

    for d in desired:
        key = _identity_key(d.title, d.source_kind, d.source_ref)
        match = current_by_key.get(key)
        if match is None or key in matched_keys:
            # No current counterpart (or key already consumed by an earlier desired block) → create.
            ops.append(Op(
                action="create", title=d.title,
                start_min=d.start_min, end_min=d.end_min,
                source_kind=d.source_kind, source_ref=d.source_ref,
            ))
            continue

        matched_keys.add(key)
        changed = (
            match.start_min != d.start_min
            or match.end_min != d.end_min
            or _normalize_title(match.title) != _normalize_title(d.title)
        )
        if changed:
            ops.append(Op(
                action="patch", title=d.title,
                start_min=d.start_min, end_min=d.end_min,
                source_kind=d.source_kind, source_ref=d.source_ref,
                block_id=match.block_id, google_event_id=match.google_event_id, etag=match.etag,
            ))
        # else: unchanged → no-op (emit nothing)

    # Current blocks with no desired counterpart → soft-cancel.
    for c in current:
        key = _identity_key(c.title, c.source_kind, c.source_ref)
        if key not in matched_keys:
            ops.append(Op(
                action="soft_cancel", title=c.title,
                block_id=c.block_id, google_event_id=c.google_event_id, etag=c.etag,
                source_kind=c.source_kind, source_ref=c.source_ref,
            ))

    return ops


def build_plan_diff(
    proposed: List[ProposedBlock],
    fixed: List[FixedBlock],
    current: List[CurrentBlock],
) -> PlanDiff:
    """Full pipeline: validate overlaps → reconcile → summarize."""
    kept, conflicts = validate_overlaps(proposed, fixed)
    ops = reconcile(kept, current)
    return PlanDiff(operations=ops, conflicts=conflicts, summary=_summarize(ops, conflicts))


def _summarize(ops: List[Op], conflicts: List[str]) -> str:
    creates = sum(1 for o in ops if o.action == "create")
    patches = sum(1 for o in ops if o.action == "patch")
    cancels = sum(1 for o in ops if o.action == "soft_cancel")
    parts: List[str] = []
    if creates:
        parts.append(f"add {creates}")
    if patches:
        parts.append(f"update {patches}")
    if cancels:
        parts.append(f"remove {cancels}")
    body = ", ".join(parts) if parts else "no changes"
    if conflicts:
        body += f" ({len(conflicts)} couldn't fit)"
    return body
