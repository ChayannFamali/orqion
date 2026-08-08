"""POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import LoginRequest, LoginResponse, UserResponse
from app.auth.dependencies import current_user
from app.auth.passwords import verify_password
from app.auth.sessions import COOKIE_NAME, create_session, invalidate_session
from app.db.models import User
from app.db.session import get_session
from app.errors import OrqionError

router = APIRouter(prefix="/api/auth", tags=["auth"])


class InvalidCredentials(OrqionError):
    error_code = "invalid_credentials"
    reason = "Неверный email или пароль"
    status_code = 401


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    result = await session.execute(
        select(User).where(User.email == body.email, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, body.password):
        raise InvalidCredentials()

    session_id = await create_session(session, user.id, user.workspace_id)
    await session.commit()

    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return LoginResponse(user=UserResponse(id=user.id, email=user.email, is_active=user.is_active))


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> None:
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        await invalidate_session(session, session_id)
        await session.commit()
    response.delete_cookie(key=COOKIE_NAME, path="/")


@router.get("/me", response_model=UserResponse)
async def me(
    user: User = Depends(current_user),
) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, is_active=user.is_active)
