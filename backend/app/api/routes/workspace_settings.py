"""GET /api/workspace/settings, PATCH /api/workspace/settings.

Реестр служебных настроек рабочей области: одно обобщённое хранилище
вместо отдельной таблицы под каждую настройку. Описания ключей живут в
коде — ``app/settings/registry.py``, в таблице ``workspace_settings``
хранится только факт записи значения.

Доступ разделён не по маршруту, а по ключу:

- чтение — всем аутентифицированным, со значением и меткой источника.
  Секретов реестр не хранит, поэтому «закрытого» списка чтения нет;
- запись — способность ``write_capability`` из спеки именно этого ключа.
  Без права 404, а не 403: существование ключа и требование к праву не
  раскрываются. Общий гейт на маршрут был бы ошибкой — он либо закрывал
  бы все ключи одной способностью, либо открывал бы лишнее.

Резолв значения прозрачный: строка в БД → значение из БД и источник
``db``, строки нет → дефолт из env-конфига или из дефолта модели значения,
источник ``default``. Клиент всегда получает рабочее значение.

Аудит ``settings.changed`` пишется на каждую успешную запись со старым и
новым значением; ключ виден в ``meta``, актор — колонка
``audit_log.actor_user_id``. ``object_id`` — ``workspace_id``: ключ
длиннее колонки ``audit_log.object_id`` (String(36)) и в неё не помещается.

Повторная запись того же значения тоже оставляет запись в журнале: аудит
задаётся как «на каждую успешную запись», и факт обращения к настройке
здесь уместнее пропуска.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.workspace_settings import (
    ValueSource,
    WorkspaceSettingListResponse,
    WorkspaceSettingResponse,
    WorkspaceSettingUpdate,
)
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.config import Settings
from app.db.models import User, WorkspaceSetting
from app.db.session import get_session
from app.errors import NotFound
from app.policy.models import WILDCARD, Policy
from app.policy.resolve import resolve_policy
from app.settings.registry import (
    SettingSpec,
    default_value,
    describe_value_field,
    get_spec,
    ordered_specs,
)
from app.settings.value_errors import validate_value

router = APIRouter(
    prefix="/api/workspace/settings",
    tags=["workspace-settings"],
    dependencies=[Depends(current_user)],
)


def _can_write(policy: Policy, spec: SettingSpec) -> bool:
    """Право на запись именно этого ключа (wildcard или явная способность)."""
    return WILDCARD in policy.capabilities or spec.write_capability in policy.capabilities


def _to_response(
    spec: SettingSpec,
    row: WorkspaceSetting | None,
    policy: Policy,
    app_settings: Settings,
) -> WorkspaceSettingResponse:
    field = describe_value_field(spec)
    if row is not None:
        value: Any = row.value
        source: ValueSource = "db"
    else:
        value = default_value(spec, app_settings)
        source = "default"
    return WorkspaceSettingResponse(
        key=spec.key,
        title=spec.title,
        description=spec.description,
        category=spec.category,
        type=field.type,
        enum_values=field.enum_values,
        value=value,
        source=source,
        editable=_can_write(policy, spec),
        min=field.min,
        max=field.max,
    )


async def _load_rows(session: AsyncSession, workspace_id: str) -> dict[str, WorkspaceSetting]:
    """Записи рабочей области по ключу: один запрос на весь список ключей."""
    result = await session.execute(
        select(WorkspaceSetting).where(WorkspaceSetting.workspace_id == workspace_id)
    )
    return {row.key: row for row in result.scalars().all()}


@router.get("", response_model=WorkspaceSettingListResponse)
async def list_workspace_settings(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> WorkspaceSettingListResponse:
    """Все зарегистрированные ключи с резолвнутым значением и меткой источника."""
    workspace_id = request.app.state.workspace_id
    policy = await resolve_policy(session, user)
    app_settings = Settings()
    rows = await _load_rows(session, workspace_id)
    return WorkspaceSettingListResponse(
        settings=[
            _to_response(spec, rows.get(spec.key), policy, app_settings) for spec in ordered_specs()
        ]
    )


@router.patch("", response_model=WorkspaceSettingResponse)
async def update_workspace_setting(
    body: WorkspaceSettingUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> WorkspaceSettingResponse:
    """Запись одного ключа: валидация по спеке, право по тегу спеки, аудит."""
    workspace_id = request.app.state.workspace_id

    spec = get_spec(body.key)
    if spec is None:
        raise NotFound(
            constraint={"object": "workspace_setting", "key": body.key},
            hint="Настройка не найдена",
        )

    policy = await resolve_policy(session, user)
    if not _can_write(policy, spec):
        raise NotFound(
            constraint={
                "object": "workspace_setting",
                "key": body.key,
                "reason": f"{spec.write_capability} required",
            },
            hint="Нет права на изменение этой настройки",
        )

    new_value = validate_value(spec.key, spec.value_model, body.value)
    app_settings = Settings()
    row = await session.get(WorkspaceSetting, (workspace_id, spec.key))
    old_value: Any = row.value if row is not None else default_value(spec, app_settings)

    now = datetime.now(UTC)
    if row is None:
        row = WorkspaceSetting(
            workspace_id=workspace_id,
            key=spec.key,
            value=new_value,
            updated_at=now,
            updated_by=user.id,
        )
        session.add(row)
    else:
        row.value = new_value
        row.updated_at = now
        row.updated_by = user.id

    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        action="settings.changed",
        object_type="workspace_setting",
        object_id=workspace_id,
        meta={"key": spec.key, "old": old_value, "new": new_value},
    )
    await session.commit()
    return _to_response(spec, row, policy, app_settings)
