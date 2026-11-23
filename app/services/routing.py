"""Подбор ведомства по справочнику зон ответственности (routing_rules)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.state import DepartmentCandidate
from app.ai.schemas import Topic
from app.db.models import Department, RoutingRule


def find_rule(db: Session, topic: str, subtopic: str | None) -> RoutingRule | None:
    """Самое специфичное активное правило: сначала с подтемой, затем общее по теме."""
    stmt = (
        select(RoutingRule)
        .join(Department)
        .where(RoutingRule.active.is_(True), Department.active.is_(True), RoutingRule.topic == topic)
        .where((RoutingRule.subtopic == subtopic) | RoutingRule.subtopic.is_(None))
        .order_by(RoutingRule.subtopic.is_(None), RoutingRule.priority.desc(), RoutingRule.id)
        .limit(1)
    )
    return db.scalars(stmt).first()


def find_department_candidates(db: Session, topics: list[Topic]) -> list[DepartmentCandidate]:
    best: dict[int, DepartmentCandidate] = {}
    for t in topics:
        rule = find_rule(db, t.name, t.subtopic)
        if rule is None:
            continue
        dept = rule.department
        label = t.name + (f" / {t.subtopic}" if t.subtopic else "")
        cand = DepartmentCandidate(
            department_id=dept.id,
            department_code=dept.code,
            department_name=dept.name,
            rule_id=rule.id,
            topic=t.name,
            subtopic=t.subtopic,
            score=t.confidence,
            deadline_days=rule.deadline_days,
            explanation=f"Тема «{label}» по правилу #{rule.id} закреплена за «{dept.name}»",
        )
        if dept.id not in best or best[dept.id].score < cand.score:
            best[dept.id] = cand
    return sorted(best.values(), key=lambda c: c.score, reverse=True)


def rule_links_topic_to_department(db: Session, topic: str | None, department_id: int) -> bool:
    if not topic:
        return False
    stmt = select(RoutingRule.id).where(
        RoutingRule.active.is_(True), RoutingRule.topic == topic, RoutingRule.department_id == department_id
    )
    return db.scalars(stmt).first() is not None
