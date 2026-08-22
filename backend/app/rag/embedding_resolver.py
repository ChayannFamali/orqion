"""Разрешение embedding backend по настройкам (T-430).

Общая логика для lifespan (main.py) и CLI (cli.py): если
embeddings_backend=provider — резолвит embeddings_model_alias через
БД в Model+Provider и конструирует ProviderEmbeddingBackend. Если
local — возвращает LocalEmbeddingBackend (без проверки [full] —
ленивый ImportError допустим для T-013; проверка происходит при
первом embed(), сообщение в формате §7.3).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.crypto.service import decrypt_api_key
from app.db.models import Model, Provider
from app.rag.embeddings import EmbeddingBackend, LocalEmbeddingBackend, ProviderEmbeddingBackend


class EmbeddingConfigError(RuntimeError):
    """Невалидная конфигурация embedding backend — причина + способ действия."""


async def resolve_embedding_backend(
    settings: Settings,
    session: AsyncSession,
    workspace_id: str,
    secret_key: str,
) -> EmbeddingBackend:
    """Резолвит embedding_backend по настройкам.

    Args:
        settings: настройки приложения.
        session: активная SQLAlchemy-сессия (для резолва alias).
        workspace_id: workspace для поиска модели.
        secret_key: ключ для расшифровки api_key провайдера.

    Returns:
        LocalEmbeddingBackend или ProviderEmbeddingBackend.

    Raises:
        EmbeddingConfigError: если embeddings_backend=provider, но
            alias не задан, модель не найдена, или провайдер отключён.
            Сообщение следует §7.3: причина + способ действия.
    """
    if settings.embeddings_backend != "provider":
        return LocalEmbeddingBackend(settings.embeddings_model)

    if not settings.embeddings_model_alias:
        raise EmbeddingConfigError(
            "embeddings_backend=provider, но embeddings_model_alias не задан. "
            "Укажите ORQION_EMBEDDINGS_MODEL_ALIAS (алиас модели с поддержкой "
            "/v1/embeddings) или установите ORQION_EMBEDDINGS_BACKEND=local."
        )

    model_row = await session.execute(
        select(Model).where(
            Model.workspace_id == workspace_id,
            Model.alias == settings.embeddings_model_alias,
            Model.enabled.is_(True),
        )
    )
    model = model_row.scalar_one_or_none()
    if model is None:
        raise EmbeddingConfigError(
            f"embeddings_model_alias='{settings.embeddings_model_alias}' "
            "not found or disabled. Зарегистрируйте модель с этим алиасом "
            "или укажите существующий ORQION_EMBEDDINGS_MODEL_ALIAS."
        )

    provider = await session.get(Provider, model.provider_id)
    if provider is None or not provider.enabled:
        raise EmbeddingConfigError(
            f"provider for embeddings_model_alias='{settings.embeddings_model_alias}' "
            "not found or disabled. Включите провайдера или укажите "
            "другой ORQION_EMBEDDINGS_MODEL_ALIAS."
        )

    api_key = decrypt_api_key(provider.api_key_enc, secret_key) if provider.api_key_enc else None
    return ProviderEmbeddingBackend(
        base_url=provider.base_url,
        model=model.upstream_name,
        api_key=api_key,
    )
