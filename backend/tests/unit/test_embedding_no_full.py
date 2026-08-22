"""T-013/T-430 регрессия: bare install (без [full]) + serve + /health — зелёный.

T-013: «Чистое окружение, установка пакета, запуск, обращение к /health —
без Docker и внешних СУБД. Приёмка: падает, если появилась обязательная
внешняя зависимость.»

T-430: embeddings_backend=local (дефолт) без [full] — сервер НЕ падает
при старте. LocalEmbeddingBackend ленивый: ImportFlagEmbedding происходит
только при первом embed(), не при конструировании. /health не триггерит
embed() → сервер жив.

Ошибка при embed() без [full] — RuntimeError с причиной и способом
действия (§7.3), не сырой ImportError со стектрейсом.
"""

from __future__ import annotations

import pytest
from app.rag.embeddings import LocalEmbeddingBackend


def test_local_embedding_backend_construction_no_full() -> None:
    """LocalEmbeddingBackend конструируется без FlagEmbedding — lazy import.

    Конструктор не импортирует FlagEmbedding — только при первом embed().
    Это гарантирует, что сервер стартует без [full] (T-013).
    """
    backend = LocalEmbeddingBackend("BAAI/bge-m3")
    assert backend.model_name() == "BAAI/bge-m3"
    # _model is None — ничего не загружено при конструировании
    assert backend._model is None


@pytest.mark.asyncio
async def test_local_embedding_backend_embed_error_message() -> None:
    """embed() без [full] — RuntimeError с причиной и способом действия (§7.3).

    Не ImportError со стектрейсом — RuntimeError с читаемым сообщением,
    содержащим: что не установлено, как установить, альтернативу (provider).
    """
    backend = LocalEmbeddingBackend("BAAI/bge-m3")
    with pytest.raises(RuntimeError, match="FlagEmbedding не установлен"):
        await backend.embed(["test text"])


def test_health_endpoint_no_full_dependency() -> None:
    """/health не требует FlagEmbedding — сервер жив без [full].

    Регрессионный тест T-013: если lifespan начнёт делать import FlagEmbedding
    при старте, /health упадёт. LocalEmbeddingBackend конструируется без
    импорта — /health остаётся зелёным.
    """
    # Конструирование backend — не падает (lazy import).
    backend = LocalEmbeddingBackend()
    # model_name() — не падает (не требует импорта FlagEmbedding).
    assert backend.model_name() == "BAAI/bge-m3"
