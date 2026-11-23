# -*- coding: utf-8 -*-
"""Integración segura del panel como sección de otra aplicación web.

El administrador emite un JWT de solo lectura para un origen padre concreto.
La aplicación padre lo envía al iframe mediante ``postMessage`` (nunca por URL)
y el widget lo usa como ``Authorization: Bearer`` contra la API de tiempo real.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth import (
    EMBED_TOKEN_EXPIRE_MINUTES,
    create_embed_token,
    embed_allowed_origins,
    require_admin,
)
from .database import get_db
from .models import DimMarca
from .schemas import EmbedTokenCreate, EmbedTokenResponse

router = APIRouter(prefix="/api/embed", tags=["Integración embebible"])


@router.get("/config")
def embed_config():
    """Configuración pública necesaria para montar el iframe."""
    return {
        "habilitado": bool(embed_allowed_origins()),
        "orígenes_permitidos": embed_allowed_origins(),
        "ttl_max_minutos": EMBED_TOKEN_EXPIRE_MINUTES,
        "mensaje": (
            "Emita /api/embed/token desde el backend de la aplicación padre y "
            "envíe el token al iframe con postMessage sobre el origen permitido."
        ),
    }


@router.post("/token", response_model=EmbedTokenResponse)
def create_token(
    body: EmbedTokenCreate,
    request: Request,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    """Emite un token efímero de solo lectura, limitado a propiedad y moneda."""
    permitidos = embed_allowed_origins()
    if not permitidos:
        raise HTTPException(
            status_code=503,
            detail="Configure NORUS_EMBED_ALLOWED_ORIGINS con el origen HTTPS de la aplicación padre.",
        )
    if body.parent_origin not in permitidos:
        raise HTTPException(status_code=403, detail="El origen padre no está autorizado.")

    propiedades = body.propiedades
    if propiedades is not None:
        propiedades = sorted({int(p) for p in propiedades})
        if not propiedades:
            raise HTTPException(status_code=422, detail="Indique al menos una propiedad o deje el campo vacío para todas.")
        encontradas = {row[0] for row in db.query(DimMarca.id).filter(DimMarca.id.in_(propiedades)).all()}
        if len(encontradas) != len(propiedades):
            raise HTTPException(status_code=404, detail="Alguna propiedad indicada no existe.")

    token, ttl = create_embed_token(
        propiedades=propiedades,
        moneda=body.moneda.upper(),
        parent_origin=body.parent_origin,
        minutos=body.minutos,
    )
    base = str(request.base_url).rstrip("/")
    url = base + "/embed.html"
    snippet = (
        "<iframe id=\"norus-panel\" src=\"" + url + "\" "
        "title=\"Inteligencia de mercado Norus\" style=\"width:100%;height:720px;border:0\"></iframe>\n"
        "<script>\n"
        "  const frame = document.getElementById('norus-panel');\n"
        "  const enviar = () => frame.contentWindow.postMessage({ tipo: 'norus:token', token: '<TOKEN>' }, '" + body.parent_origin + "');\n"
        "  frame.addEventListener('load', enviar);\n"
        "  window.addEventListener('message', (e) => {\n"
        "    if (e.origin !== '" + base + "') return;\n"
        "    if (e.data && e.data.tipo === 'norus:listo') enviar();\n"
        "  });\n"
        "</script>"
    )
    return EmbedTokenResponse(
        token=token,
        expires_en_minutos=ttl,
        parent_origin=body.parent_origin,
        propiedades=propiedades,
        moneda=body.moneda.upper(),
        url=url,
        nota=(
            "Envíe este token con postMessage desde el backend de la aplicación padre. "
            "No lo incluya en la URL del iframe. Caduca en " + str(ttl) + " minutos."
        ) + "\n\n" + snippet,
    )
