"""Агрегированная аналитика по обращениям."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import AnalyticsOut
from app.core.enums import LinkStatus, RequestStatus, ReviewDecision, RunStatus, UrgencyLevel
from app.db.models import AgentRun, Department, Request, RequestLink, Review


def summary(db: Session, date_from: datetime | None = None, date_to: datetime | None = None) -> AnalyticsOut:
    conds = []
    if date_from:
        conds.append(Request.received_at >= date_from)
    if date_to:
        conds.append(Request.received_at <= date_to)

    def group(col, *, join_dept: bool = False) -> dict[str, int]:
        stmt = select(col, func.count(Request.id)).select_from(Request)
        if join_dept:
            stmt = stmt.outerjoin(Department, Request.department_id == Department.id)
        stmt = stmt.where(*conds).group_by(col)
        return {(k if k is not None else "—"): v for k, v in db.execute(stmt).all()}

    total = db.scalar(select(func.count(Request.id)).where(*conds)) or 0
    by_status = group(Request.status)
    urgent = db.scalar(
        select(func.count(Request.id)).where(
            *conds,
            (Request.priority.in_([UrgencyLevel.HIGH.value, UrgencyLevel.CRITICAL.value]))
            | (Request.status == RequestStatus.URGENT_ESCALATION.value),
        )
    ) or 0

    req_ids = select(Request.id).where(*conds)
    dup = db.scalar(
        select(func.count(RequestLink.id)).where(
            RequestLink.status == LinkStatus.CANDIDATE.value, RequestLink.request_id.in_(req_ids)
        )
    ) or 0
    decisions = dict(
        db.execute(
            select(Review.decision, func.count(Review.id)).where(Review.request_id.in_(req_ids)).group_by(Review.decision)
        ).all()
    )
    accepted = decisions.get(ReviewDecision.ACCEPT.value, 0) + decisions.get(ReviewDecision.CORRECT.value, 0)
    runs = dict(
        db.execute(
            select(AgentRun.status, func.count(AgentRun.id)).where(AgentRun.request_id.in_(req_ids)).group_by(AgentRun.status)
        ).all()
    )
    runs_total = sum(runs.values())
    failed = runs.get(RunStatus.FAILED.value, 0)

    return AnalyticsOut(
        total=total,
        by_status=by_status,
        by_topic=group(Request.topic),
        by_department=group(Department.name, join_dept=True),
        by_priority=group(Request.priority),
        urgent=urgent,
        duplicate_candidates=dup,
        registered=by_status.get(RequestStatus.REGISTERED.value, 0),
        reviews_total=sum(decisions.values()),
        correction_rate=round(decisions.get(ReviewDecision.CORRECT.value, 0) / accepted, 3) if accepted else None,
        failed_runs=failed,
        technical_error_rate=round(failed / runs_total, 3) if runs_total else None,
    )
