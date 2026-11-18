# -*- coding: utf-8 -*-
"""============================================================
   NORUS & LUUM · Capa de base de datos (SQLAlchemy)
   Modelo dimensional: tablas de hechos y dimensiones.
   Soporta SQLite (desarrollo) y PostgreSQL (producción/nube).
   ============================================================"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

try:  # Local .env support; Docker Compose injects variables directly.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass

log = logging.getLogger(__name__)

# ============================================================
# CONFIGURACIÓN DE CONEXIÓN
# ============================================================

# Prioridad: variable de entorno > SQLite local
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if not DATABASE_URL:
    # Desarrollo: SQLite local
    DB_PATH = os.environ.get(
        "NORUS_DB_PATH",
        str(Path(__file__).resolve().parent.parent / "data" / "norusluum.db"),
    )
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{DB_PATH}"

# Algunos proveedores cloud entregan "postgres://"; SQLAlchemy exige el driver.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Parámetros de conexión del proveedor cloud (SSL obligatorio por defecto).
_parsed = urlparse(DATABASE_URL)
IS_POSTGRESQL = _parsed.scheme in (
    "postgresql", "postgresql+psycopg", "postgresql+psycopg2", "postgresql+asyncpg"
)
IS_SQLITE = _parsed.scheme.startswith("sqlite")

if IS_POSTGRESQL:
    _sslmode = os.environ.get("NORUS_DB_SSLMODE", "require").strip() or "require"
    # No sobreescribir un sslmode explícito en la URL.
    _connect_args = {}
    if "sslmode=" not in DATABASE_URL:
        _connect_args["sslmode"] = _sslmode
    _connect_args["connect_timeout"] = int(os.environ.get("NORUS_DB_CONNECT_TIMEOUT", "10"))
    engine = create_engine(
        DATABASE_URL,
        pool_size=int(os.environ.get("NORUS_DB_POOL_SIZE", "10")),
        max_overflow=int(os.environ.get("NORUS_DB_MAX_OVERFLOW", "20")),
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=30,
        connect_args=_connect_args,
        echo=False,
    )
elif IS_SQLITE:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
else:
    engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependencia de FastAPI: sesión de BD por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def database_kind() -> str:
    return "postgresql" if IS_POSTGRESQL else ("sqlite" if IS_SQLITE else _parsed.scheme)
