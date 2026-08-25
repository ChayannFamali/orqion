"""usage_daily: убираем пережившие 0021 SQLite-FK на user/model (BUG-019).

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-25

Миграция 0021 (фикс BUG-008) убрала внешние ключи
``usage_daily.user_id`` / ``usage_daily.model_id`` на PostgreSQL, но на
SQLite оставила их с обоснованием «SQLite не енфорсит внешние ключи по
умолчанию». Предпосылка неверна для orqion: ``app/db/engine.py`` включает
``PRAGMA foreign_keys=ON`` на каждом соединении, поэтому ключи енфорсились.
Итог: удаление модели, имевшей историю в ``usage_daily``, падало
``FOREIGN KEY constraint failed`` → HTTP 500 (вместо переноса строк на
сентинел ``NIL_ID``, как задумано в BUG-008/T-443).

Миграция приводит схему SQLite к ``models.py`` и PostgreSQL: у
``usage_daily`` остаётся только FK на ``workspace``; ``user_id``/``model_id``
хранят реальные идентификаторы либо сентинел ``NIL_ID`` (агрегат хранится
бессрочно — §5.3, переживает удаление пользователей/моделей).

Техника: в SQLite нельзя удалить отдельный безымянный внешний ключ —
таблица пересоздаётся (create → copy → drop → rename) с точным повтором
схемы 0010+0021 минус два FK. Данные копируются.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "workspace_id, date, user_id, model_id, "
    "requests, tokens_in, tokens_out, cost, errors, avg_latency_ms"
)


def _table_sql(with_user_model_fks: bool) -> str:
    fk_lines = ""
    if with_user_model_fks:
        fk_lines = (
            ',\n    FOREIGN KEY (user_id) REFERENCES "user" (id)'
            ",\n    FOREIGN KEY (model_id) REFERENCES model (id)"
        )
    return f"""CREATE TABLE usage_daily_new (
    workspace_id VARCHAR(36) NOT NULL,
    date VARCHAR(10) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    model_id VARCHAR(36) NOT NULL,
    requests INTEGER DEFAULT '0' NOT NULL,
    tokens_in INTEGER DEFAULT '0' NOT NULL,
    tokens_out INTEGER DEFAULT '0' NOT NULL,
    cost FLOAT DEFAULT '0.0' NOT NULL,
    errors INTEGER DEFAULT '0' NOT NULL,
    avg_latency_ms INTEGER,
    PRIMARY KEY (workspace_id, date, user_id, model_id),
    FOREIGN KEY (workspace_id) REFERENCES workspace (id){fk_lines}
)"""


def _rebuild_usage_daily(with_user_model_fks: bool) -> None:
    """Пересоздаёт usage_daily без потери данных (только SQLite)."""
    op.execute(_table_sql(with_user_model_fks))
    op.execute(f"INSERT INTO usage_daily_new ({_COLUMNS}) SELECT {_COLUMNS} FROM usage_daily")
    op.execute("DROP TABLE usage_daily")
    op.execute("ALTER TABLE usage_daily_new RENAME TO usage_daily")
    op.execute("CREATE INDEX ix_usage_daily_workspace_id ON usage_daily (workspace_id)")
    op.execute("CREATE INDEX ix_usage_daily_date ON usage_daily (date)")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _rebuild_usage_daily(with_user_model_fks=False)
    # PostgreSQL: FK уже убраны миграцией 0021 — схема уже корректна.


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _rebuild_usage_daily(with_user_model_fks=True)
    # PostgreSQL: downgrade 0021 возвращает FK; здесь делать нечего.
