"""POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me, POST /api/auth/exit-impersonation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import LoginRequest, LoginResponse, UserResponse
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.auth.local_provider import LocalIdentityProvider
from app.auth.rate_limit import LoginRateLimiter
from app.auth.sessions import (
    COOKIE_NAME,
    create_session,
    get_session_record,
    invalidate_session,
)
from app.config import Settings
from app.db.models import User
from app.db.session import get_session
from app.errors import BadRequest, OrqionError
from app.policy.resolve import resolve_policy

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_settings() -> Settings:
    return Settings()


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
    request: Request,
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    workspace_id = request.app.state.workspace_id
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

    provider = LocalIdentityProvider(session)
    auth_result = await provider.authenticate(
        credentials={"email": body.email, "password": body.password}
    )
    user = auth_result.user

    limiter.reset(body.email, ip)

    session_id = await create_session(session, user.id, workspace_id, settings)
    await session.commit()

    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.session_cookie_secure,
    )
    policy = await resolve_policy(session, user)
    return LoginResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            is_active=user.is_active,
            capabilities=policy.capabilities,
        )
    )


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
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    policy = await resolve_policy(session, user)

    is_impersonating = False
    impersonated_by_email: str | None = None

    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        current_session = await get_session_record(session, session_id)
        if current_session is not None and current_session.impersonated_by is not None:
            # Это сессия имперсонации — находим actor по родительской сессии
            parent_session = await get_session_record(session, current_session.impersonated_by)
            if parent_session is not None:
                actor_result = await session.execute(
                    select(User).where(User.id == parent_session.user_id)
                )
                actor = actor_result.scalar_one_or_none()
                if actor is not None:
                    is_impersonating = True
                    impersonated_by_email = actor.email

    return UserResponse(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        capabilities=policy.capabilities,
        is_impersonating=is_impersonating,
        impersonated_by_email=impersonated_by_email,
    )


@router.post("/exit-impersonation")
async def exit_impersonation(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Выход из имперсонации: восстанавливает родительскую сессию.

    Если родительская сессия истекла или удалена — полный logout.
    """
    workspace_id = request.app.state.workspace_id
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        raise OrqionError(
            "Нет активной сессии",
            hint="Войдите в систему",
        )

    current_session = await get_session_record(session, session_id)
    if current_session is None or current_session.impersonated_by is None:
        raise BadRequest(
            "Текущая сессия не является имперсонацией",
            hint="Вы не в режиме имперсонации",
        )

    parent_session_id = current_session.impersonated_by

    # Инвалидируем текущую (имперсонационную) сессию
    await invalidate_session(session, session_id)

    # Audit: impersonate.exit (T-317 — пробел, обнаруженный при исследовании)
    actor_result = await session.execute(select(User).where(User.id == current_session.user_id))
    actor_user = actor_result.scalar_one_or_none()
    if actor_user is not None:
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor_user.id,
            action="impersonate.exit",
            object_type="user",
            object_id=actor_user.id,
            meta={},
        )

    # Проверяем родительскую сессию
    from datetime import UTC, datetime

    parent_session = await get_session_record(session, parent_session_id)
    now = datetime.now(UTC)
    parent_expired = (
        parent_session is None
        or (
            parent_session.expires_at.tzinfo is None
            and parent_session.expires_at.replace(tzinfo=UTC) <= now
        )
        or (parent_session.expires_at.tzinfo is not None and parent_session.expires_at <= now)
    )
    if parent_expired:
        # Родительская сессия истекла или удалена — полный logout
        response.delete_cookie(key=COOKIE_NAME, path="/")
        await session.commit()
        return {"status": "logged_out", "reason": "parent_session_expired"}

    # Восстанавливаем родительскую сессию
    await session.commit()
    response.set_cookie(
        key=COOKIE_NAME,
        value=parent_session_id,
        httponly=True,
        samesite="lax",
        path="/",
        secure=Settings().session_cookie_secure,
    )
    return {"status": "restored"}


# ---------------------------------------------------------------------------
# OIDC endpoints (T-404b)
# ---------------------------------------------------------------------------

OIDC_STATE_COOKIE = "orqion_oidc_state"
OIDC_VERIFIER_COOKIE = "orqion_oidc_verifier"
_OIDC_COOKIE_MAX_AGE = 300  # 5 минут


@router.get("/oidc/login")
async def oidc_login(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """GET /api/auth/oidc/login → redirect URL к IdP.

    Устанавливает state + code_verifier в подписанных HttpOnly cookies (5min TTL).
    """
    if not settings.oidc_enabled:
        raise BadRequest(
            "OIDC отключён",
            hint="Установите ORQION_OIDC_ENABLED=true и orqion[oidc]",
        )

    from app.auth.oidc_provider import (
        OidcIdentityProvider,
        _generate_pkce,
        _generate_state,
        _sign_state_cookie,
    )

    workspace_id = request.app.state.workspace_id
    provider = OidcIdentityProvider(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )
    discovery = await provider._fetch_discovery()

    state = _generate_state()
    verifier, challenge = _generate_pkce()
    secret_key = settings.secret_key or "fallback"

    signed_state = _sign_state_cookie(state, secret_key)
    signed_verifier = _sign_state_cookie(verifier, secret_key)

    authorize_url = provider.build_authorize_url(discovery, state, challenge)

    response.set_cookie(
        key=OIDC_STATE_COOKIE,
        value=signed_state,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.session_cookie_secure,
        max_age=_OIDC_COOKIE_MAX_AGE,
    )
    response.set_cookie(
        key=OIDC_VERIFIER_COOKIE,
        value=signed_verifier,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.session_cookie_secure,
        max_age=_OIDC_COOKIE_MAX_AGE,
    )
    return {"authorize_url": authorize_url}


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    response: Response,
    code: str = "",
    state: str = "",
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """GET /api/auth/oidc/callback → обмен code на tokens, создание сессии.

    Валидирует state из cookie, обменивает code через OidcIdentityProvider.
    """
    if not settings.oidc_enabled:
        raise BadRequest(
            "OIDC отключён",
            hint="Установите ORQION_OIDC_ENABLED=true и orqion[oidc]",
        )

    from app.auth.oidc_provider import (
        OidcError,
        OidcIdentityProvider,
        _verify_state_cookie,
    )

    # Валидация state (CSRF protection)
    signed_state = request.cookies.get(OIDC_STATE_COOKIE, "")
    signed_verifier = request.cookies.get(OIDC_VERIFIER_COOKIE, "")
    secret_key = settings.secret_key or "fallback"

    cookie_state = _verify_state_cookie(signed_state, secret_key)
    if cookie_state is None or cookie_state != state:
        raise OidcError("State mismatch — возможная CSRF-атака")

    code_verifier = _verify_state_cookie(signed_verifier, secret_key)
    if code_verifier is None:
        raise OidcError("code_verifier не найден или подпись неверна")

    workspace_id = request.app.state.workspace_id
    provider = OidcIdentityProvider(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
    )

    auth_result = await provider.authenticate(
        credentials={
            "code": code,
            "code_verifier": code_verifier,
            "state": state,
        }
    )
    user = auth_result.user

    # Очистка OIDC cookies
    response.delete_cookie(key=OIDC_STATE_COOKIE, path="/")
    response.delete_cookie(key=OIDC_VERIFIER_COOKIE, path="/")

    # Создание сессии
    session_id = await create_session(session, user.id, workspace_id, settings)
    await session.commit()

    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.session_cookie_secure,
    )
    return {"status": "ok", "email": user.email}
