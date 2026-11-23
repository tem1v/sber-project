"""Инструменты агента. Каждый инструмент пишет результат в state и возвращает его агенту."""

from pydantic import BaseModel, Field

from app.agent.tools_registry import Tool, ToolContext
from app.ai import get_extractor
from app.ai.schemas import Topic
from app.core.enums import UrgencyLevel
from app.guardrails.validators import validate_route
from app.services.deadlines import calculate_deadline
from app.services.duplicates import find_similar
from app.services.routing import find_department_candidates


class NoArgs(BaseModel):
    pass


class SearchSimilarArgs(BaseModel):
    limit: int = Field(5, ge=1, le=20)


class FindDepartmentArgs(BaseModel):
    topic: str | None = Field(None, description="Тема; по умолчанию — темы из анализа")
    subtopic: str | None = None


class ValidateRouteArgs(BaseModel):
    department_id: int | None = Field(None, description="По умолчанию — выбранное ведомство")


class DeadlineArgs(BaseModel):
    rule_id: int | None = Field(None, description="По умолчанию — правило выбранного ведомства")


class FinishArgs(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def _require_analysis(ctx: ToolContext):
    if ctx.state.analysis is None:
        raise RuntimeError("Сначала вызовите analyze_text")
    return ctx.state.analysis


def analyze_text(ctx: ToolContext, _: NoArgs):
    ctx.state.analysis = get_extractor(ctx.settings.extractor)(ctx.state.text)
    return ctx.state.analysis


def search_similar(ctx: ToolContext, args: SearchSimilarArgs):
    analysis = ctx.state.analysis
    ctx.state.similar = find_similar(
        ctx.db,
        request_id=ctx.state.request_id,
        text=ctx.state.text,
        address=analysis.address.raw if analysis else None,
        topic=analysis.topics[0].name if analysis and analysis.topics else None,
        window_days=ctx.settings.duplicate_window_days,
        threshold=ctx.settings.duplicate_threshold,
        limit=args.limit,
    )
    return ctx.state.similar


def find_department(ctx: ToolContext, args: FindDepartmentArgs):
    if args.topic:
        topics = [Topic(name=args.topic, subtopic=args.subtopic, confidence=1.0)]
    else:
        topics = _require_analysis(ctx).topics
    candidates = find_department_candidates(ctx.db, topics)
    ctx.state.department_candidates = candidates
    ctx.state.chosen_department = candidates[0] if candidates else None
    return {"candidates": candidates, "chosen": ctx.state.chosen_department}


def validate_route_tool(ctx: ToolContext, args: ValidateRouteArgs):
    chosen = ctx.state.chosen_department
    department_id = args.department_id if args.department_id is not None else (chosen.department_id if chosen else None)
    ctx.state.validation = validate_route(
        ctx.db,
        text=ctx.state.text,
        analysis=ctx.state.analysis,
        department_id=department_id,
        topic=chosen.topic if chosen else None,
        confidence_threshold=ctx.settings.confidence_threshold,
        similar=ctx.state.similar,
    )
    return ctx.state.validation


def deadline_tool(ctx: ToolContext, args: DeadlineArgs):
    chosen = ctx.state.chosen_department
    analysis = ctx.state.analysis
    ctx.state.deadline = calculate_deadline(
        ctx.db,
        rule_id=args.rule_id if args.rule_id is not None else (chosen.rule_id if chosen else None),
        urgency_level=analysis.urgency.level if analysis else UrgencyLevel.NORMAL,
        received_at=ctx.state.received_at,
    )
    return ctx.state.deadline


def finish(ctx: ToolContext, args: FinishArgs):
    ctx.state.finished = True
    ctx.state.finish_reason = args.reason
    return {"finished": True, "reason": args.reason}


def build_default_tools() -> list[Tool]:
    return [
        Tool("analyze_text", "Извлечь суть, адрес, темы, срочность и недостающие сведения из текста обращения", NoArgs, analyze_text),
        Tool("search_similar", "Найти похожие обращения (кандидаты в дубликаты)", SearchSimilarArgs, search_similar),
        Tool("find_department", "Подобрать ведомство по справочнику зон ответственности", FindDepartmentArgs, find_department),
        Tool("validate_route", "Проверить маршрут guardrails: справочник, уверенность, срочность, противоречия", ValidateRouteArgs, validate_route_tool),
        Tool("calculate_deadline", "Рассчитать срок по демо-регламенту", DeadlineArgs, deadline_tool),
        Tool("finish", "Завершить анализ и передать карточку сотруднику", FinishArgs, finish),
    ]
