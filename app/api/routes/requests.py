from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.agent.base import Agent
from app.api.deps import get_agent, get_settings_dep
from app.api.schemas import (
    AgentRunOut,
    AnalyzeResponse,
    ConfirmIn,
    ConfirmResponse,
    ErrorOut,
    LinkOut,
    LinkReviewIn,
    RequestCreate,
    RequestDetail,
    RequestList,
    RequestOut,
    ReviewIn,
    ReviewResponse,
    TraceOut,
)
from app.core.config import Settings
from app.core.enums import RequestStatus, UrgencyLevel
from app.db.session import get_db
from app.services import analysis, requests as svc, review

router = APIRouter(prefix="/requests", tags=["requests"])

DB = Annotated[Session, Depends(get_db)]
RequestId = Annotated[int, Path(gt=0, description="ID обращения")]
ERRORS = {404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}}


@router.post("", status_code=201, response_model=RequestOut, responses={422: {"model": ErrorOut}})
def create_request(payload: RequestCreate, db: DB) -> RequestOut:
    """Создать обращение. Анализ запускается отдельно: POST /requests/{id}/analyze."""
    return svc.to_request_out(svc.create_request(db, payload))


@router.get("", response_model=RequestList)
def list_requests(
    db: DB,
    status: Annotated[list[RequestStatus] | None, Query()] = None,
    topic: str | None = None,
    department_id: int | None = None,
    priority: UrgencyLevel | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Поиск по тексту/сути/адресу")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RequestList:
    items, total = svc.list_requests(
        db, statuses=status, topic=topic, department_id=department_id,
        priority=priority.value if priority else None, date_from=date_from, date_to=date_to,
        q=q, limit=limit, offset=offset,
    )
    return RequestList(items=items, total=total, limit=limit, offset=offset)


@router.get("/{request_id}", response_model=RequestDetail, responses=ERRORS)
def get_request(request_id: RequestId, db: DB) -> RequestDetail:
    return svc.build_detail(db, svc.get_request(db, request_id))


@router.post(
    "/{request_id}/analyze",
    response_model=AnalyzeResponse,
    responses=ERRORS | {500: {"model": ErrorOut}, 503: {"model": ErrorOut}},
)
def analyze(
    request_id: RequestId,
    db: DB,
    agent: Annotated[Agent, Depends(get_agent)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AnalyzeResponse:
    """Запустить/повторить анализ агентом. Никогда не регистрирует обращение."""
    return analysis.run_analysis(db, request_id, agent, settings)


@router.post("/{request_id}/review", response_model=ReviewResponse, responses=ERRORS)
def submit_review(request_id: RequestId, body: ReviewIn, db: DB) -> ReviewResponse:
    """Решение сотрудника: accept / correct / reject / request_clarification / escalate."""
    return review.submit_review(db, request_id, body)


@router.post("/{request_id}/confirm-registration", response_model=ConfirmResponse, responses=ERRORS)
def confirm_registration(request_id: RequestId, db: DB, body: ConfirmIn | None = None) -> ConfirmResponse:
    """Подтвердить подготовленную регистрацию. Повторный вызов идемпотентен."""
    return review.confirm_registration(db, request_id, (body or ConfirmIn()).confirmed_by)


@router.post("/{request_id}/clarification", response_model=ConfirmResponse, responses=ERRORS)
def confirm_clarification(request_id: RequestId, db: DB, body: ConfirmIn | None = None) -> ConfirmResponse:
    """Подтвердить отправку подготовленного уточняющего вопроса (в демо — без реальной отправки)."""
    return review.confirm_clarification(db, request_id, (body or ConfirmIn()).confirmed_by)


@router.get("/{request_id}/trace", response_model=TraceOut, responses=ERRORS)
def trace(request_id: RequestId, db: DB) -> TraceOut:
    req = svc.get_request(db, request_id)
    return TraceOut(request_id=req.id, runs=[AgentRunOut.model_validate(r) for r in req.runs])


@router.patch("/{request_id}/links/{link_id}", response_model=LinkOut, responses=ERRORS)
def review_link(
    request_id: RequestId, link_id: Annotated[int, Path(gt=0)], body: LinkReviewIn, db: DB
) -> LinkOut:
    """Подтвердить/отклонить связь-кандидат в дубликаты. Обращения не объединяются."""
    return svc.review_link(db, request_id, link_id, body.decision, body.reviewer)
