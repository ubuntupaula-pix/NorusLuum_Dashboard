# -*- coding: utf-8 -*-
"""Alta idempotente de los usuarios de demostración ``editor`` y ``lector``.

Las contraseñas NO se versionan: se leen de ``NORUS_EDITOR_PASS`` y
``NORUS_LECTOR_PASS``. El script respeta la política de complejidad de
:mod:`app.auth` y asigna explícitamente las propiedades Norus Luum (1–8), sin
acceso global. No modifica reservas, incidencias ni ningún otro dato de negocio.

Uso::

    $env:NORUS_EDITOR_PASS = "..."
    $env:NORUS_LECTOR_PASS = "..."
    python -m scripts.seed_roles
"""

from __future__ import annotations

import os
import sys

from app.auth import hash_password, validate_password_strength
from app.database import SessionLocal
from app.models import DimMarca, Usuario, UsuarioPropiedad


def _crear_o_omitir(db, username: str, password: str, rol: str, propiedades: list[int]) -> None:
    if db.query(Usuario).filter(Usuario.username == username).first():
        print(f"  · {username}: ya existe, sin cambios.")
        return
    validate_password_strength(password)
    user = Usuario(
        username=username,
        password_hash=hash_password(password),
        email=None,
        rol=rol,
        activo=1,
        acceso_todas_propiedades=0,
    )
    db.add(user)
    db.flush()
    for propiedad_id in propiedades:
        db.add(UsuarioPropiedad(usuario_id=user.id, marca_id=propiedad_id))
    print(f"  · {username}: creado como {rol} con {len(propiedades)} propiedades.")


def main() -> int:
    editor_pass = os.environ.get("NORUS_EDITOR_PASS", "")
    lector_pass = os.environ.get("NORUS_LECTOR_PASS", "")
    if not editor_pass or not lector_pass:
        print("Defina NORUS_EDITOR_PASS y NORUS_LECTOR_PASS antes de ejecutar.", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        propiedades = [
            row[0]
            for row in db.query(DimMarca.id)
            .filter(DimMarca.nombre.like("Norus Luum%"))
            .order_by(DimMarca.id)
            .all()
        ]
        if not propiedades:
            print("No hay propiedades Norus Luum en la base.", file=sys.stderr)
            return 1
        print(f"Propiedades asignadas: {propiedades}")
        try:
            _crear_o_omitir(db, "editor", editor_pass, "editor", propiedades)
            _crear_o_omitir(db, "lector", lector_pass, "lector", propiedades)
        except Exception as exc:
            db.rollback()
            detail = getattr(exc, "detail", str(exc))
            print(f"Error al crear usuarios: {detail}", file=sys.stderr)
            return 1
        db.commit()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
