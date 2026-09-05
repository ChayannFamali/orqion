"""CRUD скиллов — пакетов конфигурации агентного прогона (Т-508).

Два списка по образцу уже существующего разделения в проекте:

- ``GET /api/skills`` — админский каталог (способность ``manage_skills``;
  без права 404, существование раздела не раскрывается). Возвращает все
  строки рабочей области, включая выключенные, полными полями — иначе
  выключенный скилл нельзя включить обратно (решение 7);
- ``GET /api/skills/available`` — список для выбора в диалоге: всем
  аутентифицированным, только включённые и только поля, нужные для выбора
  (решение 4; паттерн ``GET /api/models`` — «доступные текущему
  пользователю»).

Записи — только с ``manage_skills`` (wildcard-only, не в посевных
пресетах; паттерн ``manage_mcp_servers`` дословно).

Аудит ``agent_skill.changed`` пишется со старым и новым значением полей,
**без текста промпта** — только его длина: ADR-21 пункт 2 прямо запрещает
писать содержимое в журнал, а журнал append-only, бессрочный и доступен по
отдельному праву. Сохранение без изменений запись не создаёт (паттерн
``rag_settings.changed``, Т-506).

Лимиты — настройки приложения: число скиллов на рабочую область
(``agent_skills_max_per_workspace``) и предельная длина текста
(``skill_prompt_max_chars``); превышение — 422 (паттерн Т-507).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.skills import (
    SkillAvailableListResponse,
    SkillAvailableResponse,
    SkillCreate,
    SkillDeleteResponse,
    SkillListResponse,
    SkillResponse,
    SkillUpdate,
)
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.config import Settings
from app.db.models import AgentSkill, User
from app.db.session import get_session
from app.errors import Conflict, NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(prefix="/api/skills", tags=["skills"], dependencies=[Depends(current_user)])


async def _check_manage_skills(session: AsyncSession, user: User) -> bool:
    """True если admin (через *) или явно выдана manage_skills."""
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities or "manage_skills" in policy.capabilities


async def _require_manage_skills(session: AsyncSession, user: User) -> None:
    if not await _check_manage_skills(session, user):
        raise NotFound(
            constraint={"object": "skills", "reason": "manage_skills required"},
            hint="Нет права на управление скиллами",
        )


def _require_prompt_within_limit(prompt_text: str) -> None:
    max_chars = Settings().skill_prompt_max_chars
    if len(prompt_text) > max_chars:
        raise RequestValidationError(
            [
                {
                    "type": "string_too_long",
                    "loc": ("body", "prompt_text"),
                    "msg": f"String should have at most {max_chars} characters",
                    "input": None,
                }
            ]
        )


async def _require_below_count_limit(session: AsyncSession, workspace_id: str) -> None:
    settings = Settings()
    count = await session.scalar(
        select(func.count()).select_from(AgentSkill).where(AgentSkill.workspace_id == workspace_id)
    )
    if (count or 0) >= settings.agent_skills_max_per_workspace:
        raise RequestValidationError(
            [
                {
                    "type": "too_many",
                    "loc": ("body",),
                    "msg": (
                        "Не более "
                        f"{settings.agent_skills_max_per_workspace} скиллов на рабочую область"
                    ),
                    "input": None,
                }
            ]
        )


def _to_response(row: AgentSkill) -> SkillResponse:
    return SkillResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        prompt_text=row.prompt_text,
        tools=list(row.tools or []),
        default_max_tokens=row.default_max_tokens,
        enabled=row.enabled,
        created_at=row.created_at,
    )


def _audit_fields(row: AgentSkill) -> dict[str, Any]:
    """Состав записи аудита: без текста промпта, только его длина."""
    return {
        "name": row.name,
        "description": row.description,
        "tools": list(row.tools or []),
        "default_max_tokens": row.default_max_tokens,
        "enabled": row.enabled,
        "prompt_chars": len(row.prompt_text),
    }


@router.get("", response_model=SkillListResponse)
async def list_skills(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> SkillListResponse:
    """Админский каталог: все скиллы рабочей области, включая выключенные."""
    await _require_manage_skills(session, user)
    result = await session.execute(
        select(AgentSkill)
        .where(AgentSkill.workspace_id == request.app.state.workspace_id)
        .order_by(AgentSkill.name, AgentSkill.id)
    )
    rows = result.scalars().all()
    return SkillListResponse(skills=[_to_response(r) for r in rows])


@router.get("/available", response_model=SkillAvailableListResponse)
async def list_available_skills(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SkillAvailableListResponse:
    """Список для выбора в диалоге: только включённые, только нужные поля."""
    result = await session.execute(
        select(AgentSkill)
        .where(
            AgentSkill.workspace_id == request.app.state.workspace_id,
            AgentSkill.enabled.is_(True),
        )
        .order_by(AgentSkill.name, AgentSkill.id)
    )
    rows = result.scalars().all()
    return SkillAvailableListResponse(
        skills=[
            SkillAvailableResponse(id=r.id, name=r.name, description=r.description) for r in rows
        ]
    )


@router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(
    body: SkillCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> SkillResponse:
    await _require_manage_skills(session, user)
    _require_prompt_within_limit(body.prompt_text)
    workspace_id = request.app.state.workspace_id
    await _require_below_count_limit(session, workspace_id)

    row = AgentSkill(
        workspace_id=workspace_id,
        name=body.name,
        description=body.description,
        prompt_text=body.prompt_text,
        tools=list(body.tools),
        default_max_tokens=body.default_max_tokens,
        enabled=body.enabled,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(
            "Скилл с таким именем уже существует",
            constraint={"name": body.name},
            hint="Имя уникально в рабочей области",
        ) from exc

    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        action="agent_skill.changed",
        object_type="agent_skill",
        object_id=row.id,
        meta={"old": None, "new": _audit_fields(row)},
    )
    await session.commit()
    await session.refresh(row)
    return _to_response(row)


async def _get_skill(session: AsyncSession, workspace_id: str, skill_id: str) -> AgentSkill:
    result = await session.execute(
        select(AgentSkill).where(AgentSkill.id == skill_id, AgentSkill.workspace_id == workspace_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFound(
            constraint={"object": "agent_skill", "id": skill_id},
            hint="Скилл не найден",
        )
    return row


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    body: SkillUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> SkillResponse:
    await _require_manage_skills(session, user)
    _require_prompt_within_limit(body.prompt_text)
    workspace_id = request.app.state.workspace_id
    row = await _get_skill(session, workspace_id, skill_id)

    old = _audit_fields(row)
    row.name = body.name
    row.description = body.description
    row.prompt_text = body.prompt_text
    row.tools = list(body.tools)
    row.default_max_tokens = body.default_max_tokens
    row.enabled = body.enabled

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(
            "Скилл с таким именем уже существует",
            constraint={"name": body.name},
            hint="Имя уникально в рабочей области",
        ) from exc

    new = _audit_fields(row)
    if new != old:
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_user_id=user.id,
            action="agent_skill.changed",
            object_type="agent_skill",
            object_id=row.id,
            meta={"old": old, "new": new},
        )
    await session.commit()
    await session.refresh(row)
    return _to_response(row)


@router.delete("/{skill_id}", response_model=SkillDeleteResponse)
async def delete_skill(
    skill_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> SkillDeleteResponse:
    """Удаление скилла из каталога.

    Исторических ссылок на скилл нет: выбор приходит в запросе
    (``skill_id``), а не хранится на диалоге, поэтому удаление не
    оставляет битых ссылок — следующий запрос с этим id получит явный
    отказ. Основной путь «временно выключить» — PUT enabled=false.
    """
    await _require_manage_skills(session, user)
    workspace_id = request.app.state.workspace_id
    row = await _get_skill(session, workspace_id, skill_id)
    old = _audit_fields(row)

    await session.delete(row)
    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        action="agent_skill.changed",
        object_type="agent_skill",
        object_id=skill_id,
        meta={"old": old, "new": None},
    )
    await session.commit()
    return SkillDeleteResponse(deleted=True)
