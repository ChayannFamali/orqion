"""Роутеры API, сгруппированные по доменам."""

from app.api.routes.analytics import router as analytics_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.corpora import router as corpora_router
from app.api.routes.documents import document_router
from app.api.routes.documents import router as documents_router
from app.api.routes.eval import router as eval_router
from app.api.routes.models import router as models_router
from app.api.routes.providers import router as providers_router
from app.api.routes.roles import router as roles_router
from app.api.routes.routing import router as routing_router
from app.api.routes.traces import router as traces_router
from app.api.routes.users import router as users_router

__all__ = [
    "analytics_router",
    "auth_router",
    "chat_router",
    "conversations_router",
    "corpora_router",
    "document_router",
    "documents_router",
    "eval_router",
    "models_router",
    "providers_router",
    "roles_router",
    "routing_router",
    "traces_router",
    "users_router",
]
