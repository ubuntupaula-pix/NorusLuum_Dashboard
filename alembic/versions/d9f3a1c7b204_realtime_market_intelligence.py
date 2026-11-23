"""real-time market intelligence snapshot cache

Revision ID: d9f3a1c7b204
Revises: c8f5d3a20b12
Create Date: 2026-11-18 00:00:00.000000

Adds a small cache table so the real-time benchmark survives serverless cold
starts and can degrade gracefully when an upstream market API is unavailable.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9f3a1c7b204"
down_revision: Union[str, None] = "c8f5d3a20b12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mercado_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("scope_key", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("origen", sa.String(length=40), nullable=False, server_default="tiempo_real"),
        sa.Column("creado_en", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("actualizado_en", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scope_key", name="uq_mercado_snapshot_scope"),
    )
    op.create_index("ix_mercado_snapshot_scope", "mercado_snapshot", ["scope_key"], unique=True)
    op.create_index("ix_mercado_snapshot_actualizado", "mercado_snapshot", ["actualizado_en"])


def downgrade() -> None:
    op.drop_index("ix_mercado_snapshot_actualizado", table_name="mercado_snapshot")
    op.drop_index("ix_mercado_snapshot_scope", table_name="mercado_snapshot")
    op.drop_table("mercado_snapshot")
