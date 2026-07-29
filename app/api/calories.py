"""Calorie counter API — today's totals, history, manual entry, and the daily budget.

Mirrors app/api/habits.py: per-request user-scoped CalorieService, compute-on-read
totals derived from the append-only calorie_entries table. The daily budget lives in
user_settings via app/core/calorie_util.py.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_registry
from app.core import calorie_util
from app.core.calorie_service import CalorieService

router = APIRouter(prefix="/calories", tags=["calories"])


class EntryOut(BaseModel):
    id: str
    description: str
    calories: int
    kind: str = "intake"
    eaten_at: Optional[str] = None


class TodayOut(BaseModel):
    total: int            # calories eaten (intake)
    burned: int           # calories burned in workouts
    net: int              # intake - burned
    budget: int
    remaining: int        # budget - net (burning gives you more room)
    entries: List[EntryOut]


class EntryIn(BaseModel):
    description: str = Field(..., min_length=1)
    calories: int = Field(..., ge=0)
    kind: str = "intake"


class BudgetIn(BaseModel):
    budget: int


@router.get("/today", response_model=TodayOut)
async def get_today(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> TodayOut:
    uid = current_user["user_id"]
    svc = CalorieService(registry, user_id=uid)
    budget = calorie_util.get_calorie_budget(registry, uid)
    total = svc.today_total()
    burned = svc.today_burned()
    net = total - burned
    return TodayOut(
        total=total,
        burned=burned,
        net=net,
        budget=budget,
        remaining=budget - net,
        entries=[EntryOut(**e) for e in svc.list_today()],
    )


@router.get("/history")
async def get_history(
    days: int = 7,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, Any]:
    days = max(1, min(days, 90))
    uid = current_user["user_id"]
    svc = CalorieService(registry, user_id=uid)
    return {
        "budget": calorie_util.get_calorie_budget(registry, uid),
        "days": svc.daily_totals(days=days),
    }


@router.post("", response_model=TodayOut)
async def add_entry(
    payload: EntryIn,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> TodayOut:
    uid = current_user["user_id"]
    svc = CalorieService(registry, user_id=uid)
    svc.add_entry(payload.description.strip(), payload.calories, kind=payload.kind)
    budget = calorie_util.get_calorie_budget(registry, uid)
    total, burned = svc.today_total(), svc.today_burned()
    net = total - burned
    return TodayOut(
        total=total, burned=burned, net=net, budget=budget, remaining=budget - net,
        entries=[EntryOut(**e) for e in svc.list_today()],
    )


@router.delete("/{entry_id}")
async def delete_entry(
    entry_id: str,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, bool]:
    svc = CalorieService(registry, user_id=current_user["user_id"])
    if not svc.delete_entry(entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"deleted": True}


@router.put("/budget")
async def set_budget(
    payload: BudgetIn,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, int]:
    try:
        value = calorie_util.set_calorie_budget(registry, current_user["user_id"], payload.budget)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"budget": value}
