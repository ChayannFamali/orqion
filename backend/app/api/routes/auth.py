"""POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import LoginRequest, LoginResponse, UserResponse
from app.auth.dependencies import current_user
from app.auth.passwords import verify_password
from app.auth.rate_limit import LoginRateLimiter
from app.auth.sessions import COOKIE_NAME, create_session, invalidate_session
from app.config import Settings
from app.db.models import User
from app.db.session import get_session
from app.errors import OrqionError

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_settings() -> Settings:
    return Settings()


class InvalidCredentials(OrqionError):
    error_code = "invalid_credentials"
    reason = "Неверный email или пароль"
    status_code = 401


class LoginRateLimited(OrqionError):
    error_code = "login_rate_limited"
    reason = "Слишком много попыток входа"
    status_code = 429


def _get_client_ip(request: Request) -> str:
    """Извлекает IP клиента. Учитывает X-Forwarded-For (один прокси)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    ip = _get_client_ip(request)
    limiter: LoginRateLimiter = request.app.state.login_rate_limiter

    reset_in = limiter.check(body.email, ip)
    if reset_in is not None:
        raise LoginRateLimited(
            constraint={
                "max_attempts": settings.login_max_attempts,
                "period_seconds": settings.login_rate_period_seconds,
                "reset_in_seconds": round(reset_in, 1),
            },
            hint=f"Попробуйте через {reset_in:.0f} секунд",
        )

    result = await session.execute(
        select(User).where(User.email == body.email, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, body.password):
        raise InvalidCredentials()

    limiter.reset(body.email, ip)

    session_id = await create_session(session, user.id, user.workspace_id, settings)
    await session.commit()

    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.session_cookie_secure,
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
