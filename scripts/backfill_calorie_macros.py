"""Backfill dish + macros + item breakdown onto calorie entries logged before those
columns existed.

For every INTAKE entry that hasn't been enriched yet (no dish), re-runs the calorie
estimator over its saved description to fill in dish, protein/carbs/fat grams, and the
item breakdown. The confirmed calorie total is kept as-is (only nutrition metadata is
added). Idempotent: enriched rows (dish set) are skipped, so it's safe to re-run.

Run:  python scripts/backfill_calorie_macros.py [--dry-run]
"""
import json
import sys

from app.config import get_settings
from app.core.calorie_service import estimate_calories
from app.providers.factory import agent_model_specs, create_chat_provider
from app.storage.factory import create_registry

DRY = "--dry-run" in sys.argv


def main() -> None:
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(settings.database_url, paths.sqlite_db_path)
    db = getattr(registry, "_connection", None) or getattr(registry, "_conn")
    is_pg = hasattr(registry, "_conn")
    q = (lambda s: s.replace("?", "%s")) if is_pg else (lambda s: s)

    provider = create_chat_provider(settings, agent_model_specs(settings)["action_agent"])

    rows = db.execute(
        "SELECT id, description, calories FROM calorie_entries "
        "WHERE kind = 'intake' AND (dish IS NULL OR dish = '')"
    ).fetchall()
    print(f"Found {len(rows)} intake entr{'y' if len(rows) == 1 else 'ies'} to enrich"
          + (" (dry run)" if DRY else ""))

    for r in rows:
        rid = r["id"] if hasattr(r, "keys") else r[0]
        desc = r["description"] if hasattr(r, "keys") else r[1]
        try:
            est = estimate_calories(provider, desc, force=True)
        except Exception as exc:
            print(f"  ! skip {rid[:8]}: estimate failed ({exc})")
            continue
        dish = est.get("dish")
        p, c, f = est.get("protein_g", 0), est.get("carbs_g", 0), est.get("fat_g", 0)
        items_json = json.dumps(est["items"]) if est.get("items") else None
        print(f"  {desc[:44]:<44} -> {dish!r}  P{p} C{c} F{f}  items={len(est.get('items') or [])}")
        if DRY:
            continue
        cur = db.cursor() if is_pg else db
        cur.execute(q(
            "UPDATE calorie_entries SET dish = ?, protein_g = ?, carbs_g = ?, fat_g = ?, "
            "items_json = COALESCE(?, items_json) WHERE id = ?"
        ), (dish, float(p or 0), float(c or 0), float(f or 0), items_json, rid))

    if not DRY:
        db.commit()
        print("Committed.")


if __name__ == "__main__":
    main()
