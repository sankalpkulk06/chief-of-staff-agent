from datetime import date, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user, get_registry
from app.core.habit_service import HabitService

router = APIRouter(prefix="/habits", tags=["habits"])


class HabitOut(BaseModel):
    id: str
    name: str
    streak: int
    days_done: int
    logged_today: bool
    week: List[str]


@router.get("", response_model=List[HabitOut])
async def list_habits(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> List[HabitOut]:
    habit_service = HabitService(registry, user_id=current_user["user_id"])
    summaries = habit_service.get_weekly_summary()
    today = date.today()
    result = []

    for s in summaries:
        days = [(today - timedelta(days=6 - i)) for i in range(7)]
        rows = habit_service.get_logs_since(s.habit.id, days[0])
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
