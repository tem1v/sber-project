"""Интерфейс агента. Реализация с LLM (Участник 2) должна соответствовать протоколу Agent."""

from typing import TYPE_CHECKING, Protocol

from app.agent.state import AgentState

if TYPE_CHECKING:
    from app.agent.tools_registry import ToolRegistry


class AgentError(RuntimeError):
    """Агент не смог завершить работу."""


class AgentUnavailableError(AgentError):
    """Внешняя модель (LLM) недоступна: таймаут, 5xx, нет ключа и т.п."""


class Agent(Protocol):
    name: str

    def run(self, state: AgentState, tools: "ToolRegistry") -> AgentState:
        """Цикл: состояние → выбор инструмента → tools.call(...) → новое решение.

        Инструменты сами записывают результат в state и в трассировку.
        Описание инструментов для LLM (JSON Schema) — tools.specs().
        """
        ...


def build_agent(mode: str) -> Agent:
    if mode == "rule_based":
        from app.agent.rule_based_agent import RuleBasedAgent

        return RuleBasedAgent()
    if mode == "llm":
        from app.agent.agent import LLMAgent  # type: ignore[import-not-found]  # модуль Участника 2

        return LLMAgent()
    raise ValueError(f"Неизвестный режим агента: {mode}")
