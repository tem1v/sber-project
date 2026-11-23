"""Конфигурация приложения. Все значения читаются из переменных окружения / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "CivicFlow"
    log_level: str = "INFO"

    # БД: PostgreSQL в docker-compose, SQLite — для локального прототипа и тестов.
    database_url: str = "sqlite:///./civicflow.db"

    # Агент и извлечение. rule_based — встроенная заглушка без LLM,
    # llm — реализации участников 1 и 2 (app.ai.extraction / app.agent.agent).
    agent_mode: str = "rule_based"
    extractor: str = "rule_based"
    agent_max_steps: int = 12
    agent_timeout_s: float = 60.0

    # Пороги guardrails / поиска дублей.
    confidence_threshold: float = 0.6
    duplicate_threshold: float = 0.5
    duplicate_window_days: int = 30

    demo_user: str = "demo-user"


@lru_cache
def get_settings() -> Settings:
    return Settings()
