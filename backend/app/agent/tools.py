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
    """Описание инструмента: контракт для модели и атрибуты механизма."""

    name: str
    description: str
    parameters: dict[str, Any]
    # Пункт 9: деструктивные инструменты запрашивают подтверждение до
    # выполнения. В Т-502 таких нет.
    destructive: bool


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
_SPECS_BY_NAME: dict[str, ToolSpec] = {s.name: s for s in AGENT_TOOL_SPECS}


def get_tool_spec(name: str) -> ToolSpec | None:
    return _SPECS_BY_NAME.get(name)


def openai_tool_schemas() -> list[dict[str, Any]]:
    """Схемы инструментов в формате OpenAI tools API."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in AGENT_TOOL_SPECS
    ]


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
