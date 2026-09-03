"""Т-502: инструменты агентного модуля.

Обязательный MVP-инструмент — поиск по корпусу (пункт 6 пересмотренного
дизайн-ревью): оборачивает существующий RAG-конвейер (поиск → реранкинг
→ сборка контекста, Т-216/Т-217/Т-219) без генерации. Read-only,
деструктивным не является.

Гарантии ADR-21 переносятся буквально, не отсылкой (пункт 7):

- проверка ``policy.corpora`` тем же вызовом ``enforce``, что в обычном
  чате: тот же отказ и перечень недоступных корпусов; тихая фильтрация
  подмножества запрещена;
- дуальная запись каждого вызова: полный состав вызова — в span
  трассировки; компактный факт (разрешено/отклонено, класс данных на
  момент вызова) — в журнал аудита бессрочно;
- класс данных разговора с агентом = самый строгий из источников —
  вычисляется на уровне эндпоинта и прокидывается в контекст прогона.

Механизм подтверждения деструктивных действий (пункт 9) закладывается
здесь: каждый инструмент декларирует ``destructive``; в Т-502 таких нет,
реальное использование проверяется в Т-503/Т-508.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit
from app.config import Settings
from app.db.models import Corpus, Model, Provider
from app.errors import OrqionError
from app.policy.enforce import enforce
from app.policy.models import Policy
from app.rag.embeddings import EmbeddingBackend
from app.rag.pipeline import (
    RagContext,
    RagState,
    run_pipeline,
    step_build_context,
    step_rerank,
    step_search,
)
from app.rag.sources import SourceEntry
from app.rag.vector_store import VectorStore
from app.trace.service import TraceContext, span
from app.utils.tokens import count_tokens

_log = logging.getLogger("orqion.agent.tools")


@dataclass(frozen=True)
class ToolSpec:
    """Описание инструмента: контракт для модели и атрибуты механизма.

    Единый реестр инструментов (решение 4 дизайн-ревью Т-503):
    встроенные инструменты и инструменты внешних серверов протокола —
    одна структура с меткой источника ``source``. Инструмент внешнего
    сервера регистрируется под неймспейсом
    ``<имя_сервера>.<имя_инструмента>``; имя сервера не содержит точку
    (валидация при создании), разбор по первой точке однозначен.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    # Пункт 9: деструктивные инструменты запрашивают подтверждение до
    # выполнения. В Т-502/Т-503 таких нет.
    destructive: bool
    # Метка источника в едином реестре: "builtin" либо "mcp:<имя_сервера>".
    source: str = "builtin"
    # Для инструментов внешних серверов: имя сервера и имя инструмента на
    # сервере (без неймспейса) — для вызова по протоколу.
    server_name: str | None = None
    mcp_tool_name: str | None = None


SEARCH_CORPUS_SPEC = ToolSpec(
    name="search_corpus",
    description=(
        "Поиск фрагментов по выбранным корпусам документов. Принимает "
        "поисковый запрос и возвращает найденные фрагменты текста. Только "
        "чтение, ничего не изменяет."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Поисковый запрос по документам корпуса",
            },
        },
        "required": ["query"],
    },
    destructive=False,
)

AGENT_TOOL_SPECS: list[ToolSpec] = [SEARCH_CORPUS_SPEC]


def get_tool_spec(name: str, specs: Sequence[ToolSpec]) -> ToolSpec | None:
    """Поиск спецификации в переданном реестре прогона.

    Реестр один на прогон (встроенные + внешние с меткой источника);
    коллизии имён исключены неймспейсингом при сборке (Т-503).
    """
    for spec in specs:
        if spec.name == name:
            return spec
    return None


def build_tool_schemas(specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Схемы инструментов реестра в формате OpenAI tools API."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in specs
    ]


def openai_tool_schemas() -> list[dict[str, Any]]:
    """Схемы встроенных инструментов (обёртка для совместимости)."""
    return build_tool_schemas(AGENT_TOOL_SPECS)


# ---------------------------------------------------------------------------
# Единый реестр прогона (Т-503, решение 4 + пункт 8 ADR-21 буквально)
# ---------------------------------------------------------------------------

# Классы данных, при которых вынос данных на внешние серверы запрещён
# (пункт 8 ADR-21): разговор уровня К2/К3 не может отправлять запросы
# инструментам вне системы.
EXTERNAL_DATA_CLASS_BLOCK = ("К2", "К3")


def external_tools_allowed(data_class: str | None) -> bool:
    """False, если класс данных разговора запрещает вынос на внешние серверы."""
    return data_class not in EXTERNAL_DATA_CLASS_BLOCK


@dataclass(frozen=True)
class ServerEndpoint:
    """Транспортные данные сервера для вызова инструментов."""

    url: str
    api_key_enc: str | None


@dataclass
class ResolvedTools:
    """Единый реестр инструментов одного прогона.

    Один список ``specs`` без параллельных веток: встроенные
    инструменты (``source="builtin"``) и инструменты внешних серверов
    (``source="mcp:<имя_сервера>"``). Диспетчер цикла ищет спецификацию
    только здесь (решение 4 дизайн-ревью Т-503).
    """

    specs: list[ToolSpec] = field(default_factory=list)
    # Транспортные данные внешних серверов, чьи инструменты вошли в реестр.
    servers: dict[str, ServerEndpoint] = field(default_factory=dict)
    # Класс данных, если внешние инструменты отклонены целиком (К2/К3).
    blocked_external: str | None = None

    def spec_by_name(self, name: str) -> ToolSpec | None:
        return get_tool_spec(name, self.specs)

    def schemas(self) -> list[dict[str, Any]]:
        return build_tool_schemas(self.specs)


@dataclass
class ToolRunContext:
    """Зависимости прогона инструментов (собирает эндпоинт агента)."""

    session: AsyncSession
    settings: Settings
    vector_store: VectorStore
    embedding_backend: EmbeddingBackend
    secret_key: str
    workspace_id: str
    user_id: str
    policy: Policy
    corpora: list[Corpus]
    corpus_names: list[str]
    corpus_data_class: str | None
    model: Model
    provider: Provider
    trace_ctx: TraceContext
    conversation_id: str | None = None


@dataclass
class ToolOutcome:
    """Результат вызова инструмента — то, что увидит модель."""

    decision: str  # "allow" | "deny"
    text: str
    sources: list[SourceEntry] = field(default_factory=list)
    fragments_used: int = 0


@dataclass
class _ToolAction:
    """Действие для повторной проверки ``enforce`` внутри инструмента.

    Те же поля, что у действия чата: отказ по ``policy.corpora`` получается
    тем же кодом и в той же форме (с перечнем недоступных корпусов), что и
    в обычном чате — тихая фильтрация подмножества исключена построением.
    """

    model_alias: str
    model_locality: str
    input_tokens: int
    output_tokens: int
    corpus_data_class: str | None
    corpus_name: str | None
    corpus_names: list[str] | None = None


def _refusal_text(exc: OrqionError) -> str:
    """Текст отказа для модели — та же причина и перечень, что у чата."""
    parts = [exc.reason]
    constraint = exc.constraint or {}
    disallowed = constraint.get("corpora")
    if isinstance(disallowed, list) and disallowed:
        parts.append(f"Недоступные корпуса: {', '.join(str(c) for c in disallowed)}.")
    allowed = constraint.get("allowed")
    if isinstance(allowed, list):
        parts.append(f"Разрешено политикой: {', '.join(str(a) for a in allowed) or 'ничего'}.")
    if exc.hint:
        parts.append(exc.hint)
    return " ".join(parts)


async def execute_search_corpus(query: str, tctx: ToolRunContext) -> ToolOutcome:
    """Поиск по корпусам с гарантиями ADR-21 (пункт 7).

    Порядок: повторная проверка ``policy.corpora`` (отказ — тот же, что в
    чате) → поиск/реранкинг/сборка контекста существующим конвейером →
    дуальная запись: полный состав в span, компактный факт в журнал аудита.
    """
    decision = "allow"
    outcome_text = ""
    sources: list[SourceEntry] = []
    fragments_used = 0
    span_payload: dict[str, object] = {
        "tool": SEARCH_CORPUS_SPEC.name,
        "args": {"query": query},
        "corpus_names": list(tctx.corpus_names),
        "data_class": tctx.corpus_data_class,
    }

    # 1. Проверка доступности корпусов — тот же вызов, что в обычном чате.
    #    Отказ возвращается модели текстом (агент сам передаст его
    #    пользователю), факт отказа фиксируется в журнале аудита.
    try:
        enforce(
            tctx.policy,
            _ToolAction(
                model_alias=tctx.model.alias,
                model_locality=tctx.model.locality,
                input_tokens=count_tokens(query),
                output_tokens=0,
                corpus_data_class=tctx.corpus_data_class,
                corpus_name=tctx.corpus_names[0] if tctx.corpus_names else None,
                corpus_names=tctx.corpus_names or None,
            ),
        )
    except OrqionError as exc:
        decision = "deny"
        outcome_text = _refusal_text(exc)
        span_payload["decision"] = decision
        span_payload["refusal"] = exc.error_code
        async with span(tctx.trace_ctx, "agent.tool.search_corpus", payload=span_payload):
            await write_audit(
                tctx.session,
                workspace_id=tctx.workspace_id,
                actor_user_id=tctx.user_id,
                action="agent.tool.search_corpus",
                object_type="agent_tool",
                object_id=tctx.conversation_id,
                meta={
                    "decision": decision,
                    "data_class": tctx.corpus_data_class,
                    "corpus_names": list(tctx.corpus_names),
                    "error_code": exc.error_code,
                },
            )
        _log.info(
            "agent tool search_corpus denied: user=%s code=%s",
            tctx.user_id,
            exc.error_code,
        )
        return ToolOutcome(decision=decision, text=outcome_text)

    # 2. Поиск существующим конвейером без генерации.
    rag_state = RagState(
        query=query,
        trace_id=tctx.trace_ctx.trace_id if tctx.trace_ctx else "",
    )
    rag_ctx = RagContext(
        session=tctx.session,
        settings=tctx.settings,
        vector_store=tctx.vector_store,
        embedding_backend=tctx.embedding_backend,
        secret_key=tctx.secret_key,
        workspace_id=tctx.workspace_id,
        index_version_id=tctx.corpora[0].active_index_version_id or "" if tctx.corpora else "",
        index_version_ids=[c.active_index_version_id or "" for c in tctx.corpora],
        corpus_attribution={c.active_index_version_id or "": (c.id, c.name) for c in tctx.corpora},
        model=tctx.model,
        provider=tctx.provider,
        trace_ctx=tctx.trace_ctx,
    )
    rag_state = await run_pipeline(
        rag_state,
        rag_ctx,
        steps=[step_search, step_rerank, step_build_context],
    )

    sources = rag_state.sources
    fragments_used = rag_state.fragments_used
    context = (rag_state.context or "").strip()
    # Контекст может содержать дежурную фразу и при нуле фрагментов —
    # источник истины о наличии материала это ``fragments_used``.
    if context and fragments_used > 0:
        outcome_text = f"Найдено фрагментов в корпусах: {fragments_used}.\n\n{context}"
    else:
        outcome_text = "По запросу в выбранных корпусах фрагментов не найдено."
    if rag_state.degraded:
        span_payload["degraded"] = True
        span_payload["errors"] = list(rag_state.errors)

    # 3. Дуальная запись: полный состав вызова в span (досылается ниже),
    #    компактный факт — в журнал аудита.
    span_payload.update(
        {
            "decision": decision,
            "fragments_used": fragments_used,
            "sources": [{"chunk_id": s.chunk_id, "document_id": s.document_id} for s in sources],
        }
    )
    async with span(tctx.trace_ctx, "agent.tool.search_corpus", payload=span_payload):
        await write_audit(
            tctx.session,
            workspace_id=tctx.workspace_id,
            actor_user_id=tctx.user_id,
            action="agent.tool.search_corpus",
            object_type="agent_tool",
            object_id=tctx.conversation_id,
            meta={
                "decision": decision,
                "data_class": tctx.corpus_data_class,
                "corpus_names": list(tctx.corpus_names),
                "fragments_used": fragments_used,
            },
        )

    return ToolOutcome(
        decision=decision,
        text=outcome_text,
        sources=sources,
        fragments_used=fragments_used,
    )


async def execute_mcp_tool(
    spec: ToolSpec,
    args: dict[str, Any],
    tctx: ToolRunContext,
    registry: ResolvedTools,
) -> ToolOutcome:
    """Вызов инструмента внешнего сервера протокола (Т-503).

    Гарантии ADR-21 буквально, как у встроенного поиска:

    - отклонение выноса данных для К2/К3 ДО вызова (пункт 8): даже если
      спецификация каким-то путём оказалась в реестре, вызов не
      выполняется;
    - дуальная запись каждого вызова: полный состав — в span
      трассировки; компактный факт (разрешено/отклонено, класс данных,
      инструмент, сервер) — в журнал аудита бессрочно;
    - сбой транспорта не роняет прогон (решение 6): модель получает
      текст ошибки инструмента.
    """
    from app.mcp.client import call_tool, decrypt_server_connection

    decision = "allow"
    outcome_text = ""
    span_payload: dict[str, object] = {
        "tool": spec.name,
        "server": spec.server_name,
        "args": args,
        "data_class": tctx.corpus_data_class,
    }

    # 1. Отказ выноса данных К2/К3 до вызова — защита построением и
    #    защитой в глубине: реестр прогона не содержит внешних
    #    инструментов при К2/К3, но проверка повторяется в точке вызова.
    if not external_tools_allowed(tctx.corpus_data_class):
        decision = "deny"
        outcome_text = (
            f"Вызов внешнего инструмента '{spec.name}' отклонён: класс данных "
            "разговора К2/К3 запрещает вынос данных на внешние серверы."
        )
        span_payload["decision"] = decision
        async with span(tctx.trace_ctx, f"agent.tool.{spec.name}", payload=span_payload):
            await write_audit(
                tctx.session,
                workspace_id=tctx.workspace_id,
                actor_user_id=tctx.user_id,
                action="agent.tool.mcp",
                object_type="agent_tool",
                object_id=tctx.conversation_id,
                meta={
                    "decision": decision,
                    "data_class": tctx.corpus_data_class,
                    "tool": spec.name,
                    "server_name": spec.server_name,
                },
            )
        _log.info(
            "agent mcp tool denied by data class: user=%s tool=%s class=%s",
            tctx.user_id,
            spec.name,
            tctx.corpus_data_class,
        )
        return ToolOutcome(decision=decision, text=outcome_text)

    endpoint = registry.servers.get(spec.server_name or "")
    if endpoint is None or not spec.mcp_tool_name:
        # Спецификация есть, транспорта нет — ошибка сборки; прогон не падает.
        span_payload["decision"] = "allow"
        span_payload["error"] = "no_endpoint"
        async with span(tctx.trace_ctx, f"agent.tool.{spec.name}", payload=span_payload):
            pass
        return ToolOutcome(
            decision="allow",
            text=f"Инструмент '{spec.name}' временно недоступен: сервер не настроен.",
        )

    # 2. Вызов по протоколу: сбой транспорта возвращается модели текстом.
    error_text: str | None = None
    result_is_error = False
    try:
        conn = decrypt_server_connection(endpoint.url, endpoint.api_key_enc, tctx.secret_key)
        result = await call_tool(
            conn,
            spec.mcp_tool_name,
            args,
            timeout=tctx.settings.mcp_call_timeout,
        )
        outcome_text = result.text or "Инструмент вернул пустой ответ."
        result_is_error = result.is_error
    except Exception as exc:  # noqa: BLE001 сбой сервера — факт прогона, не падение
        error_text = f"{type(exc).__name__}: {exc}"
        outcome_text = f"Внешний инструмент '{spec.name}' недоступен: {type(exc).__name__}."

    # 3. Дуальная запись: полный состав в span, компактный факт — в журнал.
    span_payload["decision"] = decision
    if error_text is not None:
        span_payload["error"] = error_text
    else:
        span_payload["result_chars"] = len(outcome_text)
        span_payload["result_is_error"] = result_is_error
    async with span(tctx.trace_ctx, f"agent.tool.{spec.name}", payload=span_payload):
        meta: dict[str, object] = {
            "decision": decision,
            "data_class": tctx.corpus_data_class,
            "tool": spec.name,
            "server_name": spec.server_name,
        }
        if error_text is not None:
            meta["error"] = error_text
        await write_audit(
            tctx.session,
            workspace_id=tctx.workspace_id,
            actor_user_id=tctx.user_id,
            action="agent.tool.mcp",
            object_type="agent_tool",
            object_id=tctx.conversation_id,
            meta=meta,
        )
    if error_text is not None:
        _log.info(
            "agent mcp tool call failed: user=%s tool=%s error=%s",
            tctx.user_id,
            spec.name,
            error_text,
        )

    return ToolOutcome(decision=decision, text=outcome_text)
