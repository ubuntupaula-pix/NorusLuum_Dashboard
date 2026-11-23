# -*- coding: utf-8 -*-
"""============================================================
    NORUS & LUUM · Datos de demostración (API)
    ============================================================
El banner de la interfaz consulta ``/api/demo/estado`` para saber si hay
datos de muestra activos. Solo un administrador puede cargarlos o borrarlos.
El borrado es selectivo: elimina únicamente las filas generadas por
``scripts.seed_demo`` y respeta cualquier importación real posterior.
============================================================"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import get_current_user, require_admin
from .database import get_db
from .models import CargaArchivo, EventoAuditoria, HechoReserva, Usuario
from scripts.seed_demo import demo_carga_ids, limpiar_demo
from scripts.seed_demo_real import generar_real

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["demo"])


class DemoCargarRequest(BaseModel):
    # La demo usa el dataset hotelero real de Antonio et al. (2019), CC BY 4.0.
    filas: int = Field(default=25000, ge=100, le=120000)


def _estado(db: Session) -> dict:
    ids = demo_carga_ids(db)
    if not ids:
        return {"activo": False, "cargas": [], "reservas": 0, "generado_el": None}

    reservas = db.scalar(
        select(func.count(HechoReserva.id)).where(HechoReserva.carga_id.in_(ids))
    ) or 0
    cargas = list(db.scalars(
        select(CargaArchivo).where(CargaArchivo.id.in_(ids)).order_by(CargaArchivo.creado_en.desc())
    ))
    return {
        "activo": reservas > 0,
        "cargas": [
            {
                "id": carga.id,
                "fecha": carga.creado_en.isoformat() if carga.creado_en else None,
                "filas": carga.filas_procesadas,
            }
            for carga in cargas
        ],
        "reservas": reservas,
        "generado_el": cargas[0].creado_en.isoformat() if cargas and cargas[0].creado_en else None,
    }


def _audit(db: Session, usuario: Usuario, accion: str, detalle: str) -> None:
    db.add(EventoAuditoria(accion=accion, usuario=usuario.username, detalle=detalle))
    db.commit()


@router.get("/estado")
def estado_demo(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> dict:
    """Estado de los datos de muestra. Disponible para cualquier usuario autenticado."""
    return _estado(db)


@router.post("/cargar")
def cargar_demo(
    payload: DemoCargarRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_admin),
) -> dict:
    """Carga la demo con datos reales verificables, reemplazando la anterior.

    Las reservas provienen del dataset hotelero de Antonio et al. (2019),
    CC BY 4.0; las referencias de mercado vienen de INE-EMAT (Chile).
    ``generar_real`` ya limpia la demo previa de forma selectiva.
    """
    resumen = generar_real(payload.filas, db=db)
    _audit(db, current_user, "demo_cargar", (
        f"{resumen['exitosas']} reservas reales (CC BY 4.0) · "
        f"{resumen['referencias_mercado']} referencias INE-EMAT"
    ))
    log.info("Demo real cargada por %s (%s filas)", current_user.username, resumen["filas"])
    resultado = _estado(db)
    resultado.update(resumen)
    return resultado


@router.delete("")
def borrar_demo(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_admin),
) -> dict:
    """Borra solo los datos demo y devuelve el detalle por tabla."""
    borrados = limpiar_demo(db)
    _audit(db, current_user, "demo_borrar", "Datos de demostración eliminados")
    log.info("Datos demo borrados por %s", current_user.username)
    return {"ok": True, "borrados": borrados, **_estado(db)}
