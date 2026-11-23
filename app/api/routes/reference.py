from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.schemas import AnalyticsOut, DepartmentOut
from app.core.errors import ServiceUnavailableError
from app.db.models import Department
from app.db.session import get_db
from app.services import analytics

DB = Annotated[Session, Depends(get_db)]

router = APIRouter()
health_router = APIRouter(tags=["health"])


@router.get("/departments", response_model=list[DepartmentOut], tags=["departments"])
def departments(db: DB, include_inactive: bool = False) -> list[DepartmentOut]:
    stmt = select(Department).order_by(Department.name)
    if not include_inactive:
        stmt = stmt.where(Department.active.is_(True))
    return [DepartmentOut.model_validate(d) for d in db.scalars(stmt)]


@router.get("/analytics/summary", response_model=AnalyticsOut, tags=["analytics"])
def analytics_summary(db: DB, date_from: datetime | None = None, date_to: datetime | None = None) -> AnalyticsOut:
    return analytics.summary(db, date_from, date_to)


@health_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/health/ready")
def ready(db: DB) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise ServiceUnavailableError("База данных недоступна", code="DB_UNAVAILABLE") from exc
    return {"status": "ready"}
