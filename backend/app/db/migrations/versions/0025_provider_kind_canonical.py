"""Канонизация provider.kind: 'lm' -> 'lmstudio' (T-437).

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-23

kind провайдера ограничен каноническим набором на уровне Pydantic-схем
(см. ProviderCreate): ollama, lmstudio, external. Историческое значение
'lm' (свободный ввод до валидации) нормализуется разовой миграцией данных,
не runtime-нормализацией и не алиас-словарём (прецедент BUG-011: несколько
допустимых форм одного значения без канонической ведут к расхождениям).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE provider SET kind = 'lmstudio' WHERE kind = 'lm'")


def downgrade() -> None:
    # Обратная нормализация приблизительная: затрагивает и провайдеров,
    # созданных как 'lmstudio' уже после upgrade.
    op.execute("UPDATE provider SET kind = 'lm' WHERE kind = 'lmstudio'")
