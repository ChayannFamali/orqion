"""User auth_method, external_subject, external_issuer; password_hash nullable.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-14

T-404a: IdentityProvider interface. password_hash becomes nullable (OIDC-only
users have no password). New columns: auth_method (default "local"),
external_subject, external_issuer — for OIDC subject/issuer mapping.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(255),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column("auth_method", sa.String(20), nullable=False, server_default="local")
        )
        batch_op.add_column(sa.Column("external_subject", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("external_issuer", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("external_issuer")
        batch_op.drop_column("external_subject")
        batch_op.drop_column("auth_method")
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(255),
            nullable=False,
        )
