# -*- coding: utf-8 -*-
"""Copia la base local (SQLite o PostgreSQL) a un PostgreSQL en la nube.

Uso recomendado (PowerShell, desde la raíz del proyecto):

    $env:DATABASE_URL = 'postgresql+psycopg://usuario:clave@host:5432/norus?sslmode=require'
    $env:NORUS_ENCRYPTION_KEY = '<la misma clave usada en local>'
    python -m scripts.migrate_to_cloud --yes

El destino debe ser PostgreSQL. El script:
  1. Aplica las migraciones Alembic en el destino.
  2. Comprueba que el destino esté vacío (o exige --replace / --append).
  3. Copia todas las tablas en orden de dependencias.
  4. Reajusta las secuencias SERIAL de PostgreSQL.
  5. Verifica que el número de filas coincide.

Si la base local contiene datos personales cifrados, usa exactamente la misma
``NORUS_ENCRYPTION_KEY`` para poder descifrarlos y volver a cifrarlos en la nube.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.database import Base  # noqa: E402


def _url(value: str):
    try:
        return make_url(value)
    except Exception as exc:  # pragma: no cover - depende de la entrada
        raise SystemExit(f"URL de base de datos inválida: {exc}") from exc


def _engine(url: str):
    options = {"pool_pre_ping": True}
    parsed = _url(url)
    if parsed.get_backend_name().startswith("postgresql"):
        options["connect_args"] = {
            "connect_timeout": int(os.environ.get("NORUS_DB_CONNECT_TIMEOUT", "10")),
        }
        if "sslmode" not in url:
            options["connect_args"]["sslmode"] = os.environ.get("NORUS_DB_SSLMODE", "require")
    return create_engine(url, **options)


def _run_migrations(target_url: str) -> None:
    os.environ["DATABASE_URL"] = target_url
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(RAIZ / "alembic.ini")), "head")


def _table_counts(connection, tables) -> dict[str, int]:
    return {
        table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
        for table in tables
    }


def _reset_sequences(connection, tables) -> None:
    for table in tables:
        if "id" not in table.c:
            continue
        connection.execute(text(
            f'SELECT setval(pg_get_serial_sequence(\'{table.name}\', \'id\'), '
            f'COALESCE((SELECT MAX(id) FROM "{table.name}"), 1), '
            f'(SELECT MAX(id) FROM "{table.name}") IS NOT NULL)'
        ))


def main() -> int:
    parser = argparse.ArgumentParser(description="Migra los datos locales a PostgreSQL en la nube.")
    parser.add_argument(
        "--source",
        default=os.environ.get("NORUS_SOURCE_DATABASE_URL", ""),
        help="URL de origen (por defecto SQLite local data/norusluum.db).",
    )
    parser.add_argument(
        "--target",
        default=os.environ.get("DATABASE_URL", ""),
        help="URL de PostgreSQL destino (por defecto DATABASE_URL).",
    )
    parser.add_argument("--yes", action="store_true", help="Confirma la escritura en el destino.")
    parser.add_argument("--replace", action="store_true", help="Borra las tablas del destino antes de copiar.")
    parser.add_argument("--append", action="store_true", help="Añade filas aunque el destino no esté vacío.")
    parser.add_argument("--no-migrate", action="store_true", help="No ejecuta Alembic en el destino.")
    args = parser.parse_args()

    source_url = args.source.strip() or f"sqlite:///{(RAIZ / 'data' / 'norusluum.db').as_posix()}"
    target_url = args.target.strip()

    def _with_driver(value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    source_url = _with_driver(source_url)
    target_url = _with_driver(target_url)
    if not target_url:
        raise SystemExit("Define DATABASE_URL o usa --target con la cadena de PostgreSQL en la nube.")
    if _url(target_url).get_backend_name() != "postgresql":
        raise SystemExit("El destino debe ser PostgreSQL (postgresql+psycopg://...).")
    if _url(source_url).render_as_string(hide_password=False) == _url(target_url).render_as_string(hide_password=False):
        raise SystemExit("El origen y el destino no pueden ser la misma base de datos.")
    if not args.yes:
        raise SystemExit("Ejecuta de nuevo con --yes para confirmar la copia al destino.")

    print("Origen :", _url(source_url).render_as_string(hide_password=True))
    print("Destino:", _url(target_url).render_as_string(hide_password=True))

    if not args.no_migrate:
        print("Aplicando migraciones Alembic en el destino…")
        _run_migrations(target_url)

    source_engine = _engine(source_url)
    target_engine = _engine(target_url)
    tables = list(Base.metadata.sorted_tables)

    with source_engine.connect() as source, target_engine.begin() as target:
        existing = set(inspect(target).get_table_names())
        missing = [table.name for table in tables if table.name not in existing]
        if missing:
            raise SystemExit(f"Faltan tablas en el destino: {', '.join(missing)}. Ejecuta alembic upgrade head.")

        current = _table_counts(target, tables)
        occupied = {name: count for name, count in current.items() if count}
        if occupied and not (args.replace or args.append):
            detail = ", ".join(f"{name}={count}" for name, count in sorted(occupied.items()))
            raise SystemExit(
                "El destino ya contiene datos (" + detail + "). "
                "Usa --replace para reemplazarlos o --append para añadir."
            )
        if args.replace and args.append:
            raise SystemExit("Usa solo una de las opciones --replace o --append.")
        if args.replace:
            print("Vaciando tablas del destino…")
            names = ", ".join(f'"{table.name}"' for table in reversed(tables))
            target.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))

        print("Copiando datos…")
        for table in tables:
            rows = [dict(row._mapping) for row in source.execute(select(table))]
            if rows:
                target.execute(table.insert(), rows)
            print(f"  {table.name}: {len(rows)} filas")

        _reset_sequences(target, tables)

        final = _table_counts(target, tables)
        source_counts = _table_counts(source, tables)
        mismatches = {
            name: (source_counts[name], final[name])
            for name in source_counts
            if final[name] != source_counts[name]
        }
        if mismatches:
            raise SystemExit(f"Recuento distinto tras la copia: {mismatches}")

    print("\nMigración completada y verificada. Filas copiadas:")
    for name, count in final.items():
        print(f"  {name}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
