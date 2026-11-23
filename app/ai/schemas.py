"""Контракт результата анализа текста (ТЗ, п. 5.3).

Владелец контракта — Участник 1 (LLM/NLP). Backend и агент принимают только
данные, прошедшие валидацию этой схемой. Изменения — по согласованию с командой.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import UrgencyLevel


class Address(BaseModel):
    raw: str | None = Field(None, description="Адрес/ориентир дословно из текста")
    normalized: str | None = None
    confidence: float = Field(0.0, ge=0, le=1)


class Topic(BaseModel):
    name: str = Field(min_length=1)
    subtopic: str | None = None
    confidence: float = Field(ge=0, le=1)


class Urgency(BaseModel):
    level: UrgencyLevel = UrgencyLevel.NORMAL
    risk_signals: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str | None = None
    address: Address = Field(default_factory=Address)
    topics: list[Topic] = Field(default_factory=list)
    urgency: Urgency = Field(default_factory=Urgency)
    missing_information: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list, description="Фрагменты исходного текста-основания")
    overall_confidence: float = Field(0.0, ge=0, le=1)
    model_version: str = "unknown"
    prompt_version: str | None = None
