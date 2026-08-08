"""Зависимость current_user: cookie → сессия → пользователь."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sessions import COOKIE_NAME
from app.db.models import User
from app.db.session import get_session
from app.errors import OrqionError


class AuthenticationRequired(OrqionError):
    error_code = "authentication_required"
    reason = "Требуется аутентификация"
    status_code = 401
    hint = "Войдите в систему"


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """Возвращает пользователя из cookie сессии или выбрасывает 401."""
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        raise AuthenticationRequired()

    from app.auth.sessions import get_user_by_session

    user = await get_user_by_session(session, session_id)
    if user is None:
        raise AuthenticationRequired()
    return user
