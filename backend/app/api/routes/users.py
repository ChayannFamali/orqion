"""GET /api/users, GET /api/users/{id}, PATCH /api/users/{id}, POST /api/users/{id}/impersonate.

Access control: только admin (через "*" в capabilities).
Non-admin → 404 (прецедент T-308/T-310).
audit_log: user.role_changed, user.status_changed.
Self-edit блок: любой PATCH на собственный id → 400.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.user import (
    UserDetailResponse,
    UserListItem,
    UserListResponse,
    UserUpdate,
)
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.auth.impersonate import impersonate
from app.auth.sessions import COOKIE_NAME
from app.config import Settings
from app.db.models import Role, User
from app.db.session import get_session
from app.errors import BadRequest, NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(current_user)],
)


async def _check_admin(session: AsyncSession, user: User) -> bool:
    """True если admin (через *). Иначе — NotFound."""
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities


async def _get_user_with_role(
    session: AsyncSession, workspace_id: str, user_id: str
) -> tuple[User, Role] | None:
    result = await session.execute(
        select(User, Role)
        .join(Role, User.role_id == Role.id)
        .where(User.id == user_id, User.workspace_id == workspace_id)
    )
    row = result.first()
    if row is None:
        return None
    return row[0], row[1]


def _to_list_item(user: User, role: Role) -> UserListItem:
    return UserListItem(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        role_id=role.id,
        role_name=role.name,
        is_builtin_role=role.is_builtin,
    )


def _to_detail_response(user: User, role: Role) -> UserDetailResponse:
    return UserDetailResponse(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        role_id=role.id,
        role_name=role.name,
        is_builtin_role=role.is_builtin,
    )


@router.get("", response_model=UserListResponse)
async def list_users(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> UserListResponse:
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "users", "reason": "admin required"},
            hint="Нет права на управление пользователями",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(User, Role)
        .join(Role, User.role_id == Role.id)
        .where(User.workspace_id == workspace_id)
        .order_by(User.created_at.desc())
    )
    rows = result.all()
    return UserListResponse(users=[_to_list_item(u, r) for u, r in rows])


@router.get("/{user_id}", response_model=UserDetailResponse)
async def get_user(
    user_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> UserDetailResponse:
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "users", "reason": "admin required"},
            hint="Нет права на управление пользователями",
        )

    workspace_id = request.app.state.workspace_id
    pair = await _get_user_with_role(session, workspace_id, user_id)
    if pair is None:
        raise NotFound(
            constraint={"object": "user", "id": user_id},
            hint="Пользователь не найден",
        )
    return _to_detail_response(*pair)


@router.patch("/{user_id}", response_model=UserDetailResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> UserDetailResponse:
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "users", "reason": "admin required"},
            hint="Нет права на управление пользователями",
        )

    # Self-edit блок: любой PATCH на собственный id → 400
    if user_id == user.id:
        raise BadRequest(
            "Нельзя редактировать собственную учётную запись",
            hint="Используйте другой аккаунт администратора для изменений",
        )

    workspace_id = request.app.state.workspace_id
    pair = await _get_user_with_role(session, workspace_id, user_id)
    if pair is None:
        raise NotFound(
            constraint={"object": "user", "id": user_id},
            hint="Пользователь не найден",
        )
    target, target_role = pair

    # Смена роли
    if body.role_id is not None and body.role_id != target.role_id:
        # Проверяем что новая роль существует в этом workspace
        new_role_result = await session.execute(
            select(Role).where(Role.id == body.role_id, Role.workspace_id == workspace_id)
        )
        new_role = new_role_result.scalar_one_or_none()
        if new_role is None:
            raise NotFound(
                constraint={"object": "role", "id": body.role_id},
                hint="Роль не найдена в workspace",
            )

        old_role_id = target.role_id
        target.role_id = body.role_id
        target_role = new_role

        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_user_id=user.id,
            action="user.role_changed",
            object_type="user",
            object_id=target.id,
            meta={
                "old_role_id": old_role_id,
                "new_role_id": body.role_id,
                "target_email": target.email,
            },
        )

    # Смена is_active
    if body.is_active is not None and body.is_active != target.is_active:
        old_active = target.is_active
        target.is_active = body.is_active

        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_user_id=user.id,
            action="user.status_changed",
            object_type="user",
            object_id=target.id,
            meta={
                "old": old_active,
                "new": body.is_active,
                "target_email": target.email,
            },
        )

    await session.commit()
    await session.refresh(target)

    return _to_detail_response(target, target_role)


@router.post("/{user_id}/impersonate")
async def impersonate_user(
    user_id: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    settings: Settings = Depends(lambda: Settings()),
) -> dict[str, str]:
    """Вход от имени пользователя. Создаёт новую сессию с impersonated_by."""
    # Проверка вложенной имперсонации — перед _check_admin, т.к. после имперсонации
    # current_user = target (не admin), и _check_admin вернёт 404 раньше nested-проверки
    actor_session_id = request.cookies.get(COOKIE_NAME)
    if actor_session_id is not None:
        from app.auth.sessions import get_session_record

        current_session_record = await get_session_record(session, actor_session_id)
        if (
            current_session_record is not None
            and current_session_record.impersonated_by is not None
        ):
            raise BadRequest(
                "Невозможно начать новую имперсонацию, не завершив текущую",
                hint="Сначала выйдите из текущей имперсонации",
            )

    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "users", "reason": "admin required"},
            hint="Нет права на управление пользователями",
        )

    workspace_id = request.app.state.workspace_id

    session_id = await impersonate(
        session,
        actor=user,
        target_user_id=user_id,
        workspace_id=workspace_id,
        settings=settings,
        actor_session_id=actor_session_id,
    )
    await session.commit()

    response.delete_cookie(key=COOKIE_NAME, path="/")
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.session_cookie_secure,
    )
    return {"status": "impersonating"}
