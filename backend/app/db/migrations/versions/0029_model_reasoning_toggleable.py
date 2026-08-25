"""model: флаг возможности переключения режима рассуждения (каркас Т-445).

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-25

Ручной флаг ``model.reasoning_toggleable`` по паттерну ``supports_reasoning``
(Т-113): админ явно отмечает модели, у которых провайдер умеет включать/
выключать режим рассуждения. Без флага политика ``policy.reasoning``
(off/optional/on) не знает, для какой модели пробовать слать параметр
переключения (вариант Б1, каркас Т-445).

Конкретный параметр запроса в каркасе НЕ отправляется — добавится точечно,
когда появится живой провайдер с наблюдаемым эффектом переключения
(результат зондов 2026-08-25: ни один живой провайдер эффект не показал).

Колонка NOT NULL с server_default false — существующие строки получают
значение по умолчанию.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model",
        sa.Column(
            "reasoning_toggleable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("model", "reasoning_toggleable")
