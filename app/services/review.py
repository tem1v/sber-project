"""Решения сотрудника, подготовка черновиков и их подтверждение.

Правила: черновик готовится только по решению сотрудника; регистрация и отправка
уточнения — отдельные операции подтверждения; повторное подтверждение идемпотентно.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import ConfirmResponse, DraftOut, ReviewIn, ReviewOut, ReviewResponse
from app.core.enums import ActionType, DraftStatus, RequestStatus, ReviewDecision
from app.core.errors import ConflictError, UnprocessableError
from app.core.taxonomy import utcnow
from app.db.models import ActionDraft, Department, Request, Review
from app.services.requests import get_request
from app.services.statuses import REVIEWABLE, ensure_transition

D = ReviewDecision


def _pending(req: Request, action: ActionType) -> ActionDraft | None:
    return next((d for d in req.drafts if d.action_type == action and d.status == DraftStatus.PENDING), None)


def _cancel_pending(req: Request, *actions: ActionType) -> None:
    for d in req.drafts:
        if d.status == DraftStatus.PENDING and d.action_type in actions:
            d.status = DraftStatus.CANCELLED.value


def _apply_corrections(db: Session, req: Request, fields: dict[str, Any]) -> None:
    if "department_id" in fields:
        dept = db.get(Department, fields["department_id"])
        if dept is None or not dept.active:
            raise UnprocessableError(
                "Ведомство отсутствует в справочнике или неактивно",
                code="INVALID_DEPARTMENT",
                details={"department_id": fields["department_id"]},
            )
    for key, value in fields.items():
        setattr(req, key, value)


def _registration_payload(req: Request) -> dict[str, Any]:
    deadline = req.deadlines[-1] if req.deadlines else None
    return {
        "request_id": req.id,
        "department_id": req.department_id,
        "department_name": req.department.name if req.department else None,
        "topic": req.topic,
        "subtopic": req.subtopic,
        "address": req.address,
        "summary": req.summary,
        "priority": req.priority,
        "due_at": deadline.due_at.isoformat() if deadline else None,
    }


def _clarification_questions(req: Request, explicit: list[str] | None) -> list[str]:
    questions = [q.strip() for q in (explicit or []) if q and q.strip()]
    if not questions and req.analyses:
        questions = list(req.analyses[-1].extracted_json.get("clarifying_questions") or [])
    return questions


def submit_review(db: Session, request_id: int, body: ReviewIn) -> ReviewResponse:
    req = get_request(db, request_id, for_update=True)
    if req.status not in REVIEWABLE:
        raise ConflictError(
            f"Решение по обращению в статусе {req.status} принять нельзя",
            code="INVALID_STATUS_TRANSITION",
            details={"status": req.status, "reviewable": sorted(REVIEWABLE)},
        )

    corrections = body.corrected_fields.model_dump(exclude_unset=True) if body.corrected_fields else {}
    if body.decision == D.CORRECT and not corrections:
        raise UnprocessableError("Для решения correct нужны исправленные поля", code="NO_CORRECTIONS")
    if corrections and body.decision not in (D.CORRECT, D.ESCALATE, D.REQUEST_CLARIFICATION):
        raise UnprocessableError("Исправления передаются с решением correct", code="UNEXPECTED_CORRECTIONS")
    if corrections.get("priority") is not None:
        corrections["priority"] = str(corrections["priority"])

    draft: ActionDraft | None = None
    if body.decision in (D.ACCEPT, D.CORRECT):
        target = RequestStatus.READY_FOR_CONFIRMATION
        ensure_transition(req.status, target)
        _apply_corrections(db, req, corrections)
        db.flush()
        db.refresh(req, ["department"])
        if req.department_id is None:
            raise UnprocessableError(
                "Ведомство не определено — укажите его через correct", code="DEPARTMENT_REQUIRED"
            )
        _cancel_pending(req, ActionType.REGISTRATION, ActionType.CLARIFICATION)
        db.flush()
        draft = ActionDraft(
            request_id=req.id,
            action_type=ActionType.REGISTRATION.value,
            payload_json=_registration_payload(req) | {"corrected_fields": corrections},
            status=DraftStatus.PENDING.value,
            created_by=body.reviewer,
        )
    elif body.decision == D.REQUEST_CLARIFICATION:
        target = RequestStatus.NEEDS_CLARIFICATION
        ensure_transition(req.status, target)
        _apply_corrections(db, req, corrections)
        questions = _clarification_questions(req, body.questions)
        if not questions:
            raise UnprocessableError("Нет вопросов для уточнения — передайте questions", code="NO_QUESTIONS")
        _cancel_pending(req, ActionType.REGISTRATION, ActionType.CLARIFICATION)
        db.flush()
        draft = ActionDraft(
            request_id=req.id,
            action_type=ActionType.CLARIFICATION.value,
            payload_json={"request_id": req.id, "channel": req.source_channel, "questions": questions},
            status=DraftStatus.PENDING.value,
            created_by=body.reviewer,
        )
    elif body.decision == D.ESCALATE:
        target = RequestStatus.URGENT_ESCALATION
        ensure_transition(req.status, target)
        _apply_corrections(db, req, corrections)
        _cancel_pending(req, ActionType.REGISTRATION)
    else:  # reject
        target = RequestStatus.REJECTED
        ensure_transition(req.status, target)
        _cancel_pending(req, ActionType.REGISTRATION, ActionType.CLARIFICATION)

    review = Review(
        request_id=req.id,
        reviewer=body.reviewer,
        decision=body.decision.value,
        corrected_fields_json=corrections,
        comment=body.comment,
    )
    db.add(review)
    if draft is not None:
        db.add(draft)
    req.status = target.value
    db.commit()
    return ReviewResponse(
        review=ReviewOut.model_validate(review),
        status=RequestStatus(req.status),
        draft=DraftOut.model_validate(draft) if draft else None,
    )


def _confirmed(db: Session, request_id: int, action: ActionType) -> ActionDraft | None:
    return db.scalars(
        select(ActionDraft)
        .where(
            ActionDraft.request_id == request_id,
            ActionDraft.action_type == action.value,
            ActionDraft.status == DraftStatus.CONFIRMED.value,
        )
        .order_by(ActionDraft.id.desc())
        .limit(1)
    ).first()


def confirm_registration(db: Session, request_id: int, confirmed_by: str) -> ConfirmResponse:
    req = get_request(db, request_id, for_update=True)
    done = _confirmed(db, req.id, ActionType.REGISTRATION)
    if done is not None:
        return ConfirmResponse(
            request_id=req.id, status=RequestStatus(req.status), draft=DraftOut.model_validate(done),
            registration_number=req.registration_number, already_confirmed=True,
        )

    draft = _pending(req, ActionType.REGISTRATION)
    if req.status != RequestStatus.READY_FOR_CONFIRMATION or draft is None:
        raise ConflictError(
            "Нет подготовленного черновика регистрации — сначала примите решение по карточке",
            code="DRAFT_NOT_READY",
            details={"status": req.status},
        )
    ensure_transition(req.status, RequestStatus.REGISTERED)
    draft.status = DraftStatus.CONFIRMED.value
    draft.confirmed_by = confirmed_by
    draft.confirmed_at = utcnow()
    req.registration_number = f"CF-{utcnow():%Y}-{req.id:06d}"
    req.status = RequestStatus.REGISTERED.value
    db.commit()
    return ConfirmResponse(
        request_id=req.id, status=RequestStatus.REGISTERED, draft=DraftOut.model_validate(draft),
        registration_number=req.registration_number, already_confirmed=False,
    )


def confirm_clarification(db: Session, request_id: int, confirmed_by: str) -> ConfirmResponse:
    req = get_request(db, request_id, for_update=True)
    draft = _pending(req, ActionType.CLARIFICATION)
    if draft is None:
        done = _confirmed(db, req.id, ActionType.CLARIFICATION)
        if done is not None and req.status == RequestStatus.NEEDS_CLARIFICATION:
            return ConfirmResponse(
                request_id=req.id, status=RequestStatus(req.status), draft=DraftOut.model_validate(done),
                already_confirmed=True,
            )
        raise ConflictError(
            "Нет подготовленного черновика уточнения — сначала выберите решение request_clarification",
            code="DRAFT_NOT_READY",
            details={"status": req.status},
        )
    # Демо: реальной отправки нет — фиксируем подтверждённое действие.
    draft.status = DraftStatus.CONFIRMED.value
    draft.confirmed_by = confirmed_by
    draft.confirmed_at = utcnow()
    db.commit()
    return ConfirmResponse(
        request_id=req.id, status=RequestStatus(req.status), draft=DraftOut.model_validate(draft),
        already_confirmed=False,
    )
