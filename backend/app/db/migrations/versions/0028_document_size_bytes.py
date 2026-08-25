"""document: размер файла в байтах (закрытие «0 B» в списке документов).

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-25

Список документов показывал плейсхолдер «0 B» для каждого файла: размер не
хранился. ``LocalBlobStore.put`` уже считает размер при записи
(``BlobRef.size``) — значение сохраняется в ``document.size_bytes``.

Колонка nullable: у ранее загруженных документов размер неизвестен
(задним числом не восстановить без перечитывания всех blob).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document", sa.Column("size_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("document", "size_bytes")
