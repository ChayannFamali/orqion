"""Т-508: скиллы — сужение реестра инструментов и фрагмент системного промпта.

Скилл декларативный (решение 1 мини-дизайн-ревью): фрагмент системного
промпта + подмножество инструментов единого реестра + дефолт
``max_tokens``. Исполняемого содержимого нет и путь к нему не
закладывается — санкционированный путь исполнения уже существует
(реестр MCP-серверов Т-503 со всеми его защитами).

Сужение — чистая функция :func:`apply_skill`, применяется ПОСЛЕ
``resolve_tools`` и ДО цикла (решение 2): гарантии Т-503 (сборка
реестра, отказ К2/К3 до транспорта, аудит недоступности сервера) не
трогаем. Скилл не добавляет инструмент, которого нет в реестре, не
пересчитывает класс данных и не подменяет ``policy.corpora``.

Пустой список инструментов скилла означает НОЛЬ инструментов (решение 6,
правка пользователя) — максимально ограничительно, а не «не сужать»:
расширение доступа должно быть явным перечислением имён в момент правки
скилла, а не молчаливым следствием регистрации нового сервера.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import ResolvedTools
from app.db.models import AgentSkill
from app.errors import SkillNotAvailable


async def resolve_skill_for_run(
    session: AsyncSession,
    workspace_id: str,
    skill_id: str,
) -> AgentSkill:
    """Загружает скилл для прогона; явный отказ 400, если не найден/отключён.

    Несуществующий или ``enabled=false`` ``skill_id`` — невалидный
    параметр запроса (решение 5, правка пользователя): явный отказ, а не
    тихий откат к прогону без скилла. Прецеденты в коде: fail-closed
    ``resolve_corpora`` (решение Е1 Т-439 — «весь запрос падает, если хоть
    один из выбранных корпусов не найден или не готов»), пустое имя
    корпуса и конфликт пинов в ``chat.py``, несоответствие подтверждения
    ожидаемому в ``agent.py``.

    Отличать от деградации решения 6: здесь отказ на сам факт
    «запрошенного скилла не существует», а не на недоступность одного из
    его инструментов в уже принятом прогоне.

    Ошибка — отдельный класс ``SkillNotAvailable``, а не ``BadRequest`` с
    текстом: обработчик ошибок кладёт в ответ атрибут ``reason`` класса,
    поэтому позиционное сообщение ``BadRequest`` до клиента не дошло бы и
    пользователь увидел бы общее «Некорректный запрос».
    """
    result = await session.execute(
        select(AgentSkill).where(
            AgentSkill.id == skill_id,
            AgentSkill.workspace_id == workspace_id,
        )
    )
    skill = result.scalar_one_or_none()
    if skill is None or not skill.enabled:
        raise SkillNotAvailable(constraint={"skill_id": skill_id})
    return skill


def skill_drop_reason(name: str, registry: ResolvedTools) -> str:
    """Причина, по которой инструмент скилла не вошёл в прогон.

    ``blocked_external`` — внешний инструмент (имя с неймспейсом
    ``<сервер>.<инструмент>``) при классе данных К2/К3: отклонён ещё до
    обнаружения, факт уже записан существующим ``mcp.tools.blocked``
    (решение 2 — новый аудит на каждый скилл не пишем).

    ``not_in_registry`` — прочее: сервер недоступен и уже скрыт
    ``resolve_tools`` с аудитом ``mcp.server.unavailable``, инструмент не
    зарегистрирован, или опечатка в имени. Отдельно различать
    «недоступный сервер» и «не найдено» здесь нечем — ``resolve_tools``
    уже изъял инструменты недоступных серверов и зафиксировал это своим
    аудитом, поэтому причина на этом слое честная и укрупнённая.
    """
    if registry.blocked_external is not None and "." in name:
        return "blocked_external"
    return "not_in_registry"


def apply_skill(
    registry: ResolvedTools,
    skill: AgentSkill,
) -> tuple[ResolvedTools, list[str]]:
    """Сужает реестр прогона до инструментов скилла (решение 2).

    Возвращает ``(суженный реестр, имена инструментов скилла,
    недоступные в этом прогоне)``. Чистая функция: входной реестр не
    изменяется, строится новый. Порядок спецификаций сохраняется
    канонический (как в реестре), а не как в списке скилла.

    Пустой ``skill.tools`` даёт НОЛЬ инструментов (решение 6): модели
    уходит пустой набор схем, встроенный ``search_corpus`` в том числе
    недоступен. Транспортные данные (``servers``) сужаются до серверов,
    чьи инструменты остались, — недостижимый эндпоинт не сохраняется.
    """
    wanted = list(dict.fromkeys(skill.tools or []))
    wanted_set = set(wanted)
    kept_specs = [s for s in registry.specs if s.name in wanted_set]
    kept_names = {s.name for s in kept_specs}
    unavailable = [name for name in wanted if name not in kept_names]

    kept_servers = {
        server_name: endpoint
        for server_name, endpoint in registry.servers.items()
        if any(s.server_name == server_name for s in kept_specs)
    }
    narrowed = ResolvedTools(
        specs=kept_specs,
        servers=kept_servers,
        blocked_external=registry.blocked_external,
    )
    return narrowed, unavailable
