from datetime import date, timedelta
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_chat_service, require_auth
from app.core.chat_service import ChatService

router = APIRouter(prefix="/habits", tags=["habits"], dependencies=[Depends(require_auth)])


class HabitOut(BaseModel):
    id: str
    name: str
    streak: int
    days_done: int
    logged_today: bool
    week: List[str]  # 7 entries: "done" | "skip" | "none" | "future"


@router.get("", response_model=List[HabitOut])
async def list_habits(
    chat_service: ChatService = Depends(get_chat_service),
) -> List[HabitOut]:
    habit_service = chat_service.get_habit_service()
    if habit_service is None:
        return []

    summaries = habit_service.get_weekly_summary()
    today = date.today()
    result = []

    for s in summaries:
        # Build a per-day status for the last 7 days (oldest first)
        days = [(today - timedelta(days=6 - i)) for i in range(7)]

        # Fetch logs for this habit over the last 7 days
        rows = habit_service._db.execute(
            """
            SELECT DATE(logged_at) as day, status
            FROM habit_logs
            WHERE habit_id = ? AND DATE(logged_at) >= ?
            """,
            (s.habit.id, days[0].isoformat()),
        ).fetchall()

        log_map = {row["day"]: row["status"] for row in rows}

        week = []
        for d in days:
            if d > today:
                week.append("future")
            elif d.isoformat() in log_map:
                week.append(log_map[d.isoformat()])
            else:
                week.append("none")

        result.append(HabitOut(
            id=s.habit.id,
            name=s.habit.name,
            streak=s.streak,
            days_done=s.days_done,
            logged_today=s.logged_today,
            week=week,
        ))

    return result
