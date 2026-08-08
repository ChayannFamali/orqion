"""HTTP-роутеры, SSE-стриминг. Только валидация и вызов сервиса."""

from app.api.health import router as health_router

__all__ = ["health_router"]
