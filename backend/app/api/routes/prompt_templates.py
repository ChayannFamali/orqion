"""CRUD библиотеки сохранённых промптов (Т-507).

Личные текстовые шаблоны пользователя (системные промпты / готовые
формулировки вопросов), применяемые в чате вставкой в поле ввода.

Доступ — способность ``custom_prompts`` (в посевных пресетах у ролей
developer, architect, manager; у admin через ``*``): без права все
эндпоинты отвечают 404 по паттерну ``view_code_graph``.

Первая версия — только личные шаблоны: видны и изменяются только
владельцем. Лимиты — настройки приложения: число шаблонов на
пользователя (``prompt_templates_max_per_user``) и предельная длина
текста (``prompt_template_max_chars``); превышение — 422.

Аудит не пишется: личное содержимое, как содержимое диалогов
(решение дизайн-ревью Т-507, согласуется с арх.документом §5.3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.prompt_templates import (
    PromptTemplateCreate,
    PromptTemplateListResponse,
    PromptTemplateResponse,
    PromptTemplateUpdate,
)
from app.auth.dependencies import current_user
from app.config import Settings
from app.db.models import PromptTemplate, User
from app.db.session import get_session
from app.errors import NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/prompt-templates",
    tags=["prompt-templates"],
    dependencies=[Depends(current_user)],
)


async def _check_custom_prompts(session: AsyncSession, user: User) -> bool:
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities or "custom_prompts" in policy.capabilities


async def _require_custom_prompts(session: AsyncSession, user: User) -> None:
    if not await _check_custom_prompts(session, user):
        raise NotFound(
            constraint={"object": "prompt-templates", "reason": "custom_prompts required"},
            hint="Нет права на работу с шаблонами промптов",
        )


def _require_body_within_limit(body: str) -> None:
    max_chars = Settings().prompt_template_max_chars
    if len(body) > max_chars:
        raise RequestValidationError(
            [
                {
                    "type": "string_too_long",
                    "loc": ("body", "body"),
                    "msg": f"String should have at most {max_chars} characters",
                    "input": body,
                }
            ]
        )


def _to_response(row: PromptTemplate) -> PromptTemplateResponse:
    return PromptTemplateResponse(
        id=row.id,
        title=row.title,
        body=row.body,
        created_at=row.created_at,
    )


@router.get("", response_model=PromptTemplateListResponse)
async def list_prompt_templates(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> PromptTemplateListResponse:
    await _require_custom_prompts(session, user)
    result = await session.execute(
        select(PromptTemplate)
        .where(
            PromptTemplate.workspace_id == request.app.state.workspace_id,
            PromptTemplate.user_id == user.id,
        )
        .order_by(PromptTemplate.created_at, PromptTemplate.id)
    )
    rows = result.scalars().all()
    return PromptTemplateListResponse(templates=[_to_response(r) for r in rows])


@router.post(
    "",
    response_model=PromptTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_template(
    body: PromptTemplateCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> PromptTemplateResponse:
    await _require_custom_prompts(session, user)
    _require_body_within_limit(body.body)

    settings = Settings()
    count = await session.scalar(
        select(func.count())
        .select_from(PromptTemplate)
        .where(
            PromptTemplate.workspace_id == request.app.state.workspace_id,
            PromptTemplate.user_id == user.id,
        )
    )
    if (count or 0) >= settings.prompt_templates_max_per_user:
        raise RequestValidationError(
            [
                {
                    "type": "too_many",
                    "loc": ("body",),
                    "msg": (
                        "Не более "
                        f"{settings.prompt_templates_max_per_user} шаблонов на пользователя"
                    ),
                    "input": None,
                }
            ]
        )

    row = PromptTemplate(
        workspace_id=request.app.state.workspace_id,
        user_id=user.id,
        title=body.title,
        body=body.body,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_response(row)


async def _get_own_template(
    session: AsyncSession, workspace_id: str, user: User, template_id: str
) -> PromptTemplate:
    result = await session.execute(
        select(PromptTemplate).where(
            PromptTemplate.id == template_id,
            PromptTemplate.workspace_id == workspace_id,
            PromptTemplate.user_id == user.id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFound(
            constraint={"object": "prompt_template", "id": template_id},
            hint="Шаблон не найден",
        )
    return row


@router.put("/{template_id}", response_model=PromptTemplateResponse)
async def update_prompt_template(
    template_id: str,
    body: PromptTemplateUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> PromptTemplateResponse:
    await _require_custom_prompts(session, user)
    _require_body_within_limit(body.body)
    row = await _get_own_template(session, request.app.state.workspace_id, user, template_id)
    row.title = body.title
    row.body = body.body
    await session.commit()
    await session.refresh(row)
    return _to_response(row)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt_template(
    template_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> None:
    await _require_custom_prompts(session, user)
    row = await _get_own_template(session, request.app.state.workspace_id, user, template_id)
    await session.delete(row)
    await session.commit()
