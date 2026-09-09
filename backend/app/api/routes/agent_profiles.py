"""CRUD профилей агентов — переиспользуемых конфигураций диалога (Т-509).

Профиль — модель + скилл (решение 1): отдельного системного промпта и
отдельного списка инструментов в профиле нет, эту роль полностью
выполняет скилл (Т-508). Профиль без скилла — агент без сужения.

Два списка по образцу разделения Т-508:

- ``GET /api/agent-profiles`` — админский каталог (способность
  ``manage_agents``; без права 404, существование раздела управления не
  раскрывается). Все строки рабочей области, включая отключённые, полными
  полями — иначе отключённый профиль нельзя включить обратно;
- ``GET /api/agent-profiles/available`` — список для старта диалога: всем
  аутентифицированным, только включённые и только поля выбора (решение 3).

Записи — только с ``manage_agents`` (wildcard-only, не в посевных
пресетах; паттерн ``manage_skills`` дословно).

``GET /api/agent-profiles/{id}/conversations`` (решение 5) — drill-down по
диалогам профиля. Без ``manage_agents`` — только собственные диалоги; с
правом — все диалоги рабочей области, но ТОЛЬКО метаданные и расход
(агрегат существующего ``usage_event.conversation_id``, новая таблица не
заводится). Содержимое переписки не отдаётся никогда, даже с правом: в
схеме ответа полей сообщения нет вовсе, поэтому отдать его нельзя, а не
«не отдаётся по проверке». Заголовок диалога — первые 80 символов первого
сообщения, то есть производное содержимого, — заполняется только для
собственных диалогов.

Удаление профиля при наличии диалогов — 409 (решение 8), каскада нет:
аналог провайдера с моделями и модели, закреплённой за корпусом.
Основной путь «убрать из выбора» — ``enabled=false``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.agent_profiles import (
    AgentProfileAvailableListResponse,
    AgentProfileAvailableResponse,
    AgentProfileConversationEntry,
    AgentProfileConversationListResponse,
    AgentProfileCreate,
    AgentProfileDeleteResponse,
    AgentProfileListResponse,
    AgentProfileResponse,
    AgentProfileUpdate,
)
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.config import Settings
from app.db.models import AgentProfile, AgentSkill, Conversation, Model, UsageEvent, User
from app.db.session import get_session
from app.errors import BadRequest, Conflict, NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/agent-profiles",
    tags=["agent-profiles"],
    dependencies=[Depends(current_user)],
)


async def _check_manage_agents(session: AsyncSession, user: User) -> bool:
    """True если admin (через *) или явно выдана manage_agents."""
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities or "manage_agents" in policy.capabilities


async def _require_manage_agents(session: AsyncSession, user: User) -> None:
    if not await _check_manage_agents(session, user):
        raise NotFound(
            constraint={"object": "agent-profiles", "reason": "manage_agents required"},
            hint="Нет права на управление профилями агентов",
        )


async def _require_below_count_limit(session: AsyncSession, workspace_id: str) -> None:
    settings = Settings()
    count = await session.scalar(
        select(func.count())
        .select_from(AgentProfile)
        .where(AgentProfile.workspace_id == workspace_id)
    )
    if (count or 0) >= settings.agent_profiles_max_per_workspace:
        raise RequestValidationError(
            [
                {
                    "type": "too_many",
                    "loc": ("body",),
                    "msg": (
                        "Не более "
                        f"{settings.agent_profiles_max_per_workspace} профилей "
                        "на рабочую область"
                    ),
                    "input": None,
                }
            ]
        )


async def _require_agent_model(session: AsyncSession, workspace_id: str, model_id: str) -> Model:
    """Модель профиля: существует в рабочей области и пригодна к инструментам.

    Решение 1: ``supports_tools=false`` на создании — 400, а не сохранение
    профиля, который не сможет отработать ни одного запроса. Отказ тот же,
    что в агентном прогоне (Т-502, решение 3): та же ситуация, тот же
    ответ. Состояние ``enabled`` на создании не проверяется — модель можно
    включить позже, а прогон отклоняет отключённую модель явно.
    """
    model = (
        await session.execute(
            select(Model).where(Model.id == model_id, Model.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if model is None:
        raise NotFound(
            constraint={"object": "model", "id": model_id},
            hint="Модель не найдена",
        )
    if not model.supports_tools:
        raise BadRequest(
            "Модель не отмечена как пригодная для агентного режима",
            constraint={"model_id": model_id},
            hint="Администратор должен включить флаг «Модель подходит для агентного режима»",
        )
    return model


async def _require_skill_exists(
    session: AsyncSession, workspace_id: str, skill_id: str
) -> AgentSkill:
    """Скилл профиля существует в рабочей области.

    Состояние ``enabled`` на создании не проверяется: скилл можно включить
    позже, а прогон отклоняет отключённый скилл явно (Т-508, решение 5 —
    ``SkillNotAvailable``, 400). Молчаливого «профиль без сужения» вместо
    выбранного скилла не будет.
    """
    skill = (
        await session.execute(
            select(AgentSkill).where(
                AgentSkill.id == skill_id, AgentSkill.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if skill is None:
        raise NotFound(
            constraint={"object": "agent_skill", "id": skill_id},
            hint="Скилл не найден",
        )
    return skill


def _to_response(
    row: AgentProfile, model_alias: str, skill_name: str | None
) -> AgentProfileResponse:
    return AgentProfileResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        model_id=row.model_id,
        model_alias=model_alias,
        skill_id=row.skill_id,
        skill_name=skill_name,
        enabled=row.enabled,
        created_at=row.created_at,
    )


def _audit_fields(row: AgentProfile) -> dict[str, Any]:
    """Состав записи аудита: идентификаторы и флаги, содержимого нет.

    Текста промпта в профиле нет вовсе (решение 1) — писать в журнал
    нечего, поэтому ограничение ADR-21 п. 2 здесь соблюдается построением.
    """
    return {
        "name": row.name,
        "description": row.description,
        "model_id": row.model_id,
        "skill_id": row.skill_id,
        "enabled": row.enabled,
    }


async def _load_rows(session: AsyncSession, workspace_id: str, *, enabled_only: bool) -> list[Any]:
    """Профили с алиасом модели и именем скилла одним запросом."""
    stmt = (
        select(AgentProfile, Model.alias, AgentSkill.name)
        .join(Model, Model.id == AgentProfile.model_id)
        .outerjoin(AgentSkill, AgentSkill.id == AgentProfile.skill_id)
        .where(AgentProfile.workspace_id == workspace_id)
        .order_by(AgentProfile.name, AgentProfile.id)
    )
    if enabled_only:
        stmt = stmt.where(AgentProfile.enabled.is_(True))
    return list((await session.execute(stmt)).all())


@router.get("", response_model=AgentProfileListResponse)
async def list_agent_profiles(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> AgentProfileListResponse:
    """Админский каталог: все профили рабочей области, включая отключённые."""
    await _require_manage_agents(session, user)
    rows = await _load_rows(session, request.app.state.workspace_id, enabled_only=False)
    return AgentProfileListResponse(
        profiles=[
            _to_response(row, model_alias, skill_name) for row, model_alias, skill_name in rows
        ]
    )


@router.get("/available", response_model=AgentProfileAvailableListResponse)
async def list_available_agent_profiles(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentProfileAvailableListResponse:
    """Список для старта диалога: только включённые, только поля выбора."""
    rows = await _load_rows(session, request.app.state.workspace_id, enabled_only=True)
    return AgentProfileAvailableListResponse(
        profiles=[
            AgentProfileAvailableResponse(id=row.id, name=row.name, description=row.description)
            for row, _model_alias, _skill_name in rows
        ]
    )


@router.post("", response_model=AgentProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_profile(
    body: AgentProfileCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> AgentProfileResponse:
    await _require_manage_agents(session, user)
    workspace_id = request.app.state.workspace_id
    await _require_below_count_limit(session, workspace_id)
    model = await _require_agent_model(session, workspace_id, body.model_id)
    skill: AgentSkill | None = None
    if body.skill_id is not None:
        skill = await _require_skill_exists(session, workspace_id, body.skill_id)

    row = AgentProfile(
        workspace_id=workspace_id,
        name=body.name,
        description=body.description,
        model_id=body.model_id,
        skill_id=body.skill_id,
        enabled=body.enabled,
        created_by=user.id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(
            "Профиль с таким именем уже существует",
            constraint={"name": body.name},
            hint="Имя уникально в рабочей области",
        ) from exc

    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        action="agent_profile.changed",
        object_type="agent_profile",
        object_id=row.id,
        meta={"old": None, "new": _audit_fields(row)},
    )
    await session.commit()
    await session.refresh(row)
    return _to_response(row, model.alias, skill.name if skill is not None else None)


async def _get_profile(session: AsyncSession, workspace_id: str, profile_id: str) -> AgentProfile:
    row = (
        await session.execute(
            select(AgentProfile).where(
                AgentProfile.id == profile_id, AgentProfile.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(
            constraint={"object": "agent_profile", "id": profile_id},
            hint="Профиль агента не найден",
        )
    return row


@router.put("/{profile_id}", response_model=AgentProfileResponse)
async def update_agent_profile(
    profile_id: str,
    body: AgentProfileUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> AgentProfileResponse:
    """Правка профиля.

    Решение 2: модель и скилл зафиксированы профилем, поэтому их смена
    меняет поведение ВСЕХ существующих диалогов профиля — это не скрытый
    побочный эффект, а смысл поля: диалог ссылается на профиль, а не
    хранит копию конфигурации. Факт смены остаётся в журнале аудита.
    """
    await _require_manage_agents(session, user)
    workspace_id = request.app.state.workspace_id
    row = await _get_profile(session, workspace_id, profile_id)
    model = await _require_agent_model(session, workspace_id, body.model_id)
    skill: AgentSkill | None = None
    if body.skill_id is not None:
        skill = await _require_skill_exists(session, workspace_id, body.skill_id)

    old = _audit_fields(row)
    row.name = body.name
    row.description = body.description
    row.model_id = body.model_id
    row.skill_id = body.skill_id
    row.enabled = body.enabled

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(
            "Профиль с таким именем уже существует",
            constraint={"name": body.name},
            hint="Имя уникально в рабочей области",
        ) from exc

    new = _audit_fields(row)
    if new != old:
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_user_id=user.id,
            action="agent_profile.changed",
            object_type="agent_profile",
            object_id=row.id,
            meta={"old": old, "new": new},
        )
    await session.commit()
    await session.refresh(row)
    return _to_response(row, model.alias, skill.name if skill is not None else None)


@router.delete("/{profile_id}", response_model=AgentProfileDeleteResponse)
async def delete_agent_profile(
    profile_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> AgentProfileDeleteResponse:
    """Удаление профиля из каталога.

    Решение 8: при наличии диалогов с этим ``agent_profile_id`` — 409, не
    каскад. Диалог без профиля потерял бы зафиксированную конфигурацию и
    молча продолжился бы как ad-hoc; удаление диалогов вместе с профилем
    уничтожило бы чужую переписку и расход админским действием. Основной
    путь «убрать из выбора» — ``enabled=false``.
    """
    await _require_manage_agents(session, user)
    workspace_id = request.app.state.workspace_id
    row = await _get_profile(session, workspace_id, profile_id)

    conversations_count = await session.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.agent_profile_id == profile_id)
    )
    if conversations_count:
        raise Conflict(
            "У профиля есть диалоги: удаление заблокировано",
            constraint={
                "object": "agent_profile",
                "id": profile_id,
                "conversations_count": conversations_count,
            },
            hint="Отключите профиль (enabled=false) вместо удаления",
        )

    old = _audit_fields(row)
    await session.delete(row)
    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        action="agent_profile.changed",
        object_type="agent_profile",
        object_id=profile_id,
        meta={"old": old, "new": None},
    )
    await session.commit()
    return AgentProfileDeleteResponse(deleted=True)


@router.get(
    "/{profile_id}/conversations",
    response_model=AgentProfileConversationListResponse,
)
async def list_agent_profile_conversations(
    profile_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> AgentProfileConversationListResponse:
    """Диалоги профиля: метаданные и расход, без содержимого переписки.

    Решение 5 — двух вариантов охвата ровно два, третьего не дано: без
    ``manage_agents`` в выдачу попадают только собственные диалоги
    пользователя, с правом — все диалоги рабочей области, но теми же
    полями метаданных. Право не расширяет состав полей, только охват
    строк.
    """
    workspace_id: str = request.app.state.workspace_id
    await _get_profile(session, workspace_id, profile_id)
    can_manage = await _check_manage_agents(session, user)
    scope = "workspace" if can_manage else "own"

    base = select(Conversation).where(
        Conversation.workspace_id == workspace_id,
        Conversation.agent_profile_id == profile_id,
    )
    count_stmt = (
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.agent_profile_id == profile_id,
        )
    )
    if not can_manage:
        base = base.where(Conversation.user_id == user.id)
        count_stmt = count_stmt.where(Conversation.user_id == user.id)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        (
            await session.execute(
                base.order_by(Conversation.last_activity_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return AgentProfileConversationListResponse(conversations=[], total=total, scope=scope)

    conversation_ids = [row.id for row in rows]
    usage_rows = (
        await session.execute(
            select(
                UsageEvent.conversation_id,
                func.count(UsageEvent.id),
                func.sum(UsageEvent.tokens_in),
                func.sum(UsageEvent.tokens_out),
                func.sum(UsageEvent.cost),
            )
            .where(
                UsageEvent.workspace_id == workspace_id,
                UsageEvent.conversation_id.in_(conversation_ids),
            )
            .group_by(UsageEvent.conversation_id)
        )
    ).all()
    usage_by_conversation: dict[str, tuple[int, int, int, float]] = {
        str(conversation_id): (
            int(requests or 0),
            int(tokens_in or 0),
            int(tokens_out or 0),
            float(cost or 0.0),
        )
        for conversation_id, requests, tokens_in, tokens_out, cost in usage_rows
    }

    entries: list[AgentProfileConversationEntry] = []
    for row in rows:
        requests_count, tokens_in, tokens_out, cost = usage_by_conversation.get(
            row.id, (0, 0, 0, 0.0)
        )
        entries.append(
            AgentProfileConversationEntry(
                id=row.id,
                user_id=row.user_id,
                # Заголовок — первые 80 символов первого сообщения, то есть
                # производное содержимого переписки: чужим не отдаётся.
                title=row.title if row.user_id == user.id else None,
                archived=row.archived,
                created_at=row.created_at,
                last_activity_at=row.last_activity_at,
                requests=requests_count,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=cost,
            )
        )
    return AgentProfileConversationListResponse(conversations=entries, total=total, scope=scope)
