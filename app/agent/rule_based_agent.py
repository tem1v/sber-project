"""Агент-заглушка без LLM: выбирает следующий шаг по результатам предыдущих.

Нужен для демо и тестов backend. Настоящий агентный цикл с выбором инструмента
моделью — app/agent/agent.py (Участник 2), включается AGENT_MODE=llm.
"""

from app.agent.base import AgentError
from app.agent.state import AgentState
from app.agent.tools_registry import ToolRegistry
from app.core.enums import URGENT_LEVELS


class RuleBasedAgent:
    name = "rule_based"

    def run(self, state: AgentState, tools: ToolRegistry) -> AgentState:
        res = tools.call("analyze_text", decision="Начинаю с извлечения полей из текста")
        if not res.ok or state.analysis is None:
            raise AgentError(f"Анализ текста не удался: {res.error}")
        analysis = state.analysis

        if not analysis.topics:
            tools.call("validate_route", decision="Тема не определена — проверяю и останавливаюсь")
            if analysis.urgency.level in URGENT_LEVELS:
                tools.call("calculate_deadline", decision="Есть признаки срочности — считаю сокращённый срок")
            tools.call("finish", {"reason": "Недостаточно сведений для маршрутизации — нужно уточнение"})
            return state

        tools.call("search_similar", {"limit": 5}, decision="Тема определена — ищу похожие обращения")
        tools.call("find_department", decision="Подбираю ведомство по справочнику")
        tools.call("validate_route", decision="Проверяю предложенный маршрут")

        if state.validation and state.validation.decision == "escalate":
            tools.call("calculate_deadline", decision="Срочный случай — считаю сокращённый срок")
            tools.call("finish", {"reason": "Признаки срочности — передаю на эскалацию"})
            return state

        if state.chosen_department is not None:
            tools.call("calculate_deadline", decision="Ведомство найдено — рассчитываю срок")
        tools.call("finish", {"reason": "Карточка подготовлена для проверки сотрудником"})
        return state
