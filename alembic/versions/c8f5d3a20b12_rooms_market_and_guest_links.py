"""room analytics, market references and guest-linked incidents

Revision ID: c8f5d3a20b12
Revises: b7e4c2f19a01
Create Date: 2026-11-14 00:00:00.000000

Adds:
  * hecho_reserva.habitacion: room number for room-level analytics and
    guest-linked incident context.
  * ops_incidencia.reserva_id: explicit link to the guest stay.
  * ops_capacidad_habitacion: declared room capacity per property and room
    type; required before occupancy by room type can be calculated.
  * mercado_referencia: public market references with source, period and
    currency for long-term price comparison.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8f5d3a20b12"
down_revision: Union[str, None] = "b7e4c2f19a01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("hecho_reserva") as batch:
        batch.add_column(
            sa.Column("habitacion", sa.String(length=30), nullable=False, server_default="")
        )
        batch.create_index("idx_reserva_habitacion", ["habitacion"])

    with op.batch_alter_table("ops_incidencia") as batch:
        batch.add_column(sa.Column("reserva_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_ops_incidencia_reserva",
            "hecho_reserva",
            ["reserva_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_ops_incidente_reserva", ["reserva_id"])

    op.create_table(
        "ops_capacidad_habitacion",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("marca_id", sa.Integer(), nullable=False),
        sa.Column("tipo_habitacion_id", sa.Integer(), nullable=False),
        sa.Column("habitaciones", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actualizado_por", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("actualizado_en", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["marca_id"], ["dim_marca.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tipo_habitacion_id"], ["dim_tipo_habitacion.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("marca_id", "tipo_habitacion_id", name="uq_capacidad_marca_tipo"),
    )
    op.create_index(
        "ix_capacidad_marca_tipo", "ops_capacidad_habitacion", ["marca_id", "tipo_habitacion_id"]
    )

    op.create_table(
        "mercado_referencia",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("marca_id", sa.Integer(), nullable=True),
        sa.Column("fuente", sa.String(length=180), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("tipo", sa.String(length=40), nullable=False, server_default="mercado"),
        sa.Column("metrica", sa.String(length=20), nullable=False, server_default="adr"),
        sa.Column("periodo", sa.String(length=7), nullable=False),
        sa.Column("valor", sa.Float(), nullable=False, server_default="0"),
        sa.Column("moneda", sa.String(length=3), nullable=False, server_default="CLP"),
        sa.Column("tipo_habitacion", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("notas", sa.Text(), nullable=False, server_default=""),
        sa.Column("creado_por", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("creado_en", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("actualizado_en", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["marca_id"], ["dim_marca.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "marca_id",
            "fuente",
            "metrica",
            "periodo",
            "moneda",
            "tipo_habitacion",
            name="uq_mercado_referencia",
        ),
    )
    op.create_index("ix_mercado_metrica_periodo", "mercado_referencia", ["metrica", "periodo"])
    op.create_index("ix_mercado_marca_periodo", "mercado_referencia", ["marca_id", "periodo"])


def downgrade() -> None:
    op.drop_index("ix_mercado_marca_periodo", table_name="mercado_referencia")
    op.drop_index("ix_mercado_metrica_periodo", table_name="mercado_referencia")
    op.drop_table("mercado_referencia")

    op.drop_index("ix_capacidad_marca_tipo", table_name="ops_capacidad_habitacion")
    op.drop_table("ops_capacidad_habitacion")

    with op.batch_alter_table("ops_incidencia") as batch:
        batch.drop_index("ix_ops_incidente_reserva")
        batch.drop_constraint("fk_ops_incidencia_reserva", type_="foreignkey")
        batch.drop_column("reserva_id")

    with op.batch_alter_table("hecho_reserva") as batch:
        batch.drop_index("idx_reserva_habitacion")
        batch.drop_column("habitacion")
