"""AI/NLP-слой. Выбор экстрактора — через настройку EXTRACTOR."""

from collections.abc import Callable

from app.ai.schemas import AnalysisResult

Extractor = Callable[[str], AnalysisResult]


def get_extractor(name: str) -> Extractor:
    if name == "rule_based":
        from app.ai.rule_based import extract

        return extract
    if name == "llm":
        # Модуль Участника 1: функция extract(text: str) -> AnalysisResult.
        from app.ai.extraction import extract  # type: ignore[import-not-found]

        return extract
    raise ValueError(f"Неизвестный экстрактор: {name}")
