"""Запуск агента и сохранение результатов. Анализ НИКОГДА не регистрирует обращение."""

import logging

from pydantic_core import to_jsonable_python
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.base import Agent, AgentError, AgentUnavailableError
from app.agent.state import AgentState, ValidationResult
from app.agent.tools import build_default_tools
from app.agent.tools_registry import ToolRegistry
from app.api.schemas import AnalysisOut, AnalyzeResponse, RouteCheck
from app.core.config import Settings
from app.core.enums import DraftStatus, LinkStatus, LinkType, RequestStatus, RunStatus
from app.core.errors import ApiError, ServiceUnavailableError
from app.core.taxonomy import ensure_aware, utcnow
from app.db.models import ActionDraft, AgentRun, Deadline, Request, RequestAnalysis, RequestLink
from app.services.requests import get_request
from app.services.statuses import ensure_transition

log = logging.getLogger(__name__)


def decide_status(state: AgentState, validation: ValidationResult) -> RequestStatus:
    if validation.decision == "escalate":
        return RequestStatus.URGENT_ESCALATION
    if validation.decision == "clarify":
        return RequestStatus.NEEDS_CLARIFICATION
    if state.similar:
        return RequestStatus.DUPLICATE_CANDIDATE
    # Даже при decision == "ok" решение принимает сотрудник.
    return RequestStatus.NEEDS_REVIEW


def _final_check(registry: ToolRegistry) -> ValidationResult:
    """Backend не доверяет выводу агента: guardrails прогоняются ещё раз в любом случае."""
    res = registry.call("validate_route", decision="Финальная проверка backend (guardrails)", system=True)
    if not res.ok or registry.state.validation is None:
        raise AgentError(f"Финальная проверка не выполнена: {res.error}")
    return registry.state.validation


def _persist(db: Session, req: Request, run: AgentRun, state: AgentState) -> RequestAnalysis:
    analysis = state.analysis
    assert analysis is not None
    version = (db.scalar(select(func.max(RequestAnalysis.version)).where(RequestAnalysis.request_id == req.id)) or 0) + 1
    row = RequestAnalysis(
        request_id=req.id,
        agent_run_id=run.id,
        version=version,
        summary=analysis.summary,
        extracted_json=analysis.model_dump(mode="json"),
        topics_json=[t.model_dump(mode="json") for t in analysis.topics],
        urgency_level=analysis.urgency.level.value,
        requires_human_review=analysis.urgency.requires_human_review,
        confidence=analysis.overall_confidence,
        model_version=analysis.model_version,
        prompt_version=analysis.prompt_version,
    )
    db.add(row)

    chosen = state.chosen_department
    first_topic = analysis.topics[0] if analysis.topics else None
    req.summary = analysis.summary
    req.address = analysis.address.raw
    req.topic = chosen.topic if chosen else (first_topic.name if first_topic else None)
    req.subtopic = chosen.subtopic if chosen else (first_topic.subtopic if first_topic else None)
    req.priority = analysis.urgency.level.value
    req.department_id = chosen.department_id if chosen else None

    existing = {
        link.related_request_id: link
        for link in db.scalars(select(RequestLink).where(RequestLink.request_id == req.id))
    }
    reverse = set(db.scalars(select(RequestLink.request_id).where(RequestLink.related_request_id == req.id)))
    for cand in state.similar:
        if cand.request_id in reverse:
            continue  # связь уже есть с другой стороны
        link = existing.get(cand.request_id)
        if link is None:
            db.add(
                RequestLink(
                    request_id=req.id,
                    related_request_id=cand.request_id,
                    similarity_score=cand.similarity,
                    link_type=LinkType.DUPLICATE.value,
                    status=LinkStatus.CANDIDATE.value,
                )
            )
        elif link.status == LinkStatus.CANDIDATE:
            link.similarity_score = cand.similarity

    if state.deadline:
        db.add(
            Deadline(
                request_id=req.id,
                rule_id=state.deadline.rule_id,
                due_at=state.deadline.due_at,
                calculation_note=state.deadline.note,
            )
        )

    # Повторный анализ отменяет неподтверждённые черновики.
    for draft in req.drafts:
        if draft.status == DraftStatus.PENDING:
            draft.status = DraftStatus.CANCELLED.value
    return row


def _mark_failed(db: Session, request_id: int, run_id: int, error: str, state: AgentState | None) -> None:
    req = db.get(Request, request_id)
    run = db.get(AgentRun, run_id)
    if req is not None and req.status == RequestStatus.ANALYZING:
        req.status = RequestStatus.FAILED.value
    if run is not None:
        run.status = RunStatus.FAILED.value
        run.error = error[:2000]
        run.finished_at = utcnow()
        if state is not None:
            run.state_json = to_jsonable_python(state.model_dump(mode="json"))
    db.commit()


def run_analysis(db: Session, request_id: int, agent: Agent, settings: Settings) -> AnalyzeResponse:
    req = get_request(db, request_id, for_update=True)
    ensure_transition(req.status, RequestStatus.ANALYZING)
    req.status = RequestStatus.ANALYZING.value
    run = AgentRun(request_id=req.id, status=RunStatus.RUNNING.value, agent_mode=agent.name)
    db.add(run)
    db.commit()
    run_id = run.id

    state = AgentState(
        request_id=req.id,
        run_id=run.id,
        text=req.raw_text,
        channel=req.source_channel,
        received_at=ensure_aware(req.received_at),
    )
    registry = ToolRegistry(db, run, state, settings, build_default_tools())
    try:
        state = agent.run(state, registry) or state
        registry.state = state
        if state.analysis is None:
            raise AgentError("Агент завершился без результата анализа")
        validation = _final_check(registry)
        row = _persist(db, req, run, state)
        new_status = decide_status(state, validation)
        ensure_transition(RequestStatus.ANALYZING, new_status)
        req.status = new_status.value
        run.status = RunStatus.SUCCEEDED.value
        run.finished_at = utcnow()
        run.steps_count = registry.steps
        run.state_json = state.model_dump(mode="json")
        db.commit()
    except SQLAlchemyError as exc:
        log.exception("DB error during analysis of request %s", request_id)
        db.rollback()
        try:
            _mark_failed(db, request_id, run_id, "Ошибка БД во время анализа", None)
        except SQLAlchemyError:
            db.rollback()
        raise ServiceUnavailableError("База данных недоступна", code="DB_UNAVAILABLE") from exc
    except AgentUnavailableError as exc:
        _mark_failed(db, request_id, run_id, str(exc), state)
        raise ServiceUnavailableError(
            "Модель недоступна, обращение переведено в статус failed — повторите анализ позже",
            code="LLM_UNAVAILABLE",
            details={"run_id": run_id},
        ) from exc
    except Exception as exc:
        log.exception("Agent failed for request %s", request_id)
        _mark_failed(db, request_id, run_id, str(exc), state)
        raise ApiError(
            "Агент не смог завершить анализ", code="AGENT_FAILED", status_code=500,
            details={"run_id": run_id, "error": str(exc)[:500]},
        ) from exc

    return AnalyzeResponse(
        request_id=req.id,
        run_id=run.id,
        run_status=RunStatus.SUCCEEDED,
        status=RequestStatus(req.status),
        analysis=AnalysisOut.model_validate(row),
        route_check=RouteCheck.model_validate(validation.model_dump()),
        similar_count=len(state.similar),
    )
