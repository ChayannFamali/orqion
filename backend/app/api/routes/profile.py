"""Личные настройки пользователя — раздел «Профиль» (Т-512).

Первый раздел, у которого область действия — один человек, а не рабочая
область: значение видно и меняется только владельцем строки. Описание
ключей живёт в коде (``app/preferences/registry.py``), в таблице
``user_preferences`` хранится только факт записи, поэтому новая личная
настройка не требует ни миграции, ни правок во фронтенде.

Доступ — **не** способность роли, а владение строкой: гейт один на весь
раздел (``current_user``), а отбор идёт по ``user_id == user.id``. Права на
ключ нет, значит и ответа 404 «нет права» нет — чужие настройки просто не
видны и не меняются. Этим раздел отличается от служебных настроек рабочей
области, где запись требует способность ``manage_settings``.

Аудит не пишется: это личное содержимое, как шаблоны промптов и
содержимое диалогов (решение дизайн-ревью Т-507, арх.документ §5.3). На
этом настаивает отдельный тест — в служебных настройках аудит пишется на
каждую запись, поэтому копирование того маршрута добавило бы аудит
незаметно.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.profile import (
    UserPreferenceListResponse,
    UserPreferenceResponse,
    UserPreferenceUpdate,
)
from app.auth.dependencies import current_user
from app.db.models import User, UserPreference
from app.db.session import get_session
from app.errors import NotFound
from app.preferences.registry import (
    PreferenceSpec,
    default_value,
    get_spec,
    ordered_specs,
)
from app.settings.registry import describe_value_model
from app.settings.value_errors import validate_value

router = APIRouter(
    prefix="/api/profile/preferences",
    tags=["profile"],
    dependencies=[Depends(current_user)],
)


def _to_response(
    spec: PreferenceSpec,
    row: UserPreference | None,
) -> UserPreferenceResponse:
    """Запись списка: значение из БД или дефолт, плюс описание поля.

    ``editable`` всегда ``true``: маршрут отдаёт только строки владельца,
    значит менять их ему можно. Поле сохранено, чтобы список рисовался тем
    же компонентом интерфейса, что и служебные настройки.

    ``enum_labels`` — подписи вариантов на языке интерфейса; машинные
    значения в списке выбора не показываются. Реестр требует, чтобы у
    перечисления подписи были заданы целиком, поэтому у ключа с
    перечислением поле не бывает пустым.
    """
    field = describe_value_model(spec.value_model)
    return UserPreferenceResponse(
        key=spec.key,
        title=spec.title,
        description=spec.description,
        category=spec.category,
        type=field.type,
        enum_values=field.enum_values,
        enum_labels=dict(spec.option_titles) if field.enum_values else None,
        value=row.value if row is not None else default_value(spec),
        source="db" if row is not None else "default",
        editable=True,
        min=field.min,
        max=field.max,
    )


async def _load_rows(
    session: AsyncSession, workspace_id: str, user_id: str
) -> dict[str, UserPreference]:
    """Записанные значения владельца по ключу: один запрос на весь список.

    Значения из таблицы, которых нет в реестре, в ответ не попадают: список
    строится по реестру, а не по содержимому таблицы. Так ключ, снятый с
    регистрации, не создаёт в интерфейсе поле, которое невозможно
    объяснить.
    """
    result = await session.execute(
        select(UserPreference).where(
            UserPreference.workspace_id == workspace_id,
            UserPreference.user_id == user_id,
        )
    )
    return {row.key: row for row in result.scalars().all()}


@router.get("", response_model=UserPreferenceListResponse)
async def list_user_preferences(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> UserPreferenceListResponse:
    """Все зарегистрированные ключи с резолвнутым значением и меткой источника."""
    rows = await _load_rows(session, request.app.state.workspace_id, user.id)
    return UserPreferenceListResponse(
        preferences=[_to_response(spec, rows.get(spec.key)) for spec in ordered_specs()]
    )


@router.patch("", response_model=UserPreferenceResponse)
async def update_user_preference(
    body: UserPreferenceUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> UserPreferenceResponse:
    """Запись одного ключа: валидация по спеке, владение по ``user_id``.

    Батча нет намеренно — при отказе по одному из ключей непонятно, какое
    именно поле его вызвало.
    """
    spec = get_spec(body.key)
    if spec is None:
        raise NotFound(
            constraint={"object": "user_preference", "key": body.key},
            hint="Настройка не найдена",
        )

    workspace_id = request.app.state.workspace_id
    new_value = validate_value(spec.key, spec.value_model, body.value)

    row = await session.get(UserPreference, (workspace_id, user.id, spec.key))
    if row is None:
        row = UserPreference(
            workspace_id=workspace_id,
            user_id=user.id,
            key=spec.key,
            value=new_value,
            updated_at=datetime.now(UTC),
        )
        session.add(row)
    else:
        row.value = new_value
        row.updated_at = datetime.now(UTC)

    await session.commit()
    return _to_response(spec, row)
