"""Реестр инструментов: валидация аргументов, лимиты шагов/времени и трассировка каждого вызова."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.base import AgentError
from app.agent.state import AgentState
from app.core.config import Settings
from app.db.models import AgentRun, AgentTrace

log = logging.getLogger(__name__)


class AgentStepLimitError(AgentError):
    pass


class AgentTimeoutError(AgentError):
    pass


@dataclass
class ToolContext:
    db: Session
    settings: Settings
    state: AgentState


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    func: Callable[[ToolContext, Any], Any]


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: str | None = None


class ToolRegistry:
    def __init__(
        self, db: Session, run: AgentRun, state: AgentState, settings: Settings, tools: list[Tool]
    ) -> None:
        self.db = db
        self.run = run
        self.state = state
        self.settings = settings
        self._tools = {t.name: t for t in tools}
        self._ctx = ToolContext(db=db, settings=settings, state=state)
        self._started = time.monotonic()
        self.steps = 0
        self.agent_steps = 0

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        """Описание инструментов в формате function calling (для LLM)."""
        return [
            {"name": t.name, "description": t.description, "parameters": t.args_model.model_json_schema()}
            for t in self._tools.values()
        ]

    def _check_limits(self) -> None:
        if self.agent_steps >= self.settings.agent_max_steps:
            raise AgentStepLimitError(f"Превышен лимит шагов агента ({self.settings.agent_max_steps})")
        if time.monotonic() - self._started > self.settings.agent_timeout_s:
            raise AgentTimeoutError(f"Превышено время работы агента ({self.settings.agent_timeout_s} с)")

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        decision: str | None = None,
        token_usage: dict[str, Any] | None = None,
        system: bool = False,
    ) -> ToolResult:
        """Выполнить инструмент. system=True — служебный вызов backend (не считается в лимит агента)."""
        if not system:
            self._check_limits()
            self.agent_steps += 1
        self.steps += 1
        arguments = arguments or {}
        started = time.perf_counter()
        tool = self._tools.get(name)
        try:
            if tool is None:
                raise LookupError(f"Неизвестный инструмент: {name}. Доступны: {', '.join(self._tools)}")
            args = tool.args_model.model_validate(arguments)
            result = ToolResult(ok=True, data=to_jsonable_python(tool.func(self._ctx, args)))
        except SQLAlchemyError:
            raise
        except ValidationError as exc:
            result = ToolResult(ok=False, error=f"Некорректные аргументы: {exc.errors(include_url=False)}")
        except Exception as exc:  # ошибка инструмента возвращается агенту, а не роняет запуск
            log.warning("tool %s failed: %s", name, exc)
            result = ToolResult(ok=False, error=str(exc))
        duration_ms = int((time.perf_counter() - started) * 1000)

        self.db.add(
            AgentTrace(
                agent_run_id=self.run.id,
                step_no=self.steps,
                tool_name=name,
                arguments_json=to_jsonable_python(arguments),
                result_json=result.data if result.ok else None,
                decision=decision,
                error=result.error,
                duration_ms=duration_ms,
                token_usage=token_usage,
            )
        )
        self.db.flush()
        self.state.history.append(f"{self.steps}:{name}:{'ok' if result.ok else 'error'}")
        return result
