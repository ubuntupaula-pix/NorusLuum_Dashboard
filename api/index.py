# ============================================================
#  NORUS & LUUM · Adaptador serverless para Vercel
# ============================================================
"""Punto de entrada que Vercel usa para construir la función.

Vercel monta el proyecto en un sistema de archivos de solo lectura salvo
``/tmp``. Este módulo:

  1. Añade la raíz del proyecto al ``sys.path`` para poder importar ``app``.
  2. Si no hay ``DATABASE_URL`` (PostgreSQL), copia la base SQLite empaquetada
     a ``/tmp`` —la única ruta escribible— y apunta ``NORUS_DB_PATH`` ahí.
  3. Expone la instancia ASGI ``app`` que Vercel detecta y sirve.

La app FastAPI ya incluye la ruta *catch-all* que sirve el frontend estático,
por lo que todas las peticiones (API + HTML/CSS/JS) pasan por esta función.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Base de datos -------------------------------------------------------
# Con PostgreSQL gestionado (Neon, Supabase, RDS…) basta con DATABASE_URL.
# Sin ella usamos SQLite: el archivo se empaqueta en data/ y se copia a /tmp
# porque el resto del sistema de archivos es de solo lectura.
if not os.environ.get("DATABASE_URL", "").strip():
    destino = Path(os.environ.get("NORUS_DB_PATH", "/tmp/norusluum.db"))
    origen = ROOT / "data" / "norusluum.db"
    if not destino.exists() and origen.is_file():
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, destino)
        except OSError:  # pragma: no cover - solo en entornos restringidos
            pass
    os.environ["NORUS_DB_PATH"] = str(destino)

from app.main import app  # noqa: E402  (debe importarse tras fijar la BD)

__all__ = ["app"]
