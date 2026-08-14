"""User refresh_token_enc for OIDC sync (T-405).

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-14

T-405: Периодическая синхронизация групп. refresh_token хранится
зашифрованным (AES-GCM, app/crypto/service.py). Nullable — только
OIDC-пользователи при oidc_sync_enabled.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("refresh_token_enc", sa.String(512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("refresh_token_enc")
