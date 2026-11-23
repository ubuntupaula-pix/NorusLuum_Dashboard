"""chunked import sessions for large files

Revision ID: f4a7c9e1b253
Revises: d9f3a1c7b204
Create Date: 2026-11-19 00:00:00.000000

Vercel limits each request body to ~4.5 MB. Large CSV/TSV/XLSX/JSON files are
uploaded in 4 MB chunks, staged in the database and assembled when the client
calls /finalizar. This migration adds the two small tables used by that flow.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a7c9e1b253"
down_revision: Union[str, None] = "d9f3a1c7b204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "importacion_sesion",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("upload_id", sa.String(length=64), nullable=False),
        sa.Column("nombre_archivo", sa.String(length=255), nullable=False),
        sa.Column("formato", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("mapeo", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("usuario", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("tamano_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="abierta"),
        sa.Column("carga_id", sa.Integer(), nullable=True),
        sa.Column("mensaje", sa.Text(), nullable=False, server_default=""),
        sa.Column("creado_en", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("actualizado_en", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("upload_id", name="uq_import_sesion_upload_id"),
    )
    op.create_index("ix_importacion_sesion_upload_id", "importacion_sesion", ["upload_id"], unique=True)

    op.create_table(
        "importacion_chunk",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("upload_id", sa.String(length=64), nullable=False),
        sa.Column("indice", sa.Integer(), nullable=False),
        sa.Column("contenido", sa.LargeBinary(), nullable=False),
        sa.Column("creado_en", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("upload_id", "indice", name="uq_import_chunk_upload_indice"),
    )
    op.create_index("ix_importacion_chunk_upload_id", "importacion_chunk", ["upload_id"])


def downgrade() -> None:
    op.drop_index("ix_importacion_chunk_upload_id", table_name="importacion_chunk")
    op.drop_table("importacion_chunk")
    op.drop_index("ix_importacion_sesion_upload_id", table_name="importacion_sesion")
    op.drop_table("importacion_sesion")
