"""Fleet-wide settings: the telemetry message budget."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_role, verify_console_access, verify_read_access
from app.budget import controller as budget
from app.database import get_db
from app.security import OPERATOR

router = APIRouter(prefix="/api/v1", tags=["fleet"])


class BudgetUpdate(BaseModel):
    enabled: bool
    messages_per_minute: float | None = Field(None, gt=0, le=1_000_000)


@router.get("/fleet/budget")
async def get_budget(db: AsyncSession = Depends(get_db), _=Depends(verify_read_access)):
    """The budget, and what the last allocation observed and planned."""
    current = await budget.get_budget(db)
    return {**current, "last": budget.controller.last if budget.controller else {}}


@router.put("/fleet/budget")
async def set_budget(
    body: BudgetUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_console_access),
    __=Depends(require_role(OPERATOR)),
):
    """Turn the budget on or off. While on, the API sets every robot's
    tolerances (``tol_*``) every 30 seconds to keep the fleet within it."""
    return await budget.set_budget(db, body.model_dump())
