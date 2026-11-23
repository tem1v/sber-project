"""Обращения: создание, выборка, карточка, связи-дубликаты."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import (
    AnalysisOut,
    DeadlineOut,
    DraftOut,
    LinkOut,
    RequestCreate,
    RequestDetail,
    RequestListItem,
    RequestOut,
    ReviewOut,
    RouteCheck,
)
from app.core.enums import DraftStatus, LinkStatus, RequestStatus, RunStatus
from app.core.errors import ConflictError, NotFoundError
from app.core.taxonomy import utcnow
from app.db.models import AgentRun, Request, RequestLink
from app.services.statuses import REVIEWABLE, can_transition


def create_request(db: Session, payload: RequestCreate) -> Request:
    req = Request(
        raw_text=payload.raw_text,
        source_channel=payload.source_channel.value,
        received_at=payload.received_at or utcnow(),
        metadata_json=payload.metadata,
        status=RequestStatus.NEW.value,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


def get_request(db: Session, request_id: int, *, for_update: bool = False) -> Request:
    stmt = select(Request).where(Request.id == request_id)
    if for_update:
        stmt = stmt.with_for_update()
    req = db.scalars(stmt).first()
    if req is None:
        raise NotFoundError(f"Обращение #{request_id} не найдено", details={"request_id": request_id})
    return req


def list_requests(
    db: Session,
    *,
    statuses: list[RequestStatus] | None = None,
    topic: str | None = None,
    department_id: int | None = None,
    priority: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[RequestListItem], int]:
    stmt = select(Request)
    if statuses:
        stmt = stmt.where(Request.status.in_([s.value for s in statuses]))
    if topic:
        stmt = stmt.where(Request.topic == topic)
    if department_id is not None:
        stmt = stmt.where(Request.department_id == department_id)
    if priority:
        stmt = stmt.where(Request.priority == priority)
    if date_from:
        stmt = stmt.where(Request.received_at >= date_from)
    if date_to:
        stmt = stmt.where(Request.received_at <= date_to)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Request.raw_text.ilike(like), Request.summary.ilike(like), Request.address.ilike(like)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.options(selectinload(Request.department))
        .order_by(Request.received_at.desc(), Request.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [to_list_item(r) for r in rows], total


def to_list_item(req: Request) -> RequestListItem:
    item = RequestListItem.model_validate(req)
    item.department_name = req.department.name if req.department else None
    return item


def to_request_out(req: Request) -> RequestOut:
    out = RequestOut.model_validate(req)
    out.department_name = req.department.name if req.department else None
    return out


def _links(db: Session, request_id: int) -> list[LinkOut]:
    rows = db.scalars(
        select(RequestLink)
        .where(or_(RequestLink.request_id == request_id, RequestLink.related_request_id == request_id))
        .options(selectinload(RequestLink.related), selectinload(RequestLink.source))
        .order_by(RequestLink.similarity_score.desc())
    ).all()
    out = []
    for link in rows:
        other = link.related if link.request_id == request_id else link.source
        out.append(
            LinkOut(
                id=link.id,
                related_request_id=other.id,
                related_summary=other.summary or other.raw_text[:160],
                related_status=other.status,
                similarity_score=link.similarity_score,
                link_type=link.link_type,
                status=link.status,
                reviewed_by=link.reviewed_by,
                reviewed_at=link.reviewed_at,
            )
        )
    return out


def allowed_actions(req: Request) -> list[str]:
    actions = []
    if can_transition(req.status, RequestStatus.ANALYZING):
        actions.append("analyze")
    if req.status in REVIEWABLE:
        actions.append("review")
    pending = {d.action_type for d in req.drafts if d.status == DraftStatus.PENDING}
    if req.status == RequestStatus.READY_FOR_CONFIRMATION and "registration" in pending:
        actions.append("confirm_registration")
    if "clarification" in pending:
        actions.append("confirm_clarification")
    return actions


def last_state(db: Session, request_id: int) -> dict[str, Any] | None:
    run = db.scalars(
        select(AgentRun)
        .where(AgentRun.request_id == request_id, AgentRun.status == RunStatus.SUCCEEDED.value)
        .order_by(AgentRun.id.desc())
        .limit(1)
    ).first()
    return run.state_json if run else None


def build_detail(db: Session, req: Request) -> RequestDetail:
    analysis = req.analyses[-1] if req.analyses else None
    state = last_state(db, req.id) or {}
    validation = state.get("validation")
    return RequestDetail(
        request=to_request_out(req),
        analysis=AnalysisOut.model_validate(analysis) if analysis else None,
        analysis_versions=len(req.analyses),
        department_candidates=state.get("department_candidates") or [],
        route_check=RouteCheck.model_validate(validation) if validation else None,
        similar=_links(db, req.id),
        deadline=DeadlineOut.model_validate(req.deadlines[-1]) if req.deadlines else None,
        drafts=[DraftOut.model_validate(d) for d in req.drafts],
        reviews=[ReviewOut.model_validate(r) for r in req.reviews],
        allowed_actions=allowed_actions(req),
    )


def review_link(db: Session, request_id: int, link_id: int, decision: str, reviewer: str) -> LinkOut:
    get_request(db, request_id)
    link = db.get(RequestLink, link_id)
    if link is None or request_id not in (link.request_id, link.related_request_id):
        raise NotFoundError(f"Связь #{link_id} у обращения #{request_id} не найдена")
    new_status = LinkStatus.CONFIRMED if decision == "confirm" else LinkStatus.REJECTED
    if link.status != LinkStatus.CANDIDATE:
        if link.status != new_status:
            raise ConflictError("Решение по связи уже принято", details={"status": link.status})
    else:  # повтор того же решения идемпотентен
        link.status = new_status.value
        link.reviewed_by = reviewer
        link.reviewed_at = utcnow()
        db.commit()
    return next(item for item in _links(db, request_id) if item.id == link.id)
