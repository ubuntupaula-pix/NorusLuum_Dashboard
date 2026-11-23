# -*- coding: utf-8 -*-
"""Límites centralizados de importación.

Vercel limita el cuerpo de cada petición a ~4.5 MB, así que los archivos
grandes se suben en fragmentos y se ensamblan en la base de datos. Todos los
límites son configurables por variable de entorno y se comparten entre la API,
el ETL y el front-end (que expone los mismos valores en /api/importar/config).
"""

from __future__ import annotations

import math
import os


def _entero(nombre: str, por_defecto: int, minimo: int = 1) -> int:
    try:
        valor = int(os.environ.get(nombre, str(por_defecto)))
    except (TypeError, ValueError):
        valor = por_defecto
    return max(minimo, valor)


# Un millón de filas y 1 GB por archivo: muy por encima de las 100.000 filas
# pedidas. El límite real de cada petición se resuelve con fragmentos.
IMPORT_MAX_ROWS = _entero("NORUS_IMPORT_MAX_ROWS", 1_000_000)
IMPORT_MAX_COLUMNS = _entero("NORUS_IMPORT_MAX_COLUMNS", 200)
UPLOAD_MAX_BYTES = _entero("NORUS_UPLOAD_MAX_BYTES", 1024 * 1024 * 1024)

# Fragmentos de 4 MB caben holgadamente en el límite de Vercel.
UPLOAD_CHUNK_BYTES = _entero("NORUS_UPLOAD_CHUNK_BYTES", 4 * 1024 * 1024)
UPLOAD_MAX_CHUNKS = _entero(
    "NORUS_UPLOAD_MAX_CHUNKS", math.ceil(UPLOAD_MAX_BYTES / UPLOAD_CHUNK_BYTES)
)
UPLOAD_SESSION_TTL_HOURS = _entero("NORUS_UPLOAD_SESSION_TTL_HOURS", 24)

# Tamaño del lote para consultas de existentes y flushes.
ETL_BATCH_SIZE = _entero("NORUS_ETL_BATCH_SIZE", 5_000)

EXTENSIONES_PERMITIDAS = {".csv", ".tsv", ".txt", ".xlsx", ".json"}


def limites_publicos() -> dict:
    """Valores que el front-end necesita para dimensionar la subida."""
    return {
        "max_bytes": UPLOAD_MAX_BYTES,
        "max_rows": IMPORT_MAX_ROWS,
        "max_columns": IMPORT_MAX_COLUMNS,
        "chunk_bytes": UPLOAD_CHUNK_BYTES,
        "max_chunks": UPLOAD_MAX_CHUNKS,
        "extensiones": sorted(EXTENSIONES_PERMITIDAS),
    }
