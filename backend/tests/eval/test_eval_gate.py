"""CI-гейт оценки RAG (T-227).

ADR-10, Gate 2: «набор оценки прогоняется в CI, метрики сохраняются между
версиями индекса».

Этот тест УМЫШЛЕННО ставит orqion[full] (pip install -e .[full]) — ради
реального измерения качества поиска (bge-m3, bge-reranker-v2-m3).
Фейковый эмбеддинг превратил бы гейт в «код не упал», а не «качество не упало».
Не оптимизировать обратно на лёгкий вариант без понимания, зачем он тяжёлый.

Пороги хранятся в tests/fixtures/eval_thresholds.json — в репозитории,
но не в коде поставки (backend/app/).

grounded_refusal_ratio НЕ проверяется: generate замокан, метрика зависит
от реального текста ответа модели.

Локально (без [full]) тест skip. В CI (ORQION_EVAL_GATE=1) — обязательный
прогон с реальными моделями. Если [full] или модели недоступны — явный fail,
не тихая деградация.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Маркер: тесты оценки — только с [full]
_EVAL_GATE = os.environ.get("ORQION_EVAL_GATE", "") == "1"


@pytest.mark.skipif(
    not _EVAL_GATE,
    reason="Eval gate требует orqion[full] (bge-m3, bge-reranker-v2-m3). "
    "Установите ORQION_EVAL_GATE=1 и pip install -e .[full] для запуска.",
)
@pytest.mark.asyncio
async def test_eval_gate_recall_threshold() -> None:
    """Прогон оценки с реальным pipeline — recall@5, MRR, cited_sources ≥ порогов.

    Шаги:
    1. Создать workspace, корпус, загрузить документы из фикстуры.
    2. Построить индекс (реальные эмбеддинги bge-m3).
    3. Создать eval_set из golden set.
    4. Запустить run_eval (реальный search/rerank, мок generate).
    5. Проверить метрики ≥ порогов из eval_thresholds.json.
    """
    # Проверка что [full] реально установлен
    import importlib.util

    if importlib.util.find_spec("FlagEmbedding") is None:
        pytest.fail(
            "orqion[full] не установлен. Eval gate требует реальные ML-модели. "
            "Install: pip install -e .[full]"
        )

    from app.config import Settings
    from app.crypto.service import encrypt_api_key
    from app.db.base import Base
    from app.db.models import (
        Chunk,
        Corpus,
        Document,
        EvalItem,
        EvalSet,
        IndexVersion,
        Model,
        Provider,
        Workspace,
    )
    from app.rag.embeddings import LocalEmbeddingBackend
    from app.rag.eval_runner import run_eval
    from app.rag.vector_store import SQLiteVectorStore
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # Загружаем пороги
    thresholds_path = FIXTURES / "eval_thresholds.json"
    with thresholds_path.open() as f:
        thresholds = json.load(f)

    # Загружаем golden set
    golden_path = FIXTURES / "eval_golden_set.jsonl"
    golden_entries: list[dict[str, str]] = []
    with golden_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                golden_entries.append(json.loads(line))

    # Создаём in-memory БД
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Workspace
        ws = Workspace(name="eval-gate-ws")
        session.add(ws)
        await session.flush()

        # Provider + Model
        provider = Provider(
            workspace_id=ws.id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", "test-secret-key-32chars!!"),
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = Model(
            workspace_id=ws.id,
            provider_id=provider.id,
            alias="local/test-model",
            upstream_name="test-model",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(model)
        await session.flush()

        # Corpus
        corpus = Corpus(workspace_id=ws.id, name="eval-gate-corpus")
        session.add(corpus)
        await session.flush()

        # IndexVersion
        iv = IndexVersion(
            workspace_id=ws.id,
            corpus_id=corpus.id,
            embedding_model="BAAI/bge-m3",
            chunker="code",
            chunker_version="1",
            status="active",
        )
        session.add(iv)
        await session.flush()
        corpus.active_index_version_id = iv.id

        # Документы и чанки из golden set
        chunk_ids: list[str] = []
        doc_ids: list[str] = []
        for i, entry in enumerate(golden_entries):
            doc = Document(
                workspace_id=ws.id,
                corpus_id=corpus.id,
                blob_uri=f"sha256-eval-{i:061d}",
                filename=entry["filepath"],
                mime="text/x-python",
                sha256=f"sha256-eval-{i:061d}",
                source_type="upload",
                status="indexed",
            )
            session.add(doc)
            await session.flush()
            doc_ids.append(doc.id)

            chunk = Chunk(
                workspace_id=ws.id,
                index_version_id=iv.id,
                document_id=doc.id,
                ordinal=i,
                text=entry["func_code"],
                meta={
                    "document_filename": entry["filepath"],
                    "chunker": "code",
                    "symbol": entry["func_code"].split("(")[0].replace("def ", ""),
                },
            )
            session.add(chunk)
            await session.flush()
            chunk_ids.append(chunk.id)

        # EvalSet с элементами — expected_doc_ids = [doc_id]
        eval_set = EvalSet(
            workspace_id=ws.id,
            corpus_id=corpus.id,
            name="ci-golden-set",
        )
        session.add(eval_set)
        await session.flush()

        for i, entry in enumerate(golden_entries):
            item = EvalItem(
                workspace_id=ws.id,
                eval_set_id=eval_set.id,
                question=entry["question"],
                expected_doc_ids=[doc_ids[i]],
                expected_answer=entry["func_code"],
            )
            session.add(item)

        await session.commit()

    # Реальные эмбеддинги и векторное хранилище
    settings = Settings()
    embedding_backend = LocalEmbeddingBackend(settings.embeddings_model)
    vector_store = SQLiteVectorStore(":memory:")

    # Мокаем generate (не тратит токены в CI)
    from unittest.mock import AsyncMock, patch

    mock_complete = AsyncMock(
        return_value={
            "choices": [{"message": {"content": "Answer based on context"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )

    async with session_factory() as session:
        with patch.object(
            "app.providers.client.ProviderClient",
            "complete",
            mock_complete,
        ):
            eval_run = await run_eval(
                session=session,
                workspace_id=ws.id,
                eval_set_id=eval_set.id,
                index_version_id=iv.id,
                settings=settings,
                vector_store=vector_store,
                embedding_backend=embedding_backend,
                secret_key="test-secret-key-32chars!!",
                model=model,
                provider=provider,
            )
            await session.commit()

    # Проверяем пороги
    metrics = eval_run.metrics or {}
    for metric_name, threshold in thresholds.items():
        value = metrics.get(metric_name)
        assert value is not None, f"Metric {metric_name} not found in eval_run metrics"
        assert value >= threshold, (
            f"Eval gate FAILED: {metric_name}={value} < threshold={threshold}. "
            f"Quality regression detected. Full metrics: {metrics}"
        )

    # Метрики должны быть вычислены
    assert metrics.get("total_items") == len(golden_entries)

    await vector_store.close()
    await engine.dispose()
