# -*- coding: utf-8 -*-
"""Cifra los valores PII que aún estén en texto plano en la base actual.

Las columnas marcadas con ``EncryptedText`` (huésped de reservas y secretos
MFA) cifran al escribir, pero las filas creadas antes del endurecimiento
pueden seguir en claro. Este script las reescribe cifradas con la clave
``NORUS_ENCRYPTION_KEY`` actual.

    python -m scripts.encrypt_existing --dry-run
    python -m scripts.encrypt_existing
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.crypto import PREFIX, EncryptedText, encryption_active  # noqa: E402
from app.database import Base, engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Cifra PII existente en texto plano.")
    parser.add_argument("--dry-run", action="store_true", help="Solo cuenta, no modifica.")
    args = parser.parse_args()

    if not encryption_active():
        raise SystemExit("Define NORUS_ENCRYPTION_KEY antes de cifrar los datos existentes.")

    total = 0
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            columns = [column for column in table.columns if isinstance(column.type, EncryptedText)]
            if not columns:
                continue
            names = ", ".join(f'"{column.name}"' for column in columns)
            rows = connection.execute(text(f'SELECT id, {names} FROM "{table.name}"')).mappings().all()
            pending = []
            for row in rows:
                changes = {
                    column.name: row[column.name]
                    for column in columns
                    if row[column.name] is not None and not str(row[column.name]).startswith(PREFIX)
                }
                if changes:
                    pending.append((row["id"], changes))
            if not pending:
                continue
            print(f"{table.name}: {len(pending)} filas con texto plano")
            total += len(pending)
            if args.dry_run:
                continue
            for row_id, changes in pending:
                connection.execute(
                    table.update().where(table.c.id == row_id).values(**changes)
                )

        if not args.dry_run and total:
            # Verificación: ninguna columna cifrada debe quedar sin el prefijo.
            for table in Base.metadata.sorted_tables:
                columns = [c for c in table.columns if isinstance(c.type, EncryptedText)]
                for column in columns:
                    count = connection.execute(text(
                        f'SELECT count(*) FROM "{table.name}" '
                        f'WHERE "{column.name}" IS NOT NULL '
                        f'AND "{column.name}" NOT LIKE :prefix'
                    ), {"prefix": PREFIX + "%"}).scalar_one()
                    if count:
                        raise SystemExit(f"Quedaron {count} valores sin cifrar en {table.name}.{column.name}")

    if total == 0:
        print("No hay valores en texto plano: todo está cifrado.")
    elif args.dry_run:
        print(f"\n{total} filas se cifrarían con --dry-run. Ejecuta sin --dry-run para aplicarlo.")
    else:
        print(f"\n{total} filas cifradas y verificadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
