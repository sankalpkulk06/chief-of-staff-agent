"""Unit tests for the pure-Python plan diff engine (app/core/plan_diff.py)."""
import pytest

from app.core.plan_diff import (
    CurrentBlock,
    FixedBlock,
    Op,
    ProposedBlock,
    build_plan_diff,
    hhmm_to_minutes,
    minutes_to_hhmm,
    reconcile,
    validate_overlaps,
)


def P(title, start, end, kind="conversation", ref=None):
    return ProposedBlock(title, hhmm_to_minutes(start), hhmm_to_minutes(end), kind, ref)


def F(title, start, end):
    return FixedBlock(title, hhmm_to_minutes(start), hhmm_to_minutes(end))


def C(block_id, title, start, end, gid="g", etag="e", kind="conversation", ref=None):
    return CurrentBlock(block_id, title, hhmm_to_minutes(start), hhmm_to_minutes(end), gid, etag, kind, ref)


# --- time helpers ---------------------------------------------------------

def test_hhmm_roundtrip():
    assert hhmm_to_minutes("08:30") == 510
    assert minutes_to_hhmm(510) == "08:30"
    assert hhmm_to_minutes("00:00") == 0
    assert hhmm_to_minutes("23:59") == 1439


@pytest.mark.parametrize("bad", ["24:00", "08:60", "8", "08:30:00", "aa:bb", "-1:00"])
def test_hhmm_rejects_malformed(bad):
    with pytest.raises(ValueError):
        hhmm_to_minutes(bad)


# --- Stage A: overlap validation -----------------------------------------

def test_no_overlap_all_kept():
    proposed = [P("Gym", "08:00", "09:30"), P("Work", "10:00", "12:00")]
    kept, conflicts = validate_overlaps(proposed, [])
    assert len(kept) == 2
    assert conflicts == []


def test_touching_blocks_allowed():
    # end == start is not an overlap
    proposed = [P("A", "08:00", "09:00"), P("B", "09:00", "10:00")]
    kept, conflicts = validate_overlaps(proposed, [])
    assert len(kept) == 2
    assert conflicts == []


def test_block_inside_fixed_dropped():
    proposed = [P("Gym", "08:30", "09:00")]
    fixed = [F("Standup", "08:00", "09:30")]
    kept, conflicts = validate_overlaps(proposed, fixed)
    assert kept == []
    assert len(conflicts) == 1
    assert "Standup" in conflicts[0]


def test_block_straddling_fixed_boundary_dropped():
    proposed = [P("Gym", "08:00", "08:45")]
    fixed = [F("Meeting", "08:30", "09:00")]
    kept, conflicts = validate_overlaps(proposed, fixed)
    assert kept == []
    assert len(conflicts) == 1


def test_block_touching_fixed_boundary_kept():
    proposed = [P("Gym", "07:00", "08:30")]
    fixed = [F("Meeting", "08:30", "09:00")]
    kept, conflicts = validate_overlaps(proposed, fixed)
    assert len(kept) == 1
    assert conflicts == []


def test_two_proposed_overlap_earlier_wins():
    proposed = [P("Gym", "08:00", "09:30"), P("Run", "09:00", "10:00")]
    kept, conflicts = validate_overlaps(proposed, [])
    assert len(kept) == 1
    assert kept[0].title == "Gym"  # earlier start wins
    assert len(conflicts) == 1
    assert "Run" in conflicts[0]


def test_zero_length_block_dropped():
    proposed = [P("Weird", "08:00", "08:00")]
    kept, conflicts = validate_overlaps(proposed, [])
    assert kept == []
    assert "end is not after start" in conflicts[0]


def test_empty_proposed():
    kept, conflicts = validate_overlaps([], [F("X", "08:00", "09:00")])
    assert kept == []
    assert conflicts == []


# --- Stage B: reconciliation ---------------------------------------------

def test_all_create_when_no_current():
    desired = [P("Gym", "08:00", "09:30", "habit", "h1"), P("Work", "10:00", "12:00")]
    ops = reconcile(desired, [])
    assert all(o.action == "create" for o in ops)
    assert len(ops) == 2


def test_all_soft_cancel_when_no_desired():
    current = [C("b1", "Gym", "08:00", "09:30", kind="habit", ref="h1")]
    ops = reconcile([], current)
    assert len(ops) == 1
    assert ops[0].action == "soft_cancel"
    assert ops[0].block_id == "b1"
    assert ops[0].etag == "e"


def test_unchanged_is_noop():
    desired = [P("Gym", "08:00", "09:30", "habit", "h1")]
    current = [C("b1", "Gym", "08:00", "09:30", kind="habit", ref="h1")]
    ops = reconcile(desired, current)
    assert ops == []


def test_time_change_is_patch_not_recreate():
    desired = [P("Gym", "06:00", "07:30", "habit", "h1")]
    current = [C("b1", "Gym", "08:00", "09:30", kind="habit", ref="h1")]
    ops = reconcile(desired, current)
    assert len(ops) == 1
    assert ops[0].action == "patch"
    assert ops[0].block_id == "b1"
    assert ops[0].start_min == hhmm_to_minutes("06:00")
    assert ops[0].etag == "e"


def test_rename_cancels_and_creates():
    # Identity is title-based (the LLM can't reproduce source_ref), so a renamed block
    # reads as remove-old + add-new — still the correct end state.
    desired = [P("Morning workout", "08:00", "09:30", "habit", "h1")]
    current = [C("b1", "Gym", "08:00", "09:30", kind="habit", ref="h1")]
    ops = reconcile(desired, current)
    assert sorted(o.action for o in ops) == ["create", "soft_cancel"]


def test_unchanged_noop_even_when_ref_differs():
    # The LLM re-emits a kept block with a different/absent source_ref; title still
    # matches, so it must be a no-op (this is the churn bug the title-key fixes).
    desired = [P("Gym", "08:00", "09:30", "conversation", None)]
    current = [C("b1", "Gym", "08:00", "09:30", kind="habit", ref="h1")]
    assert reconcile(desired, current) == []


def test_title_match_when_no_ref():
    desired = [P("Deep Work", "10:00", "12:00")]
    current = [C("b1", "deep   work", "09:00", "11:00")]  # normalized title match
    ops = reconcile(desired, current)
    assert len(ops) == 1
    assert ops[0].action == "patch"


def test_mixed_create_patch_cancel():
    desired = [
        P("Gym", "06:00", "07:30", "habit", "h1"),   # moved → patch
        P("New task", "10:00", "11:00", "todo", "t9"),  # new → create
    ]
    current = [
        C("b1", "Gym", "08:00", "09:30", kind="habit", ref="h1"),   # will patch
        C("b2", "Old task", "14:00", "15:00", kind="todo", ref="t2"),  # gone → cancel
    ]
    ops = reconcile(desired, current)
    actions = sorted(o.action for o in ops)
    assert actions == ["create", "patch", "soft_cancel"]


# --- full pipeline --------------------------------------------------------

def test_build_plan_diff_end_to_end():
    proposed = [
        P("Gym", "08:00", "09:30", "habit", "h1"),
        P("Overlap", "09:00", "10:00"),  # collides with Gym → dropped
    ]
    fixed = [F("Standup", "14:00", "14:30")]
    current = [C("b1", "Gym", "07:00", "08:00", kind="habit", ref="h1")]  # moved → patch
    diff = build_plan_diff(proposed, fixed, current)

    assert len(diff.conflicts) == 1
    patch_ops = [o for o in diff.operations if o.action == "patch"]
    assert len(patch_ops) == 1
    assert patch_ops[0].block_id == "b1"
    assert "update 1" in diff.summary
    assert "couldn't fit" in diff.summary


def test_summary_no_changes():
    diff = build_plan_diff([], [], [])
    assert diff.summary == "no changes"
