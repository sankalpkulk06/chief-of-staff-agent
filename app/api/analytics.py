from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_registry, require_auth
from app.storage.sqlite_registry import SQLiteRegistry

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_auth)])

# SQLite %w: 0=Sunday … 6=Saturday. Remap to Mon=0 … Sun=6 for the UI.
_DOW_REMAP = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 0: 6}


class AnalyticsOut(BaseModel):
    # heatmap[day][hour] = turn count  (day 0=Mon, hour 0–23)
    heatmap: List[List[int]]
    most_active_hour: int
    # top topics: [{label, count, pct}]
    topics: List[Dict]


@router.get("", response_model=AnalyticsOut)
async def get_analytics(
    registry: SQLiteRegistry = Depends(get_registry),
) -> AnalyticsOut:
    # ---- heatmap: turns per (day-of-week, hour) over last 14 days ----
    rows = registry._connection.execute(
        """
        SELECT CAST(strftime('%w', created_at) AS INTEGER) AS dow,
               CAST(strftime('%H', created_at) AS INTEGER) AS hour,
               COUNT(*) AS cnt
        FROM chat_turns
        WHERE created_at >= datetime('now', '-14 days')
        GROUP BY dow, hour
        """
    ).fetchall()

    heatmap = [[0] * 24 for _ in range(7)]
    for r in rows:
        day_idx = _DOW_REMAP.get(r["dow"], 0)
        heatmap[day_idx][r["hour"]] = r["cnt"]

    # most active hour (global, not just last 14 days)
    hour_rows = registry._connection.execute(
        """
        SELECT CAST(strftime('%H', created_at) AS INTEGER) AS hour,
               COUNT(*) AS cnt
        FROM chat_turns
        GROUP BY hour
        ORDER BY cnt DESC
        LIMIT 1
        """
    ).fetchone()
    most_active_hour = hour_rows["hour"] if hour_rows else 9

    # ---- topics: top user-turn keywords as a proxy for topics ----
    kw_rows = registry._connection.execute(
        """
        SELECT content FROM chat_turns
        WHERE role = 'user'
        ORDER BY created_at DESC
        LIMIT 500
        """
    ).fetchall()

    stop = {
        "the","a","an","is","it","what","how","can","do","i","my","me","you",
        "to","of","in","on","for","and","or","with","this","that","was","are",
        "be","have","has","had","will","would","could","should","about","from",
        "at","by","not","no","but","we","they","their","there","when","where",
        "which","who","why","please","just","some","any","all","get","its",
        "if","so","as","up","out","into","also","tell","show","need","want",
        "use","using","used","make","like","know","go","more","then","than",
        "your","our","his","her","them","been","did","does","let","now","new",
        "see","give","check","find","look","help","sage","hi","hey","ok","yes",
    }

    word_counts: Dict[str, int] = {}
    for r in kw_rows:
        for word in r["content"].lower().split():
            w = word.strip(".,!?;:\"'()[]{}")
            if len(w) > 3 and w not in stop and w.isalpha():
                word_counts[w] = word_counts.get(w, 0) + 1

    top = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)[:6]
    max_count = top[0][1] if top else 1
    topics = [
        {"label": w, "count": c, "pct": round(c / max_count * 100)}
        for w, c in top
    ]

    return AnalyticsOut(heatmap=heatmap, most_active_hour=most_active_hour, topics=topics)
