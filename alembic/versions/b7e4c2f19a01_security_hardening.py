"""security hardening: mfa, sso, property scope and daily metrics

Revision ID: b7e4c2f19a01
Revises: aad351811f0c
Create Date: 2026-11-14 00:00:00.000000

Adds:
  * usuario: MFA, SSO, token version, email and global-scope flag.
  * usuario_propiedad: explicit per-property permissions.
  * hecho_metrica_diaria: day/week granularity metrics.
  * hecho_incidencia.marca_id: property scope for incidents.
  * Wider text columns for encrypted PII.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e4c2f19a01"
down_revision: Union[str, None] = "aad351811f0c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    with op.batch_alter_table("usuario") as batch:
        batch.add_column(sa.Column("email", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")))
        batch.add_column(sa.Column("acceso_todas_propiedades", sa.Integer(), nullable=False, server_default=sa.text("0")))
        batch.add_column(sa.Column("mfa_habilitado", sa.Integer(), nullable=False, server_default=sa.text("0")))
        batch.add_column(sa.Column("mfa_secret", sa.Text(), nullable=True))
        batch.add_column(sa.Column("mfa_pending_secret", sa.Text(), nullable=True))
        batch.add_column(sa.Column("mfa_last_counter", sa.BigInteger(), nullable=False, server_default=sa.text("0")))
        batch.add_column(sa.Column("backup_codes", sa.Text(), nullable=True))
        batch.add_column(sa.Column("sso_subject", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("sso_provider", sa.String(length=120), nullable=True))

    op.create_index("ix_usuario_email", "usuario", ["email"], unique=True)
    op.create_index("ix_usuario_sso_subject", "usuario", ["sso_subject"], unique=True)

    op.create_table(
        "usuario_propiedad",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("marca_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuario.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["marca_id"], ["dim_marca.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("usuario_id", "marca_id", name="uq_usuario_propiedad"),
    )
    op.create_index("ix_usuario_propiedad_usuario_id", "usuario_propiedad", ["usuario_id"])
    op.create_index("ix_usuario_propiedad_marca_id", "usuario_propiedad", ["marca_id"])

    op.create_table(
        "hecho_metrica_diaria",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fecha", sa.String(length=10), nullable=False),
        sa.Column("anio", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("marca_id", sa.Integer(), nullable=True),
        sa.Column("moneda", sa.String(length=3), nullable=False),
        sa.Column("ocupacion", sa.Float(), nullable=True),
        sa.Column("adr", sa.Float(), nullable=True),
        sa.Column("revpar", sa.Float(), nullable=True),
        sa.Column("ingresos", sa.Float(), nullable=True),
        sa.Column("noches_vendidas", sa.Float(), nullable=True),
        sa.Column("habitaciones_disponibles", sa.Float(), nullable=True),
        sa.Column("carga_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["marca_id"], ["dim_marca.id"]),
        sa.ForeignKeyConstraint(["carga_id"], ["carga_archivo.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fecha", "marca_id", "moneda", name="uq_metrica_dia_propiedad_moneda"),
    )
    op.create_index("ix_hecho_metrica_diaria_fecha", "hecho_metrica_diaria", ["fecha"])
    op.create_index("idx_metrica_dia_marca", "hecho_metrica_diaria", ["fecha", "marca_id"])

    with op.batch_alter_table("hecho_incidencia") as batch:
        batch.add_column(sa.Column("marca_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_hecho_incidencia_marca", "dim_marca", ["marca_id"], ["id"])
    op.create_index("idx_incidencia_marca", "hecho_incidencia", ["marca_id"])

    with op.batch_alter_table("hecho_reserva") as batch:
        batch.alter_column("huesped", existing_type=sa.String(length=200), type_=sa.Text(), existing_nullable=True)

    with op.batch_alter_table("ops_objeto_perdido") as batch:
        batch.alter_column("descripcion", existing_type=sa.String(length=240), type_=sa.Text(), existing_nullable=False)

    # Backwards compatibility: existing users keep full visibility until an
    # administrator assigns explicit properties.
    if dialect != "sqlite":
        op.execute("UPDATE usuario SET acceso_todas_propiedades = 1 WHERE rol <> 'admin'")


def downgrade() -> None:
    with op.batch_alter_table("ops_objeto_perdido") as batch:
        batch.alter_column("descripcion", existing_type=sa.Text(), type_=sa.String(length=240), existing_nullable=False)

    with op.batch_alter_table("hecho_reserva") as batch:
        batch.alter_column("huesped", existing_type=sa.Text(), type_=sa.String(length=200), existing_nullable=True)

    op.drop_index("idx_incidencia_marca", table_name="hecho_incidencia")
    with op.batch_alter_table("hecho_incidencia") as batch:
        batch.drop_constraint("fk_hecho_incidencia_marca", type_="foreignkey")
        batch.drop_column("marca_id")

    op.drop_index("idx_metrica_dia_marca", table_name="hecho_metrica_diaria")
    op.drop_index("ix_hecho_metrica_diaria_fecha", table_name="hecho_metrica_diaria")
    op.drop_table("hecho_metrica_diaria")

    op.drop_index("ix_usuario_propiedad_marca_id", table_name="usuario_propiedad")
    op.drop_index("ix_usuario_propiedad_usuario_id", table_name="usuario_propiedad")
    op.drop_table("usuario_propiedad")

    op.drop_index("ix_usuario_sso_subject", table_name="usuario")
    op.drop_index("ix_usuario_email", table_name="usuario")
    with op.batch_alter_table("usuario") as batch:
        batch.drop_column("sso_provider")
        batch.drop_column("sso_subject")
        batch.drop_column("backup_codes")
        batch.drop_column("mfa_last_counter")
        batch.drop_column("mfa_pending_secret")
        batch.drop_column("mfa_secret")
        batch.drop_column("mfa_habilitado")
        batch.drop_column("acceso_todas_propiedades")
        batch.drop_column("token_version")
        batch.drop_column("email")
