"""Т-509: профиль агента как источник конфигурации прогона и флаг остановки.

Профиль фиксирует модель и скилл диалога (решение 2 мини-дизайн-ревью):
диалог, созданный от профиля, их не переопределяет. Модуль намеренно
тонкий — всю защиту профилю обеспечивают уже существующие механизмы:
модель проходит ту же проверку ``supports_tools`` и тот же ``enforce_all``
(Т-502, пункты 3 и 8), скилл сужает реестр той же чистой функцией
``apply_skill`` (Т-508, решение 2). Профиль не добавляет ни инструментов,
ни прав, ни послаблений политики — он лишь выбирает уже существующие.

Флаг остановки (решение 7) читается между шагами цикла, а не внутри вызова
модели или инструмента: прерывать текущий шаг нельзя — инструмент внешнего
сервера мог уже начать действие, а оборванный вызов модели оставил бы
оплаченный расход без результата.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentProfile, Conversation
from app.errors import AgentProfileNotAvailable


async def resolve_profile_for_run(
    session: AsyncSession,
    workspace_id: str,
    profile_id: str,
) -> AgentProfile:
    """Загружает профиль для прогона; явный отказ 400, если не найден/отключён.

    Fail-closed по образцу ``resolve_skill_for_run`` (Т-508, решение 5):
    профиль — источник модели и скилла диалога, поэтому тихий откат к
    ad-hoc прогону с моделью из запроса означал бы обход закреплённой
    администратором конфигурации. Отключённый профиль отключает и его
    диалоги: это смысл выключения, а не побочный эффект.

    Отдельный класс ошибки — чтобы конкретная причина дошла до клиента в
    поле ``reason`` (обработчик берёт атрибут класса, а не текст
    исключения).
    """
    row = (
        await session.execute(
            select(AgentProfile).where(
                AgentProfile.id == profile_id,
                AgentProfile.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.enabled:
        raise AgentProfileNotAvailable(constraint={"agent_profile_id": profile_id})
    return row


async def is_stop_requested(session: AsyncSession, conversation_id: str | None) -> bool:
    """Читает флаг остановки диалога отдельным запросом.

    Запрос именно отдельный (не состояние загруженного ORM-объекта): флаг
    ставит другой HTTP-запрос, поэтому значение обязано читаться из базы
    заново на каждой проверке. Каждый вызов модели завершается
    ``record_usage`` с коммитом, так что транзакция прогона к моменту
    проверки закрыта и SELECT видит уже зафиксированную остановку.
    """
    if conversation_id is None:
        return False
    value = await session.scalar(
        select(Conversation.stop_requested).where(Conversation.id == conversation_id)
    )
    return bool(value)


async def consume_stop_request(session: AsyncSession, conversation_id: str | None) -> None:
    """Снимает флаг: остановка потреблена прогоном и не действует на следующий.

    Обновление отдельным UPDATE, а не присваиванием атрибуту ORM-объекта:
    экземпляр диалога в сессии прогона загружен до установки флага другим
    запросом, и присваивание могло бы перезаписать его устаревшим
    состоянием строки.
    """
    if conversation_id is None:
        return
    await session.execute(
        update(Conversation).where(Conversation.id == conversation_id).values(stop_requested=False)
    )
    await session.commit()
