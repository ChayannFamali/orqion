"""Конфигурация приложения на pydantic-settings, префикс ORQION_."""

from __future__ import annotations

import secrets
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки orqion. Все дефолты работают без переменных окружения (профиль minimal)."""

    model_config = SettingsConfigDict(
        env_prefix="ORQION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "orqion"
    profile: str = "minimal"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    database_url: str = "sqlite:///./orqion.db"

    vector_store: str = "sqlite-vec"
    vector_store_path: str = "./data/vec.db"
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    blob_store_path: str = "./data/blobs"

    # S3-совместимое хранилище (профиль standard/full)
    blob_store_backend: str = "local"  # local | s3
    s3_endpoint_url: str = ""
    s3_bucket: str = "orqion-blobs"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    embeddings_model: str = "BAAI/bge-m3"
    embeddings_backend: str = "local"

    secret_key: str | None = Field(default=None)

    session_cookie_secure: bool = False
    session_ttl_days: int = 7

    probe_interval_seconds: int = 900

    login_max_attempts: int = 5
    login_rate_period_seconds: int = 300

    # Загрузка документов (T-204)
    max_upload_size_mb: int = 50
    allowed_upload_extensions: str = ".pdf,.docx,.pptx,.xlsx,.py,.cpp,.ts,.go,.java,.sql,.md,.txt"

    # Переформулировка запроса (T-218)
    rag_query_reformulation_enabled: bool = False
    rag_reformulation_model_alias: str = ""

    # OIDC (T-404b). По умолчанию отключён — локальный вход работает без env.
    oidc_enabled: bool = False
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_issuer: str = ""
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/oidc/callback"
    oidc_group_role_map: str = "{}"
    oidc_default_role: str = "support"

    # OIDC sync (T-405). Отдельно от oidc_enabled: хранит долгоживущий
    # refresh_token per-user — новый класс секрета. По умолчанию отключён.
    oidc_sync_enabled: bool = False
    oidc_sync_interval_seconds: int = 300

    # Retention (T-406). arch.md §5.3. 0 = бессрочно.
    span_retention_days: int = 30
    usage_event_retention_days: int = 90
    message_retention_days: int = 0
    retention_cleanup_interval_seconds: int = 3600

    # Технические метрики (T-407). arch.md ADR-16. Профиль full.
    # extras: orqion[metrics] (prometheus-client). Ленивый импорт.
    # Включение без установленного extras — явный fail при старте.
    metrics_enabled: bool = False

    # DLP-детекторы (T-409). arch.md ADR-13. По умолчанию выключены.
    # Плагины для обнаружения персональных данных/секретов в исходящих
    # запросах к внешним провайдерам. Страховка от случайной вставки,
    # не средство защиты от умысла.
    detectors_enabled: bool = False

    def model_post_init(self, __context: object, /) -> None:
        """Вычисляет session_cookie_secure после загрузки настроек.

        Если ORQION_SESSION_COOKIE_SECURE явно задана в окружении — используется она.
        Иначе: minimal → False (local, no TLS), standard/full → True (network, TLS).
        """
        import os

        if "ORQION_SESSION_COOKIE_SECURE" not in os.environ:
            self.session_cookie_secure = self.profile != "minimal"
        super().model_post_init(__context)


def get_or_create_secret_key(settings: Settings, data_dir: Path) -> str:
    """Возвращает секретный ключ: из настроек, из файла, или создаёт новый.

    При ORQION_SECRET_KEY в окружении — используется он.
    Иначе ключ читается из data_dir/.secret_key; при отсутствии — генерируется
    и записывается с правами 0o600.
    """
    if settings.secret_key:
        return settings.secret_key

    key_path = data_dir / ".secret_key"
    if key_path.exists():
        return key_path.read_text().strip()

    key = secrets.token_urlsafe(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key)
    key_path.chmod(0o600)
    return key
