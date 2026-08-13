"""GET /api/roles, GET /api/roles/{id}, POST /api/roles, PATCH /api/roles/{id}.

Access control: только admin (через "*" в capabilities).
Non-admin → 404 (не раскрываем существование), по прецеденту T-308.
audit_log: запись при создании роли и изменении политики.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.role import (
    RoleCreate,
    RoleListResponse,
    RoleResponse,
    RoleUpdate,
)
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.db.models import Role, User
from app.db.session import get_session
from app.errors import BadRequest, NotFound
from app.policy.models import WILDCARD, Policy
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/roles",
    tags=["roles"],
    dependencies=[Depends(current_user)],
)


async def _check_admin(session: AsyncSession, user: User) -> bool:
    """True если admin (через *). Иначе — NotFound."""
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities


def _role_to_response(role: Role) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        name=role.name,
        is_builtin=role.is_builtin,
        policy=role.policy,
    )


@router.get("", response_model=RoleListResponse)
async def list_roles(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> RoleListResponse:
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "roles", "reason": "admin required"},
            hint="Нет права на управление ролями",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Role)
        .where(Role.workspace_id == workspace_id)
        .order_by(Role.is_builtin.desc(), Role.name)
    )
    roles = result.scalars().all()
    return RoleListResponse(roles=[_role_to_response(r) for r in roles])


@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> RoleResponse:
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "roles", "reason": "admin required"},
            hint="Нет права на управление ролями",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Role).where(Role.id == role_id, Role.workspace_id == workspace_id)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise NotFound(
            constraint={"object": "role", "id": role_id},
            hint="Роль не найдена",
        )
    return _role_to_response(role)


@router.post("", response_model=RoleResponse, status_code=201)
async def create_role(
    body: RoleCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> RoleResponse:
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "roles", "reason": "admin required"},
            hint="Нет права на управление ролями",
        )

    # Валидация политики через Pydantic
    try:
        policy = Policy.model_validate(body.policy)
    except ValidationError as exc:
        errors = exc.errors()
        loc = ".".join(str(p) for p in errors[0]["loc"]) if errors else "policy"
        raise BadRequest(
            f"Политика некорректна: поле '{loc}'",
            hint=errors[0]["msg"] if errors else "Проверьте структуру политики",
        ) from exc

    workspace_id = request.app.state.workspace_id

    # Проверка дубликата имени до insert
    existing = await session.execute(
        select(Role).where(
            Role.workspace_id == workspace_id,
            Role.name == body.name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise BadRequest(
            "Имя роли должно быть уникально в рамках workspace",
            hint=f"Имя '{body.name}' уже существует",
        )

    role = Role(
        workspace_id=workspace_id,
        name=body.name,
        is_builtin=False,  # всегда False для API-созданных ролей
        policy=policy.model_dump(),
    )
    session.add(role)
    await session.flush()

    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        action="role.created",
        object_type="role",
        object_id=role.id,
        meta={"name": body.name, "policy": policy.model_dump()},
    )
    await session.commit()
    await session.refresh(role)

    return _role_to_response(role)


@router.patch("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: str,
    body: RoleUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> RoleResponse:
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "roles", "reason": "admin required"},
            hint="Нет права на управление ролями",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Role).where(Role.id == role_id, Role.workspace_id == workspace_id)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise NotFound(
            constraint={"object": "role", "id": role_id},
            hint="Роль не найдена",
        )

    # Валидация политики через Pydantic
    try:
        policy = Policy.model_validate(body.policy)
    except ValidationError as exc:
        errors = exc.errors()
        loc = ".".join(str(p) for p in errors[0]["loc"]) if errors else "policy"
        raise BadRequest(
            f"Политика некорректна: поле '{loc}'",
            hint=errors[0]["msg"] if errors else "Проверьте структуру политики",
        ) from exc

    old_policy: dict[str, Any] = dict(role.policy)
    role.policy = policy.model_dump()

    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        action="role.policy_changed",
        object_type="role",
        object_id=role.id,
        meta={"old": old_policy, "new": policy.model_dump()},
    )
    await session.commit()
    await session.refresh(role)

    return _role_to_response(role)
