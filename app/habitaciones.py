"""Room-type performance analytics and declared capacity.

Occupancy by room type is only calculated when the property explicitly
declares how many rooms of that type exist. Without that configuration the
values are served as ``null``; the platform never infers capacity from the
total room count or from reservation volume.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .auth import (
    allowed_property_ids,
    assert_property_access,
    get_current_user,
    properties_for_user,
    require_editor,
)
from .database import get_db
from .models import (
    CapacidadHabitacion,
    DimFecha,
    DimMarca,
    DimTipoHabitacion,
    EventoAuditoria,
    HechoReserva,
    IncidenciaOperativa,
    Usuario,
)
from .schemas import CapacidadHabitacionCreate

router = APIRouter(prefix="/api/habitaciones", tags=["Habitaciones"])

CANCELLED = ("cancelada", "cancelado", "cancelled", "canceled", "anulada", "anulado")


def _scope_ids(db: Session, user: Usuario, requested: int | None) -> list[int] | None:
    """Return the property ids to query: a concrete list or ``None`` for all."""
    allowed = allowed_property_ids(db, user)
    if requested is not None:
        if not db.get(DimMarca, requested):
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
        assert_property_access(db, user, requested)
        return [requested]
    return allowed


def _period(desde: str | None, hasta: str | None) -> tuple[str | None, str | None]:
    clean = []
    for value, name in ((desde, "desde"), (hasta, "hasta")):
        if value in (None, ""):
            clean.append(None)
            continue
        try:
            clean.append(datetime.strptime(value, "%Y-%m-%d").date().isoformat())
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"El parámetro {name} debe tener formato YYYY-MM-DD.",
            )
    if clean[0] and clean[1] and clean[0] > clean[1]:
        raise HTTPException(status_code=422, detail="La fecha 'desde' no puede ser posterior a 'hasta'.")
    return clean[0], clean[1]


def _period_nights(desde: str | None, hasta: str | None) -> int | None:
    if not desde or not hasta:
        return None
    try:
        return (date.fromisoformat(hasta) - date.fromisoformat(desde)).days + 1
    except ValueError:
        return None


def _audit(db: Session, user: Usuario, action: str, detail: str) -> None:
    db.add(EventoAuditoria(accion=action[:50], usuario=user.username, detalle=detail[:500]))


def _masked(row: HechoReserva, hide_pii: bool) -> dict:
    return {
        "id": row.id,
        "codigo": "" if hide_pii else (row.codigo or ""),
        "huesped": "" if hide_pii else (row.huesped or ""),
        "habitacion": row.habitacion or "",
        "tipo_habitacion": row.tipo_habitacion.nombre if row.tipo_habitacion else "",
        "fecha": row.fecha.fecha if row.fecha else None,
        "noches": float(row.noches or 0.0),
        "valor": float(row.valor or 0.0),
        "moneda": row.moneda or "CLP",
        "canal": row.canal.nombre if row.canal else "",
        "estado": row.estado or "",
        "propiedad_id": row.marca_id,
        "propiedad": row.marca.nombre if row.marca else "",
    }


@router.get("/resumen")
def room_summary(
    propiedad_id: int | None = Query(None, ge=1),
    moneda: str = Query("CLP", pattern=r"^[A-Z]{3}$"),
    desde: str | None = Query(None, max_length=10),
    hasta: str | None = Query(None, max_length=10),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    """Occupancy, ADR and RevPAR per room type plus stay-level detail."""
    desde, hasta = _period(desde, hasta)
    scope = _scope_ids(db, user, propiedad_id)
    nights_in_period = _period_nights(desde, hasta)
    hide_pii = user.rol == "lector"

    # Declared capacity is the only valid denominator for room-type occupancy.
    cap_query = db.query(CapacidadHabitacion, DimTipoHabitacion.nombre).join(
        DimTipoHabitacion, CapacidadHabitacion.tipo_habitacion_id == DimTipoHabitacion.id
    )
    if scope is not None:
        if not scope:
            cap_query = cap_query.filter(CapacidadHabitacion.marca_id == -1)
        else:
            cap_query = cap_query.filter(CapacidadHabitacion.marca_id.in_(scope))
    capacities: dict[tuple[int, str], int] = {}
    for row, type_name in cap_query.all():
        capacities[(row.marca_id, type_name)] = int(row.habitaciones or 0)

    query = db.query(HechoReserva).outerjoin(DimMarca, HechoReserva.marca_id == DimMarca.id)
    if scope is not None:
        if not scope:
            query = query.filter(HechoReserva.marca_id == -1)
        else:
            query = query.filter(HechoReserva.marca_id.in_(scope))
    if desde or hasta:
        query = query.join(DimFecha, HechoReserva.fecha_id == DimFecha.id)
        if desde:
            query = query.filter(DimFecha.fecha >= desde)
        if hasta:
            query = query.filter(DimFecha.fecha <= hasta)
    query = query.filter(HechoReserva.moneda == moneda)

    rows = query.all()
    rows = [r for r in rows if (r.estado or "").casefold() not in CANCELLED]

    # Room-level incident linkage. Open operational incidents are counted per
    # property + room number so the rooms view can prioritise guest response.
    incident_query = db.query(IncidenciaOperativa)
    if scope is not None:
        if not scope:
            incident_query = incident_query.filter(IncidenciaOperativa.propiedad_id == -1)
        else:
            incident_query = incident_query.filter(IncidenciaOperativa.propiedad_id.in_(scope))
    incidents = incident_query.all()
    open_by_room: dict[tuple[int | None, str], int] = defaultdict(int)
    total_by_room: dict[tuple[int | None, str], int] = defaultdict(int)
    open_total = 0
    for incident in incidents:
        key = (incident.propiedad_id, (incident.habitacion or "").strip())
        if not key[1]:
            continue
        total_by_room[key] += 1
        if incident.estado in ("abierta", "en_progreso"):
            open_by_room[key] += 1
            open_total += 1

    brand_ids = {r.marca_id for r in rows if r.marca_id is not None}
    brand_ids.update(marca_id for (marca_id, _) in capacities if marca_id is not None)
    brands = {b.id: b.nombre for b in db.query(DimMarca).filter(DimMarca.id.in_(brand_ids)).all()} if brand_ids else {}

    groups: dict[tuple[int | None, str], dict] = {}

    def bucket(marca_id: int | None, type_name: str) -> dict:
        key = (marca_id, type_name)
        if key not in groups:
            groups[key] = {
                "propiedad_id": marca_id,
                "propiedad": brands.get(marca_id, ""),
                "tipo_habitacion": type_name,
                "habitaciones": capacities.get((marca_id, type_name)),
                "reservas": 0,
                "noches_vendidas": 0.0,
                "reservas_con_noches": 0,
                "ingresos": 0.0,
                "reservas_con_valor": 0,
                "canales": defaultdict(lambda: {"noches": 0.0, "ingresos": 0.0}),
            }
        return groups[key]

    # Include configured types even when they have no reservations in the period.
    for (marca_id, type_name) in capacities:
        bucket(marca_id, type_name)

    for row in rows:
        type_name = row.tipo_habitacion.nombre if row.tipo_habitacion else "Sin tipo"
        group = bucket(row.marca_id, type_name)
        group["reservas"] += 1
        nights = float(row.noches or 0.0)
        if nights > 0:
            group["noches_vendidas"] += nights
            group["reservas_con_noches"] += 1
        value = float(row.valor or 0.0)
        group["ingresos"] += value
        group["reservas_con_valor"] += 1
        channel = row.canal.nombre if row.canal else "Sin canal"
        group["canales"][channel]["noches"] += nights
        group["canales"][channel]["ingresos"] += value

    tipos = []
    for (marca_id, type_name), group in groups.items():
        noches = group["noches_vendidas"] if group["reservas_con_noches"] else None
        ingresos = group["ingresos"] if group["reservas_con_valor"] else None
        capacidad = group["habitaciones"]
        ocupacion = None
        revpar = None
        if capacidad is not None and capacidad > 0 and nights_in_period and noches is not None:
            ocupacion = round(noches / (capacidad * nights_in_period) * 100, 2)
            if ingresos is not None:
                revpar = round(ingresos / (capacidad * nights_in_period), 2)
        adr = round(ingresos / noches, 2) if ingresos is not None and noches and noches > 0 else None
        canales = {
            "labels": sorted(group["canales"].keys()),
            "noches": [round(group["canales"][c]["noches"], 2) for c in sorted(group["canales"].keys())],
            "ingresos": [round(group["canales"][c]["ingresos"], 2) for c in sorted(group["canales"].keys())],
        }
        tipos.append({
            "propiedad_id": marca_id,
            "propiedad": group["propiedad"],
            "tipo_habitacion": type_name,
            "habitaciones": capacidad,
            "reservas": group["reservas"],
            "noches_vendidas": round(noches, 2) if noches is not None else None,
            "ingresos": round(ingresos, 2) if ingresos is not None else None,
            "adr": adr,
            "ocupacion": ocupacion,
            "revpar": revpar,
            "canales": canales,
        })

    tipos.sort(key=lambda item: (item["propiedad"] or "", item["tipo_habitacion"] or ""))

    # Breakdown by physical room number. Occupancy uses the stay nights over
    # the period (one room can only be sold once per night); ADR is revenue per
    # sold night. Guest names stay masked for readers.
    rooms: dict[tuple[int | None, str], dict] = {}
    for row in rows:
        room_number = (row.habitacion or "").strip()
        if not room_number:
            continue
        key = (row.marca_id, room_number)
        group = rooms.get(key)
        if group is None:
            group = rooms[key] = {
                "propiedad_id": row.marca_id,
                "propiedad": brands.get(row.marca_id, ""),
                "habitacion": room_number,
                "tipo_habitacion": "",
                "huesped": "",
                "estado": "",
                "reservas": 0,
                "noches_vendidas": 0.0,
                "reservas_con_noches": 0,
                "ingresos": 0.0,
                "reservas_con_valor": 0,
                "ultima_fecha": "",
                "tipos": defaultdict(int),
            }
        group["reservas"] += 1
        type_name = row.tipo_habitacion.nombre if row.tipo_habitacion else "Sin tipo"
        group["tipos"][type_name] += 1
        nights = float(row.noches or 0.0)
        if nights > 0:
            group["noches_vendidas"] += nights
            group["reservas_con_noches"] += 1
        value = float(row.valor or 0.0)
        group["ingresos"] += value
        group["reservas_con_valor"] += 1
        stay_date = row.fecha.fecha if row.fecha else ""
        if not group["ultima_fecha"] or stay_date >= group["ultima_fecha"]:
            group["ultima_fecha"] = stay_date
            group["tipo_habitacion"] = type_name
            group["huesped"] = "" if hide_pii else (row.huesped or "")
            group["estado"] = row.estado or ""

    por_habitacion = []
    for (marca_id, room_number), group in rooms.items():
        noches = group["noches_vendidas"] if group["reservas_con_noches"] else None
        ingresos = group["ingresos"] if group["reservas_con_valor"] else None
        adr = round(ingresos / noches, 2) if ingresos is not None and noches and noches > 0 else None
        ocupacion = (
            round(noches / nights_in_period * 100, 2)
            if nights_in_period and noches is not None
            else None
        )
        principal_type = max(group["tipos"].items(), key=lambda kv: kv[1])[0] if group["tipos"] else ""
        por_habitacion.append({
            "propiedad_id": marca_id,
            "propiedad": group["propiedad"],
            "habitacion": room_number,
            "tipo_habitacion": group["tipo_habitacion"] or principal_type,
            "huesped": group["huesped"],
            "estado": group["estado"],
            "reservas": group["reservas"],
            "noches_vendidas": round(noches, 2) if noches is not None else None,
            "ingresos": round(ingresos, 2) if ingresos is not None else None,
            "adr": adr,
            "ocupacion": ocupacion,
            "incidencias_abiertas": open_by_room.get((marca_id, room_number), 0),
            "incidencias_total": total_by_room.get((marca_id, room_number), 0),
            "ultima_estancia": group["ultima_fecha"] or None,
        })
    por_habitacion.sort(key=lambda item: (item["propiedad"] or "", item["habitacion"] or ""))

    total_capacity = sum(capacities.values()) if capacities else None
    total_nights = sum(g["noches_vendidas"] for g in groups.values())
    total_revenue = sum(g["ingresos"] for g in groups.values())
    global_occupancy = None
    global_revpar = None
    if total_capacity and total_capacity > 0 and nights_in_period and total_nights:
        global_occupancy = round(total_nights / (total_capacity * nights_in_period) * 100, 2)
        if total_revenue:
            global_revpar = round(total_revenue / (total_capacity * nights_in_period), 2)

    detail = []
    for row in rows:
        item = _masked(row, hide_pii)
        room_key = (row.marca_id, (row.habitacion or "").strip())
        item["incidencias_abiertas"] = open_by_room.get(room_key, 0)
        item["incidencias_total"] = total_by_room.get(room_key, 0)
        detail.append(item)
    detail.sort(key=lambda item: (item["propiedad"], item["habitacion"], item["fecha"] or ""))
    detail_total = len(detail)
    detail = detail[:500]

    return {
        "moneda": moneda,
        "desde": desde,
        "hasta": hasta,
        "noches_periodo": nights_in_period,
        "propiedad_id": propiedad_id,
        "propiedades": [
            {"id": p.id, "nombre": p.nombre, "habitaciones": int(p.habitaciones or 0)}
            for p in properties_for_user(db, user)
        ],
        "resumen": {
            "habitaciones_configuradas": total_capacity,
            "tipos_configurados": len(capacities),
            "reservas": len(rows),
            "noches_vendidas": round(total_nights, 2) if total_nights else None,
            "ingresos": round(total_revenue, 2) if total_revenue else None,
            "ocupacion": global_occupancy,
            "revpar": global_revpar,
            "incidencias_abiertas": open_total,
            "habitaciones_con_actividad": len(por_habitacion),
        },
        "tipos": tipos,
        "por_habitacion": por_habitacion,
        "detalle": detail,
        "detalle_total": detail_total,
        "detalle_limitado": detail_total > len(detail),
    }


@router.get("/tipos")
def room_types(
    db: Session = Depends(get_db),
    _: Usuario = Depends(get_current_user),
):
    """Available room-type dimension values, used by the capacity form."""
    return [
        {"id": row.id, "nombre": row.nombre}
        for row in db.query(DimTipoHabitacion).order_by(DimTipoHabitacion.nombre).all()
    ]


@router.get("/capacidades")
def list_capacities(
    propiedad_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    scope = _scope_ids(db, user, propiedad_id)
    query = db.query(CapacidadHabitacion, DimTipoHabitacion.nombre, DimMarca.nombre).join(
        DimTipoHabitacion, CapacidadHabitacion.tipo_habitacion_id == DimTipoHabitacion.id
    ).outerjoin(DimMarca, CapacidadHabitacion.marca_id == DimMarca.id)
    if scope is not None:
        if not scope:
            query = query.filter(CapacidadHabitacion.marca_id == -1)
        else:
            query = query.filter(CapacidadHabitacion.marca_id.in_(scope))
    return [
        {
            "id": row.id,
            "marca_id": row.marca_id,
            "propiedad": prop_name or "",
            "tipo_habitacion_id": row.tipo_habitacion_id,
            "tipo_habitacion": type_name,
            "habitaciones": int(row.habitaciones or 0),
            "actualizado_por": row.actualizado_por or "",
            "actualizado_en": row.actualizado_en,
        }
        for row, type_name, prop_name in query.order_by(DimMarca.nombre, DimTipoHabitacion.nombre).all()
    ]


@router.post("/capacidades", status_code=201)
def upsert_capacity(
    body: CapacidadHabitacionCreate,
    db: Session = Depends(get_db),
    user: Usuario = Depends(require_editor),
):
    """Declare (or update) how many rooms of a type a property has."""
    assert_property_access(db, user, body.marca_id)
    if not db.get(DimMarca, body.marca_id):
        raise HTTPException(status_code=404, detail="Propiedad no encontrada.")

    type_name = body.tipo_habitacion.strip()[:100]
    room_type = db.query(DimTipoHabitacion).filter_by(nombre=type_name).first()
    if room_type is None:
        room_type = DimTipoHabitacion(nombre=type_name)
        db.add(room_type)
        db.flush()

    row = db.query(CapacidadHabitacion).filter_by(
        marca_id=body.marca_id, tipo_habitacion_id=room_type.id
    ).first()
    created = row is None
    if row is None:
        row = CapacidadHabitacion(
            marca_id=body.marca_id,
            tipo_habitacion_id=room_type.id,
            habitaciones=body.habitaciones,
            actualizado_por=user.username,
        )
        db.add(row)
    else:
        row.habitaciones = body.habitaciones
        row.actualizado_por = user.username
    _audit(
        db, user, "capacidad_habitacion_guardar",
        f"{body.marca_id}:{type_name}:{body.habitaciones}",
    )
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "marca_id": row.marca_id,
        "tipo_habitacion_id": row.tipo_habitacion_id,
        "tipo_habitacion": type_name,
        "habitaciones": int(row.habitaciones or 0),
        "creado": created,
    }


@router.delete("/capacidades/{capacidad_id}")
def delete_capacity(
    capacidad_id: int,
    db: Session = Depends(get_db),
    user: Usuario = Depends(require_editor),
):
    row = db.get(CapacidadHabitacion, capacidad_id)
    if not row:
        raise HTTPException(status_code=404, detail="Configuración de capacidad no encontrada.")
    assert_property_access(db, user, row.marca_id)
    db.delete(row)
    _audit(db, user, "capacidad_habitacion_eliminar", str(capacidad_id))
    db.commit()
    return {"ok": True}
