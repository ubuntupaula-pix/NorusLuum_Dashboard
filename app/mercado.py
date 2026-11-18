"""Market benchmarking against public references.

References may be entered manually, imported from an authorized file or
synchronized on demand from whitelisted keyless public APIs. The platform
never invents market prices: every record stores its source, period, currency
and URL so any comparison can be traced back to its origin.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .auth import (
    allowed_property_ids,
    assert_property_access,
    get_current_user,
    properties_for_user,
    require_editor,
)
from . import mercado_publico
from .database import get_db
from .etl import a_numero, leer_tabla
from .models import (
    DimMarca,
    EventoAuditoria,
    HechoMetricaMensual,
    ReferenciaMercado,
    Usuario,
)
from .schemas import (
    MercadoReferenciaCreate,
    MercadoReferenciaUpdate,
    _validar_periodo_mercado,
    _validar_url_mercado,
)

router = APIRouter(prefix="/api/mercado", tags=["Mercado"])

METRICAS = {"adr", "revpar", "ocupacion", "precio"}
# ``precio`` is the long-term public reference price (published rack or
# long-stay price). It is aggregated from income/nights exactly like ADR but is
# labelled separately so a benchmark is never confused with the hotel's ADR.
REFERENCE_FIELDS = (
    "fuente", "url", "tipo", "metrica", "periodo", "valor", "moneda",
    "tipo_habitacion", "notas",
)


def _scope_ids(db: Session, user: Usuario, requested: int | None) -> list[int] | None:
    allowed = allowed_property_ids(db, user)
    if requested is not None:
        if not db.get(DimMarca, requested):
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
        assert_property_access(db, user, requested)
        return [requested]
    return allowed


def _audit(db: Session, user: Usuario, action: str, detail: str) -> None:
    db.add(EventoAuditoria(accion=action[:50], usuario=user.username, detalle=detail[:500]))


def _split_period(value: str | None, name: str, default_month: int) -> tuple[int, int] | None:
    if value in (None, ""):
        return None
    value = str(value).strip()
    if not re.fullmatch(r"\d{4}(-\d{2})?", value):
        raise HTTPException(status_code=422, detail=f"El parámetro {name} debe tener formato YYYY-MM o YYYY.")
    try:
        year = int(value[:4])
        month = int(value[5:7]) if "-" in value else default_month
    except ValueError:
        raise HTTPException(status_code=422, detail=f"El parámetro {name} no es válido.")
    if not 1900 <= year <= 2100 or not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail=f"El parámetro {name} está fuera de rango.")
    return year, month


def _market_scope_filter(query, scope, requested: int | None):
    if requested is not None:
        return query.filter(or_(ReferenciaMercado.marca_id.is_(None), ReferenciaMercado.marca_id == requested))
    if scope is None:
        # Global users without a selected property compare against general
        # market references only; mixing unrelated properties would distort it.
        return query.filter(ReferenciaMercado.marca_id.is_(None))
    if not scope:
        return query.filter(ReferenciaMercado.marca_id.is_(None))
    return query.filter(or_(ReferenciaMercado.marca_id.is_(None), ReferenciaMercado.marca_id.in_(scope)))


def _reference_payload(row: ReferenciaMercado, property_name: str = "") -> dict:
    return {
        "id": row.id,
        "marca_id": row.marca_id,
        "propiedad": property_name,
        "fuente": row.fuente,
        "url": row.url or "",
        "tipo": row.tipo or "mercado",
        "metrica": row.metrica,
        "periodo": row.periodo,
        "valor": float(row.valor or 0.0),
        "moneda": row.moneda or "CLP",
        "tipo_habitacion": row.tipo_habitacion or "",
        "notas": row.notas or "",
        "creado_por": row.creado_por or "",
        "creado_en": row.creado_en,
        "actualizado_en": row.actualizado_en,
    }


def _store_reference(db: Session, data: dict, marca_id: int | None, user: Usuario):
    row = db.query(ReferenciaMercado).filter_by(
        marca_id=marca_id,
        fuente=data["fuente"],
        metrica=data["metrica"],
        periodo=data["periodo"],
        moneda=data["moneda"],
        tipo_habitacion=data.get("tipo_habitacion", ""),
    ).first()
    created = row is None
    if row is None:
        row = ReferenciaMercado(marca_id=marca_id, creado_por=user.username, **data)
        db.add(row)
    else:
        for field, value in data.items():
            setattr(row, field, value)
        if not row.creado_por:
            row.creado_por = user.username
    return row, created


@router.get("/referencias")
def list_references(
    propiedad_id: int | None = Query(None, ge=1),
    metrica: str = Query("", max_length=20),
    desde: str | None = Query(None, max_length=7),
    hasta: str | None = Query(None, max_length=7),
    moneda: str = Query("", max_length=3),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    scope = _scope_ids(db, user, propiedad_id)
    query = db.query(ReferenciaMercado).outerjoin(DimMarca, ReferenciaMercado.marca_id == DimMarca.id)
    query = _market_scope_filter(query, scope, propiedad_id)
    if metrica:
        if metrica not in METRICAS:
            raise HTTPException(status_code=422, detail="Métrica inválida.")
        query = query.filter(ReferenciaMercado.metrica == metrica)
    if moneda:
        query = query.filter(ReferenciaMercado.moneda == moneda.upper())
    rows = query.order_by(ReferenciaMercado.periodo.desc(), ReferenciaMercado.fuente).all()
    if desde:
        rows = [r for r in rows if r.periodo >= str(desde)]
    if hasta:
        rows = [r for r in rows if r.periodo <= str(hasta)]
    return [
        _reference_payload(row, row.marca.nombre if row.marca else "")
        for row in rows
    ]


@router.post("/referencias", status_code=201)
def create_reference(
    body: MercadoReferenciaCreate,
    db: Session = Depends(get_db),
    user: Usuario = Depends(require_editor),
):
    allowed = allowed_property_ids(db, user)
    marca_id = body.propiedad_id
    if marca_id is not None:
        assert_property_access(db, user, marca_id)
        if not db.get(DimMarca, marca_id):
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
    elif allowed is not None:
        if len(allowed) != 1:
            raise HTTPException(
                status_code=422,
                detail="Seleccione la propiedad de la referencia o solicite acceso global.",
            )
        marca_id = allowed[0]
    if body.metrica == "ocupacion" and body.valor > 100:
        raise HTTPException(status_code=422, detail="La ocupación de referencia debe estar entre 0 y 100.")

    data = body.model_dump(exclude={"propiedad_id"})
    data["url"] = (data.get("url") or "").strip()
    data["tipo_habitacion"] = (data.get("tipo_habitacion") or "").strip()[:100]
    row, created = _store_reference(db, data, marca_id, user)
    _audit(db, user, "mercado_referencia_guardar", f"{row.periodo}:{row.fuente}:{row.metrica}")
    db.commit()
    db.refresh(row)
    payload = _reference_payload(row, row.marca.nombre if row.marca else "")
    payload["creado"] = created
    return payload


@router.put("/referencias/{reference_id}")
def update_reference(
    reference_id: int,
    body: MercadoReferenciaUpdate,
    db: Session = Depends(get_db),
    user: Usuario = Depends(require_editor),
):
    row = db.get(ReferenciaMercado, reference_id)
    if not row:
        raise HTTPException(status_code=404, detail="Referencia de mercado no encontrada.")
    assert_property_access(db, user, row.marca_id)

    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    if "propiedad_id" in updates:
        target = updates.pop("propiedad_id")
        assert_property_access(db, user, target)
        if not db.get(DimMarca, target):
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
        row.marca_id = target
    if updates.get("metrica") == "ocupacion" and updates.get("valor", row.valor) > 100:
        raise HTTPException(status_code=422, detail="La ocupación de referencia debe estar entre 0 y 100.")
    if row.metrica == "ocupacion" and updates.get("valor", row.valor) > 100:
        raise HTTPException(status_code=422, detail="La ocupación de referencia debe estar entre 0 y 100.")

    for field, value in updates.items():
        if field not in REFERENCE_FIELDS:
            continue
        if field in ("url", "tipo_habitacion", "notas"):
            value = str(value or "").strip()
        setattr(row, field, value)

    conflict = db.query(ReferenciaMercado).filter(
        ReferenciaMercado.id != row.id,
        ReferenciaMercado.marca_id == row.marca_id,
        ReferenciaMercado.fuente == row.fuente,
        ReferenciaMercado.metrica == row.metrica,
        ReferenciaMercado.periodo == row.periodo,
        ReferenciaMercado.moneda == row.moneda,
        ReferenciaMercado.tipo_habitacion == (row.tipo_habitacion or ""),
    ).first()
    if conflict:
        raise HTTPException(status_code=409, detail="Ya existe una referencia con la misma fuente, métrica y período.")

    _audit(db, user, "mercado_referencia_actualizar", f"{row.periodo}:{row.fuente}:{row.metrica}")
    db.commit()
    db.refresh(row)
    return _reference_payload(row, row.marca.nombre if row.marca else "")


@router.delete("/referencias/{reference_id}")
def delete_reference(
    reference_id: int,
    db: Session = Depends(get_db),
    user: Usuario = Depends(require_editor),
):
    row = db.get(ReferenciaMercado, reference_id)
    if not row:
        raise HTTPException(status_code=404, detail="Referencia de mercado no encontrada.")
    assert_property_access(db, user, row.marca_id)
    db.delete(row)
    _audit(db, user, "mercado_referencia_eliminar", str(reference_id))
    db.commit()
    return {"ok": True}


def _as_float(value):
    if value is None or str(value).strip() == "":
        return None
    return a_numero(value)


@router.post("/importar")
async def import_references(
    file: UploadFile = File(...),
    propiedad_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    user: Usuario = Depends(require_editor),
):
    """Import authorized market references from CSV, TSV, XLSX or JSON.

    Every row must carry source attribution (``fuente``) and a ``periodo`` so
    the comparison stays auditable. Long-term public prices use the ``precio``
    metric alongside ADR, RevPAR and occupancy.
    """
    ext = (file.filename or "").lower().rsplit(".", 1)[-1]
    if ext not in {"csv", "tsv", "xlsx", "json"}:
        raise HTTPException(status_code=400, detail="Solo se admiten archivos CSV, TSV, XLSX o JSON.")
    max_bytes = int(os.environ.get("NORUS_MARKET_IMPORT_MAX_BYTES", str(512 * 1024 * 1024)))
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="El archivo de mercado supera el tamaño permitido.")
    if not content:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    try:
        filas, columnas = leer_tabla(file.filename or f"referencias.{ext}", content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "No se pudo leer el archivo.")
    headers = [str(h or "").strip().lower() for h in columnas]
    if len(headers) != len(set(headers)):
        raise HTTPException(status_code=400, detail="El archivo tiene encabezados duplicados.")
    required = {"fuente", "metrica", "periodo", "valor"}
    if not required <= set(headers):
        raise HTTPException(
            status_code=400,
            detail="Faltan columnas obligatorias: fuente, metrica, periodo, valor.",
        )


    allowed = allowed_property_ids(db, user)
    if propiedad_id is not None:
        assert_property_access(db, user, propiedad_id)
        if not db.get(DimMarca, propiedad_id):
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
    property_by_name = {
        (p.nombre or "").casefold(): p.id
        for p in db.query(DimMarca).all()
    }

    exitosas = fallidas = 0
    errors: list[str] = []
    for index, raw in enumerate(filas, start=2):
        if not any(str(v or "").strip() for v in raw.values()):
            continue
        values = {str(k or "").strip().lower(): v for k, v in raw.items()}
        try:
            property_name = str(values.get("propiedad") or "").strip()
            target = propiedad_id
            if target is None and property_name:
                target = property_by_name.get(property_name.casefold())
                if not target:
                    raise ValueError("La propiedad indicada no existe.")
            if target is not None:
                assert_property_access(db, user, target)
            elif allowed is not None:
                if len(allowed) != 1:
                    raise ValueError("Indique la propiedad de la referencia.")
                target = allowed[0]

            data = {
                "fuente": str(values.get("fuente") or "").strip()[:180],
                "url": _validar_url_mercado(str(values.get("url") or "")),
                "tipo": str(values.get("tipo") or "mercado").strip().lower()[:40],
                "metrica": str(values.get("metrica") or "").strip().lower(),
                "periodo": _validar_periodo_mercado(str(values.get("periodo") or "")),
                "valor": _as_float(values.get("valor")),
                "moneda": str(values.get("moneda") or os.environ.get("NORUS_DEFAULT_CURRENCY", "CLP")).strip().upper(),
                "tipo_habitacion": str(values.get("tipo_habitacion") or "").strip()[:100],
                "notas": str(values.get("notas") or "").strip()[:2000],
            }
            if data["tipo"] not in {"mercado", "competencia", "referencia", "otro"}:
                data["tipo"] = "mercado"
            if not data["fuente"] or len(data["fuente"]) < 2:
                raise ValueError("La fuente es obligatoria.")
            if data["metrica"] not in METRICAS:
                raise ValueError("La métrica debe ser adr, revpar, ocupacion o precio.")
            if data["valor"] is None or data["valor"] < 0:
                raise ValueError("El valor debe ser un número positivo.")
            if data["metrica"] == "ocupacion" and data["valor"] > 100:
                raise ValueError("La ocupación debe estar entre 0 y 100.")
            if not re.fullmatch(r"[A-Z]{3}", data["moneda"]):
                raise ValueError("La moneda debe ser un código ISO de 3 letras.")
            _store_reference(db, data, target, user)
            exitosas += 1
        except Exception as exc:  # noqa: BLE001 - per-row validation feedback
            fallidas += 1
            if len(errors) < 20:
                errors.append(f"Fila {index}: {exc}")

    _audit(db, user, "mercado_importar", f"{exitosas} referencias · {fallidas} rechazadas")
    db.commit()
    return {
        "filas_procesadas": exitosas + fallidas,
        "exitosas": exitosas,
        "fallidas": fallidas,
        "estado": "completada" if not fallidas else ("parcial" if exitosas else "fallida"),
        "errores": errors,
    }


@router.get("/publico/fuentes")
def public_sources(user: Usuario = Depends(get_current_user)):
    """Lista las APIs públicas sin clave disponibles para sincronizar."""
    return mercado_publico.describe_sources()


@router.post("/publico/actualizar")
def refresh_public_references(
    moneda: str = Query("CLP", pattern=r"^[A-Z]{3}$"),
    propiedad_id: int | None = Query(None, ge=1),
    fuentes: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
    user: Usuario = Depends(require_editor),
):
    """Descarga referencias públicas verificables y las guarda como referencias.

    La descarga es manual, usa lista blanca de hosts y conserva atribución.
    Una fuente caída no impide usar las demás; el estado de cada una se
    devuelve al cliente y queda registrado en auditoría.
    """
    if propiedad_id is not None:
        if not db.get(DimMarca, propiedad_id):
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
        assert_property_access(db, user, propiedad_id)

    selected = None
    if fuentes:
        selected = [name.strip() for name in fuentes.split(",") if name.strip()]
    try:
        references, statuses = mercado_publico.fetch_public_references(moneda, selected)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    creadas = actualizadas = 0
    for data in references:
        _, created = _store_reference(db, data, propiedad_id, user)
        if created:
            creadas += 1
        else:
            actualizadas += 1

    errors = [s for s in statuses if s["estado"] == "error"]
    if errors and references:
        state = "parcial"
    elif errors:
        state = "fallida"
    else:
        state = "completada"
    _audit(
        db,
        user,
        "mercado_publico_sincronizar",
        f"{moneda} · {len(references)} referencias · {creadas} nuevas · {actualizadas} actualizadas",
    )
    db.commit()
    return {
        "estado": state,
        "moneda": moneda,
        "propiedad_id": propiedad_id,
        "referencias_recibidas": len(references),
        "creadas": creadas,
        "actualizadas": actualizadas,
        "fuentes": statuses,
        "nota": "Las referencias se guardan con fuente, URL, período, moneda y atribución. "
                "La comparación usa los mismos datos públicos registrados.",
    }


def _aggregate_metric(rows, metrica: str):
    adr_values = [r.adr for r in rows if r.adr is not None]
    revpar_values = [r.revpar for r in rows if r.revpar is not None]
    occ_values = [r.ocupacion for r in rows if r.ocupacion is not None]
    income_values = [r.ingresos for r in rows if r.ingresos is not None]
    nights_values = [r.noches_vendidas for r in rows if r.noches_vendidas is not None]
    available_values = [
        r.habitaciones_disponibles for r in rows
        if r.habitaciones_disponibles is not None
    ]
    income = sum(income_values) if income_values else None
    nights = sum(nights_values) if nights_values else None
    available = sum(available_values) if available_values else None

    if metrica in ("adr", "precio"):
        if income is not None and nights and nights > 0:
            return income / nights
        return sum(adr_values) / len(adr_values) if adr_values else None
    if metrica == "revpar":
        if income is not None and available and available > 0:
            return income / available
        if revpar_values:
            return sum(revpar_values) / len(revpar_values)
        if adr_values and occ_values:
            return (sum(adr_values) / len(adr_values)) * (sum(occ_values) / len(occ_values)) / 100
        return None
    if occ_values or (nights is not None and available):
        if nights is not None and available and available > 0:
            return nights / available * 100
        return sum(occ_values) / len(occ_values) if occ_values else None
    return None


@router.get("/comparacion")
def comparison(
    propiedad_id: int | None = Query(None, ge=1),
    metrica: str = Query("adr"),
    moneda: str = Query("CLP", pattern=r"^[A-Z]{3}$"),
    desde: str | None = Query(None, max_length=7),
    hasta: str | None = Query(None, max_length=7),
    granularidad: str = Query("mes"),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    """Compare hotel monthly metrics with registered public market references."""
    if metrica not in METRICAS:
        raise HTTPException(status_code=422, detail="Métrica inválida.")
    if granularidad not in {"mes", "anio"}:
        raise HTTPException(status_code=422, detail="Granularidad inválida.")
    since = _split_period(desde, "desde", 1)
    until = _split_period(hasta, "hasta", 12)
    if since and until and since > until:
        raise HTTPException(status_code=422, detail="El período 'desde' no puede ser posterior a 'hasta'.")

    scope = _scope_ids(db, user, propiedad_id)

    hotel_query = db.query(HechoMetricaMensual).filter(HechoMetricaMensual.moneda == moneda)
    if scope is not None:
        if not scope:
            hotel_query = hotel_query.filter(HechoMetricaMensual.marca_id == -1)
        else:
            hotel_query = hotel_query.filter(HechoMetricaMensual.marca_id.in_(scope))
    if since:
        hotel_query = hotel_query.filter(
            or_(
                HechoMetricaMensual.anio > since[0],
                (HechoMetricaMensual.anio == since[0]) & (HechoMetricaMensual.mes >= since[1]),
            )
        )
    if until:
        hotel_query = hotel_query.filter(
            or_(
                HechoMetricaMensual.anio < until[0],
                (HechoMetricaMensual.anio == until[0]) & (HechoMetricaMensual.mes <= until[1]),
            )
        )
    hotel_rows = hotel_query.all()

    hotel_by_key = defaultdict(list)
    for row in hotel_rows:
        key = (row.anio,) if granularidad == "anio" else (row.anio, row.mes)
        hotel_by_key[key].append(row)
    hotel_values = {
        key: _aggregate_metric(rows, metrica)
        for key, rows in hotel_by_key.items()
    }

    market_query = db.query(ReferenciaMercado).filter(
        ReferenciaMercado.metrica == metrica,
        ReferenciaMercado.moneda == moneda,
    )
    market_query = _market_scope_filter(market_query, scope, propiedad_id)
    market_rows = market_query.all()

    market_by_key = defaultdict(list)
    for row in market_rows:
        period = row.periodo or ""
        if "-" in period:
            try:
                year, month = int(period[:4]), int(period[5:7])
            except ValueError:
                continue
            if granularidad == "mes":
                key = (year, month)
            else:
                key = (year,)
        else:
            try:
                key = (int(period[:4]),)
            except ValueError:
                continue
            if granularidad == "mes":
                # Annual references are not comparable to a single month.
                continue
        if since:
            if granularidad == "mes" and key < since:
                continue
            if granularidad == "anio" and key[0] < since[0]:
                continue
        if until:
            if granularidad == "mes" and key > until:
                continue
            if granularidad == "anio" and key[0] > until[0]:
                continue
        market_by_key[key].append(row)

    market_values = {
        key: (sum(float(r.valor or 0.0) for r in rows) / len(rows) if rows else None)
        for key, rows in market_by_key.items()
    }

    periods = sorted(set(hotel_values) | set(market_values))
    labels = [
        str(key[0]) if granularidad == "anio" else f"{key[0]:04d}-{key[1]:02d}"
        for key in periods
    ]
    hotel_series = [round(hotel_values[key], 2) if hotel_values.get(key) is not None else None for key in periods]
    market_series = [round(market_values[key], 2) if market_values.get(key) is not None else None for key in periods]
    gaps = []
    for hotel_value, market_value in zip(hotel_series, market_series):
        if hotel_value is None or market_value in (None, 0):
            gaps.append(None)
        else:
            gaps.append(round((hotel_value - market_value) / market_value * 100, 2))

    valid_hotel = [v for v in hotel_series if v is not None]
    valid_market = [v for v in market_series if v is not None]
    average_hotel = round(sum(valid_hotel) / len(valid_hotel), 2) if valid_hotel else None
    average_market = round(sum(valid_market) / len(valid_market), 2) if valid_market else None
    average_gap = (
        round((average_hotel - average_market) / average_market * 100, 2)
        if average_hotel is not None and average_market not in (None, 0)
        else None
    )

    brands = {
        b.id: b.nombre
        for b in db.query(DimMarca).filter(DimMarca.id.in_(
            {r.marca_id for r in market_rows if r.marca_id is not None}
        )).all()
    } if any(r.marca_id is not None for r in market_rows) else {}

    references = [
        _reference_payload(row, brands.get(row.marca_id, ""))
        for row in sorted(market_rows, key=lambda r: (r.periodo, r.fuente))
    ]

    return {
        "metrica": metrica,
        "moneda": moneda,
        "desde": desde,
        "hasta": hasta,
        "granularidad": granularidad,
        "propiedad_id": propiedad_id,
        "propiedades": [
            {"id": p.id, "nombre": p.nombre}
            for p in properties_for_user(db, user)
        ],
        "periodos": labels,
        "hotel": hotel_series,
        "mercado": market_series,
        "brecha_pct": gaps,
        "promedio": {
            "hotel": average_hotel,
            "mercado": average_market,
            "brecha_pct": average_gap,
        },
        "referencias": references,
        "nota": (
            "Los valores del mercado provienen de referencias públicas registradas "
            "manualmente, importadas o sincronizadas desde APIs públicas sin clave "
            "(EA Hospitality Pulse y Apartments Zürich), siempre con fuente, URL, "
            "período y moneda. La métrica «precio» recoge tarifas públicas de largo "
            "plazo. Los períodos sin referencia se muestran vacíos; la plataforma "
            "no estima precios públicos."
        ),
    }
