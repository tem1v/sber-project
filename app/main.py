import logging

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.routes import reference, requests
from app.core.config import get_settings

__version__ = "0.1.0"


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(
        title="CivicFlow API",
        version=__version__,
        description=(
            "Маршрутизация обращений граждан. Анализ выполняет агент, но регистрация и отправка "
            "уточнений — только после явного подтверждения сотрудника."
        ),
    )
    register_error_handlers(app)
    app.include_router(reference.health_router)
    app.include_router(requests.router, prefix="/api/v1")
    app.include_router(reference.router, prefix="/api/v1")
    return app


app = create_app()
