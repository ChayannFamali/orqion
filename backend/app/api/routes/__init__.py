"""Роутеры API, сгруппированные по доменам."""

from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.providers import router as providers_router

__all__ = ["auth_router", "conversations_router", "providers_router"]
