"""Т-502: цикл «модель → инструменты → модель» на LangGraph.

Решения пересмотренного дизайн-ревью:

- пункт 2 — синхронный цикл в одном запросе; фоновые задачи, чекпоинты и
  восстановление после рестарта вне скоупа;
- пункт 4 — лимиты прогона (число вызовов модели и суммарные токены) из
  конфигурации, дополнительный предохранитель поверх биллинга;
- пункт 5 — минимальный граф: один цикл, без подграфов и параллельных
  веток;
- пункт 8 — каждый вызов модели внутри цикла проходит ``enforce`` и
  ``record_usage`` идентично обычному запросу чата;
- пункт 9 — деструктивный инструмент останавливает прогон до выполнения и
  возвращает запрос подтверждения (в Т-502 таких инструментов нет).

Все импорты ``langgraph``/``langchain_core`` — ленивые, внутри функций:
без дополнения ``orqion[agent]`` модуль импортируется, а эндпоинт честно
сообщает о недоступности (паттерн Т-444/Т-505).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.agent.tools import (
    SEARCH_CORPUS_SPEC,
    ToolOutcome,
    ToolRunContext,
    execute_search_corpus,
    get_tool_spec,
    openai_tool_schemas,
)
from app.config import Settings
from app.db.models import Corpus, Model, Provider, User
from app.errors import AgentRunLimitExceeded
from app.policy.enforce import enforce_all
from app.policy.models import Policy
from app.policy.rate_limiter import RateLimiter
from app.providers.client import ProviderClient
from app.providers.errors import normalize_error
from app.rag.embeddings import EmbeddingBackend
from app.rag.sources import SourceEntry
from app.rag.vector_store import VectorStore
from app.trace.service import TraceContext, span
from app.usage.service import UsageRecord, calculate_cost, record_usage
from app.utils.tokens import count_tokens

_log = logging.getLogger("orqion.agent.loop")

AGENT_SYSTEM_PROMPT = (
    "Вы — агент-ассистент с доступом к инструментам. Для ответа на вопрос "
    "по документам сначала вызовите инструмент поиска, затем отвечайте "
    "только на основании найденных фрагментов. Если фрагменты не найдены "
    "или доступ к корпусу запрещён — честно сообщите об этом пользователю, "
    "ничего не выдумывайте. Отвечайте на языке пользователя."
)


@dataclass
class AgentRunConfig:
    """Все зависимости одного агентного прогона (собирает эндпоинт)."""

    session: Any  # AsyncSession
    settings: Settings
    secret_key: str
    workspace_id: str
    user: User
    policy: Policy
    model: Model
    provider: Provider
    vector_store: VectorStore
    embedding_backend: EmbeddingBackend
    corpora: list[Corpus]
    corpus_names: list[str]
    corpus_data_class: str | None
    conversation_id: str | None
    rate_limiter: RateLimiter | None
    trace_ctx: TraceContext
    max_steps: int
    max_tokens_per_run: int


@dataclass
class AgentStep:
    """Шаг прогона для ответа и меты сообщения."""

    index: int
    kind: str  # "model" | "tool"
    name: str | None = None
    summary: str = ""
    decision: str | None = None


@dataclass
class AgentRunResult:
    """Итог прогона."""

    content: str
    steps: list[AgentStep] = field(default_factory=list)
    sources: list[SourceEntry] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model_calls: int = 0
    pending_confirmation: dict[str, Any] | None = None


class _ConfirmationStop(Exception):
    """Деструктивный инструмент запросил подтверждение — прогон остановлен."""


@dataclass
class _AgentAction:
    """Действие для ``enforce`` на каждый вызов модели (пункт 8)."""

    model_alias: str
    model_locality: str
    input_tokens: int
    output_tokens: int
    corpus_data_class: str | None
    corpus_name: str | None
    corpus_names: list[str] | None = None


@dataclass
class _RunState:
    """Мутируемое состояние прогона внутри узлов графа."""

    cfg: AgentRunConfig
    tctx: ToolRunContext
    result: AgentRunResult
    pending_confirmation: dict[str, Any] | None = None


def _lc_messages_to_openai(messages: list[Any]) -> list[dict[str, Any]]:
    """Сообщения LangChain → словари OpenAI-формата (для провайдера)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        kind = type(m).__name__
        if kind == "SystemMessage":
            out.append({"role": "system", "content": str(m.content)})
        elif kind == "HumanMessage":
            out.append({"role": "user", "content": str(m.content)})
        elif kind == "ToolMessage":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(getattr(m, "tool_call_id", "")),
                    "content": str(m.content),
                }
            )
        elif kind == "AIMessage":
            item: dict[str, Any] = {"role": "assistant", "content": str(m.content)}
            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ]
            out.append(item)
    return out


def _history_to_lc_messages(history: list[dict[str, str]]) -> list[Any]:
    """История из запроса (роль/контент) → сообщения LangChain."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    messages: list[Any] = [SystemMessage(content=AGENT_SYSTEM_PROMPT)]
    for m in history:
        if m["role"] == "assistant":
            messages.append(AIMessage(content=m["content"]))
        else:
            messages.append(HumanMessage(content=m["content"]))
    return messages


async def _call_model_once(
    rs: _RunState,
    openai_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Один вызов модели: enforce → вызов → биллинг. Пункт 8 буквально."""
    cfg = rs.cfg
    input_text = json.dumps(openai_messages, ensure_ascii=False)
    input_tokens = count_tokens(input_text)
    output_tokens = cfg.model.max_output_tokens or 1024

    # enforce идентично обычному запросу чата (политика, лимиты, бюджет)
    await enforce_all(
        cfg.policy,
        _AgentAction(
            model_alias=cfg.model.alias,
            model_locality=cfg.model.locality,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            corpus_data_class=cfg.corpus_data_class,
            corpus_name=cfg.corpus_names[0] if cfg.corpus_names else None,
            corpus_names=cfg.corpus_names or None,
        ),
        session=cfg.session,
        user_id=cfg.user.id,
        workspace_id=cfg.workspace_id,
        rate_limiter=cfg.rate_limiter,
        model_cost_in=cfg.model.cost_in,
        model_cost_out=cfg.model.cost_out,
    )

    client = ProviderClient(cfg.provider, cfg.secret_key)
    started = time.monotonic()
    call_payload: dict[str, object] = {
        "model": cfg.model.alias,
        "input_tokens": input_tokens,
    }
    # Факт приёмки Т-502: схема инструментов фиксируется в спане — она
    # уходит в запросе к провайдеру параметром ``tools``.
    tools_schema = openai_tool_schemas()
    call_payload["tools"] = tools_schema
    async with span(
        cfg.trace_ctx, f"agent.model_call.{rs.result.model_calls}", payload=call_payload
    ):
        try:
            raw = await client.complete_tools(
                messages=openai_messages,
                model=cfg.model.upstream_name,
                tools=tools_schema,
                max_tokens=cfg.model.max_output_tokens,
                temperature=0.7,
            )
        except Exception as exc:
            err = normalize_error(exc)
            await record_usage(
                cfg.session,
                cfg.workspace_id,
                UsageRecord(
                    user_id=cfg.user.id,
                    model_id=cfg.model.id,
                    conversation_id=cfg.conversation_id,
                    message_id=None,
                    tokens_in=input_tokens,
                    tokens_out=None,
                    cost=None,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status="error",
                    error_code=err.error_code,
                ),
            )
            call_payload["error_code"] = err.error_code
            raise err from exc

    usage = raw.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    latency_ms = int((time.monotonic() - started) * 1000)

    # record_usage идентично обычному запросу чата
    await record_usage(
        cfg.session,
        cfg.workspace_id,
        UsageRecord(
            user_id=cfg.user.id,
            model_id=cfg.model.id,
            conversation_id=cfg.conversation_id,
            message_id=None,
            tokens_in=prompt_tokens,
            tokens_out=completion_tokens,
            cost=calculate_cost(
                prompt_tokens, completion_tokens, cfg.model.cost_in, cfg.model.cost_out
            ),
            latency_ms=latency_ms,
            status="ok",
        ),
    )

    rs.result.tokens_in += prompt_tokens
    rs.result.tokens_out += completion_tokens
    call_payload["prompt_tokens"] = prompt_tokens
    call_payload["completion_tokens"] = completion_tokens

    # Пункт 4: суммарные токены прогона — предохранитель поверх биллинга.
    total_tokens = rs.result.tokens_in + rs.result.tokens_out
    if total_tokens > cfg.max_tokens_per_run:
        raise AgentRunLimitExceeded(
            constraint={
                "type": "tokens",
                "limit": cfg.max_tokens_per_run,
                "used": total_tokens,
            },
            hint="Уменьшите объём запроса или переформулируйте вопрос",
        )

    message_obj = raw.get("choices", [{}])[0].get("message", {})
    message: dict[str, Any] = message_obj if isinstance(message_obj, dict) else {}
    # Факт приёмки Т-502: форма сырого ответа — вернулась ли структурированная
    # реакция вызова инструмента (``tool_calls``), а не обычный текст.
    raw_calls = message.get("tool_calls") or []
    call_payload["response_has_tool_calls"] = bool(raw_calls)
    if raw_calls:
        call_payload["tool_calls"] = [
            {
                "id": tc.get("id") if isinstance(tc, dict) else None,
                "name": tc.get("function", {}).get("name")
                if isinstance(tc, dict) and isinstance(tc.get("function"), dict)
                else None,
            }
            for tc in list(raw_calls)
        ]
    return message


def _parse_tool_calls(raw_calls: list[Any]) -> list[dict[str, Any]]:
    """tool_calls ответа провайдера → формат сообщений LangChain."""
    parsed: list[dict[str, Any]] = []
    for tc in raw_calls:
        function = tc.get("function", {}) if isinstance(tc, dict) else {}
        arguments_raw = function.get("arguments", "{}")
        try:
            args = (
                json.loads(arguments_raw) if isinstance(arguments_raw, str) else dict(arguments_raw)
            )
        except json.JSONDecodeError:
            args = {}
        parsed.append(
            {
                "name": function.get("name", ""),
                "args": args if isinstance(args, dict) else {},
                "id": tc.get("id", "") if isinstance(tc, dict) else "",
                "type": "tool_call",
            }
        )
    return parsed


async def run_agent_loop(
    cfg: AgentRunConfig,
    history: list[dict[str, str]],
) -> AgentRunResult:
    """Синхронный цикл «модель → инструменты → модель» в одном запросе.

    Возбуждает ``AgentRunLimitExceeded`` при исчерпании лимитов прогона
    (пункт 4) и доменные исключения биллинга/политики — идентично чату
    (пункт 8). Возвращает результат с шагами, источниками и, при
    остановке на деструктивном инструменте, запросом подтверждения.
    """
    from langchain_core.messages import AIMessage, ToolMessage
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import MessagesState

    result = AgentRunResult(content="")
    tctx = ToolRunContext(
        session=cfg.session,
        settings=cfg.settings,
        vector_store=cfg.vector_store,
        embedding_backend=cfg.embedding_backend,
        secret_key=cfg.secret_key,
        workspace_id=cfg.workspace_id,
        user_id=cfg.user.id,
        policy=cfg.policy,
        corpora=cfg.corpora,
        corpus_names=cfg.corpus_names,
        corpus_data_class=cfg.corpus_data_class,
        model=cfg.model,
        provider=cfg.provider,
        trace_ctx=cfg.trace_ctx,
        conversation_id=cfg.conversation_id,
    )
    rs = _RunState(cfg=cfg, tctx=tctx, result=result)

    async def model_node(state: Any) -> dict[str, list[Any]]:
        result.model_calls += 1
        # Пункт 4: число шагов — предохранитель поверх биллинга.
        if result.model_calls > cfg.max_steps:
            raise AgentRunLimitExceeded(
                constraint={
                    "type": "steps",
                    "limit": cfg.max_steps,
                    "used": result.model_calls,
                },
                hint="Сократите число уточнений или упростите вопрос",
            )

        openai_messages = _lc_messages_to_openai(list(state["messages"]))
        message = await _call_model_once(rs, openai_messages)

        raw_calls = message.get("tool_calls") or []
        content = message.get("content") or ""
        if raw_calls:
            tool_calls = _parse_tool_calls(list(raw_calls))
            names = ", ".join(tc["name"] for tc in tool_calls)
            result.steps.append(
                AgentStep(
                    index=len(result.steps) + 1,
                    kind="model",
                    summary=f"Запрошены инструменты: {names}",
                )
            )
            return {"messages": [AIMessage(content=content, tool_calls=tool_calls)]}

        result.steps.append(
            AgentStep(
                index=len(result.steps) + 1,
                kind="model",
                summary=f"Финальный ответ ({len(content)} симв.)",
            )
        )
        return {"messages": [AIMessage(content=content)]}

    async def tools_node(state: Any) -> dict[str, list[Any]]:
        last = state["messages"][-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        outs: list[Any] = []
        for tc in tool_calls:
            name = str(tc.get("name", ""))
            args = tc.get("args", {}) if isinstance(tc.get("args"), dict) else {}
            call_id = str(tc.get("id", ""))

            spec = get_tool_spec(name)
            if spec is None:
                outs.append(
                    ToolMessage(
                        content=f"Инструмент '{name}' не существует.",
                        tool_call_id=call_id,
                    )
                )
                continue

            # Пункт 9: деструктивный инструмент — остановка до выполнения.
            if spec.destructive:
                rs.pending_confirmation = {
                    "call_id": call_id,
                    "tool": name,
                    "args": args,
                }
                raise _ConfirmationStop()

            if name == SEARCH_CORPUS_SPEC.name:
                query = str(args.get("query", "")).strip()
                outcome: ToolOutcome = await execute_search_corpus(query, tctx)
                result.steps.append(
                    AgentStep(
                        index=len(result.steps) + 1,
                        kind="tool",
                        name=name,
                        summary=(
                            f"Фрагментов: {outcome.fragments_used}"
                            if outcome.decision == "allow"
                            else "Отказ политики"
                        ),
                        decision=outcome.decision,
                    )
                )
                result.sources.extend(outcome.sources)
                outs.append(ToolMessage(content=outcome.text, tool_call_id=call_id))

        return {"messages": outs}

    def route_after_model(state: Any) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        # Без установленного дополнения ``END`` типизирован как Any —
        # возврат через объявленную переменную.
        end_node: str = END
        return end_node

    builder = StateGraph(MessagesState)
    builder.add_node("model", model_node)
    builder.add_node("tools", tools_node)
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", route_after_model)
    builder.add_edge("tools", "model")
    graph = builder.compile()

    initial_messages = _history_to_lc_messages(history)

    try:
        # recursion_limit — страховочный потолок поверх собственных лимитов:
        # пара «модель + инструменты» расходует два супершага на итерацию.
        final = await graph.ainvoke(
            {"messages": initial_messages},
            config={"recursion_limit": cfg.max_steps * 2 + 2},
        )
    except GraphRecursionError as exc:
        raise AgentRunLimitExceeded(
            constraint={"type": "steps", "limit": cfg.max_steps},
            hint="Сократите число уточнений или упростите вопрос",
        ) from exc
    except _ConfirmationStop:
        result.pending_confirmation = rs.pending_confirmation
        _log.info(
            "agent run stopped on destructive tool confirmation: user=%s",
            cfg.user.id,
        )
        return result

    # Финальный ответ — последнее сообщение ассистента без запроса инструментов.
    for m in reversed(final["messages"]):
        if type(m).__name__ == "AIMessage" and not getattr(m, "tool_calls", None):
            result.content = str(m.content)
            break

    return result
