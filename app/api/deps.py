from app.agent.base import Agent, build_agent
from app.core.config import Settings, get_settings


def get_settings_dep() -> Settings:
    return get_settings()


def get_agent() -> Agent:
    """Переопределяется в тестах (app.dependency_overrides)."""
    return build_agent(get_settings().agent_mode)
