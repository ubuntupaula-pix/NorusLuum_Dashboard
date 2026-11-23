# -*- coding: utf-8 -*-
"""============================================================
    NORUS & LUUM · Refresco diario programado
    ============================================================
Vercel Cron llama una vez al día a ``GET /api/cron/mercado-diario`` con
``Authorization: Bearer $CRON_SECRET`` (también se admite POST para pruebas
manuales). El trabajo:

  1. descarga las fuentes públicas reales (INE-EMAT por defecto) con
     ``force=True`` y guarda las referencias con fuente/URL/período/moneda;
  2. recalcula y persiste la instantánea de comparación de mercado para el
     ámbito global y para cada propiedad, en las monedas configuradas.

No se inventa ningún dato: si una fuente falla se conserva el último snapshot
válido y el estado se reporta como ``historico`` en la respuesta pública.
"""
from __future__ import annotations

import hmac
import logging
import os
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import mercado_publico
from .database import get_db
from .mercado import _store_reference
from .mercado_tiempo_real import PUBLIC_CACHE_SECONDS, build_snapshot
from .models import DimMarca, EventoAuditoria, Usuario

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cron", tags=["Cron"])


def _secret() -> str:
    return (os.environ.get("CRON_SECRET") or os.environ.get("NORUS_CRON_SECRET") or "").strip()


def _autorizado(request: Request) -> bool:
    secreto = _secret()
    if not secreto:
        return False
    cabecera = request.headers.get("authorization", "")
    return hmac.compare_digest(cabecera, "Bearer " + secreto)


@router.api_route("/mercado-diario", methods=["GET", "POST"])
def mercado_diario(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Sincroniza referencias reales y calienta las comparativas del día."""
    if not _autorizado(request):
        raise HTTPException(status_code=401, detail="Cron no autorizado.")

    monedas = [
        m.strip().upper()
        for m in os.environ.get("NORUS_CRON_MONEDAS", "CLP,USD").split(",")
        if m.strip().isalpha() and len(m.strip()) == 3
    ] or ["CLP"]
    admin = db.scalar(select(Usuario).where(Usuario.rol == "admin").order_by(Usuario.id))
    principal: Any = admin or SimpleNamespace(
        rol="admin", acceso_todas_propiedades=1, username="scheduler"
    )
    usuario_auditoria = getattr(principal, "username", "scheduler") or "scheduler"

    # Descartar la caché en memoria de la instancia: el cron debe leer fuentes.
    mercado_publico.clear_cache()
    resumen: dict[str, Any] = {
        "monedas": {},
        "instantaneas": 0,
        "fuentes": [],
        "errores": [],
    }

    for moneda in monedas:
        try:
            referencias, estados = mercado_publico.fetch_public_references(
                moneda, None, cache_ttl=PUBLIC_CACHE_SECONDS, force=True
            )
        except Exception as exc:  # noqa: BLE001 - el cron debe reportar y seguir
            resumen["errores"].append(f"{moneda}: {exc}"[:300])
            continue
        creadas = actualizadas = 0
        for dato in referencias:
            _, creada = _store_reference(db, dato, None, principal)
            if creada:
                creadas += 1
            else:
                actualizadas += 1
        db.add(EventoAuditoria(
            accion="mercado_cron_diario",
            usuario=usuario_auditoria,
            detalle=f"{moneda} · {len(referencias)} referencias · {creadas} nuevas · {actualizadas} actualizadas",
            ip="cron",
        ))
        db.commit()
        resumen["monedas"][moneda] = {
            "referencias": len(referencias),
            "creadas": creadas,
            "actualizadas": actualizadas,
        }
        resumen["fuentes"].extend(estados)

    # Calentar la instantánea global por moneda es suficiente: las vistas por
    # propiedad se construyen bajo demanda con las referencias ya actualizadas.
    # Calentar todas las propiedades en la misma invocación puede agotar el
    # límite de tiempo de la función serverless; se habilita explícitamente.
    calentar_propiedades = os.environ.get(
        "NORUS_CRON_WARM_PROPERTIES", "0"
    ).strip().lower() in {"1", "true", "yes", "si"}
    propiedades = list(db.scalars(select(DimMarca.id).order_by(DimMarca.id))) if calentar_propiedades else []
    for indice, moneda in enumerate(monedas):
        # La primera llamada por moneda fuerza lectura de fuentes; el resto
        # reutiliza la caché pública de 60 s recién cargada.
        objetivos: list[tuple[int | None, str]] = [(None, moneda)]
        objetivos.extend((pid, moneda) for pid in propiedades)
        for propiedad_id, moneda_objetivo in objetivos:
            try:
                build_snapshot(
                    db, principal, propiedad_id, moneda_objetivo, "24h", indice == 0
                )
                resumen["instantaneas"] += 1
            except Exception as exc:  # noqa: BLE001 - una instantánea no bloquea al resto
                resumen["errores"].append(
                    f"{moneda_objetivo}/propiedad {propiedad_id or 'global'}: {exc}"[:300]
                )
                db.rollback()

    fallos = [f for f in resumen["fuentes"] if f.get("estado") == "error"]
    resumen["estado"] = "fallida" if fallos and not resumen["monedas"] else (
        "parcial" if fallos or resumen["errores"] else "ok"
    )
    log.info("Cron mercado diario: %s", resumen["estado"])
    return resumen
