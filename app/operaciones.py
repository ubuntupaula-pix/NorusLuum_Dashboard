"""Operational hotel workflows exposed through authenticated APIs.

Every read and write is constrained by the caller's property scope.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
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
    DimFecha,
    DimMarca,
    EjecucionChecklist,
    EventoAuditoria,
    HechoReserva,
    IncidenciaOperativa,
    ItemInventario,
    ObjetoPerdido,
    OrdenMantenimiento,
    TareaOperativa,
    Usuario,
)
from .schemas import (
    ChecklistCreate,
    EstadoUpdate,
    IncidenciaCreate,
    IncidenciaReservaUpdate,
    InventarioCreate,
    MantenimientoCreate,
    ObjetoPerdidoCreate,
    StockUpdate,
    TareaCreate,
)

router = APIRouter(prefix="/api/operaciones", tags=["Operaciones hoteleras"])

TASK_STATES = {"pendiente", "en_progreso", "completada", "cancelada"}
INCIDENT_STATES = {"abierta", "en_progreso", "resuelta", "cancelada"}
LOST_STATES = {"resguardado", "reclamado", "entregado", "desechado"}
CHECKLIST_STATES = {"en_progreso", "completada", "cancelada"}


def _property_scope(db: Session, user: Usuario, requested: int | None = None):
    """Return None (all), an int, or a list of allowed property ids."""
    allowed = allowed_property_ids(db, user)
    if requested is not None:
        if not db.get(DimMarca, requested):
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
        if allowed is not None and requested not in allowed:
            raise HTTPException(status_code=403, detail="No tiene permisos para esta propiedad.")
        return requested
    return allowed


def _require_property(db: Session, user: Usuario, requested: int | None) -> int | None:
    scope = _property_scope(db, user, requested)
    if scope is None or isinstance(scope, int):
        return scope
    if not scope:
        raise HTTPException(status_code=403, detail="No tiene propiedades asignadas.")
    if len(scope) == 1:
        return scope[0]
    raise HTTPException(status_code=422, detail="Seleccione la propiedad del registro.")


def _filter_property(query, model, scope):
    if scope is None:
        return query
    if isinstance(scope, int):
        return query.filter(model.propiedad_id == scope)
    if not scope:
        return query.filter(model.propiedad_id == -1)
    return query.filter(model.propiedad_id.in_(scope))


def _audit(db: Session, user: Usuario, action: str, detail: str) -> None:
    db.add(EventoAuditoria(accion=action[:50], usuario=user.username, detalle=detail[:500]))


def _items(db: Session, model, scope, limit: int):
    query = _filter_property(db.query(model), model, scope)
    return query.order_by(model.id.desc()).limit(limit).all()


def _checklist(row: EjecucionChecklist) -> dict:
    try:
        elements = json.loads(row.elementos)
    except (TypeError, ValueError):
        elements = []
    return {**{k: v for k, v in row.__dict__.items() if not k.startswith("_")}, "elementos": elements}


@router.get("/propiedades")
def properties(db: Session = Depends(get_db), user: Usuario = Depends(get_current_user)):
    return [
        {"id": p.id, "nombre": p.nombre, "habitaciones": int(p.habitaciones or 0)}
        for p in properties_for_user(db, user)
    ]


@router.get("/resumen")
def overview(
    propiedad_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    scope = _property_scope(db, user, propiedad_id)

    def count(model, *filters):
        query = _filter_property(db.query(func.count(model.id)), model, scope)
        for clause in filters:
            query = query.filter(clause)
        return query.scalar() or 0

    return {
        "tareas_pendientes": count(TareaOperativa, TareaOperativa.estado.in_(["pendiente", "en_progreso"])),
        "incidencias_abiertas": count(IncidenciaOperativa, IncidenciaOperativa.estado.in_(["abierta", "en_progreso"])),
        "incidencias_criticas": count(IncidenciaOperativa, IncidenciaOperativa.severidad == "critica", IncidenciaOperativa.estado != "resuelta"),
        "mantenimientos_pendientes": count(OrdenMantenimiento, OrdenMantenimiento.estado.in_(["pendiente", "en_progreso"])),
        "inventario_bajo": count(ItemInventario, ItemInventario.cantidad <= ItemInventario.stock_minimo),
        "objetos_resguardados": count(ObjetoPerdido, ObjetoPerdido.estado == "resguardado"),
        "checklists_en_progreso": count(EjecucionChecklist, EjecucionChecklist.estado == "en_progreso"),
    }


# ------------------------------------------------------------
# Tareas
# ------------------------------------------------------------

@router.get("/tareas")
def tasks(
    propiedad_id: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    return _items(db, TareaOperativa, _property_scope(db, user, propiedad_id), limit)


@router.post("/tareas", status_code=201)
def create_task(body: TareaCreate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    prop = _require_property(db, user, body.propiedad_id)
    item = TareaOperativa(
        **body.model_dump(exclude={"propiedad_id"}), propiedad_id=prop, creado_por=user.username
    )
    db.add(item)
    _audit(db, user, "ops_tarea_crear", body.titulo)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/tareas/{item_id}")
def update_task(item_id: int, body: EstadoUpdate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    if body.estado not in TASK_STATES:
        raise HTTPException(422, "Estado de tarea inválido.")
    item = db.get(TareaOperativa, item_id)
    if not item:
        raise HTTPException(404, "Tarea no encontrada.")
    assert_property_access(db, user, item.propiedad_id)
    item.estado = body.estado
    _audit(db, user, "ops_tarea_estado", f"{item.id}:{body.estado}")
    db.commit()
    db.refresh(item)
    return item


# ------------------------------------------------------------
# Incidencias
# ------------------------------------------------------------

def _resolve_guest_reservation(db: Session, item: IncidenciaOperativa):
    """Return (reservation, link_kind) for an incident.

    Priority: explicit link, then property + room + stay date. The fallback
    never guesses outside the actual stay window.
    """
    if item.reserva_id:
        reserva = db.get(HechoReserva, item.reserva_id)
        if reserva and (item.propiedad_id is None or reserva.marca_id == item.propiedad_id):
            return reserva, "explicito"

    room = (item.habitacion or "").strip()
    if not room:
        return None, None

    reference = item.creado_en.date() if item.creado_en else date.today()
    query = db.query(HechoReserva).join(DimFecha, HechoReserva.fecha_id == DimFecha.id).filter(
        HechoReserva.habitacion == room
    )
    if item.propiedad_id is not None:
        query = query.filter(HechoReserva.marca_id == item.propiedad_id)
    candidates = []
    for reserva in query.all():
        if not reserva.fecha or not reserva.fecha.fecha:
            continue
        try:
            check_in = date.fromisoformat(reserva.fecha.fecha)
        except ValueError:
            continue
        nights = max(0.0, float(reserva.noches or 0.0))
        check_out = check_in + timedelta(days=max(1, int(round(nights))))
        if check_in <= reference <= check_out:
            candidates.append(reserva)
    if not candidates:
        return None, None
    candidates.sort(key=lambda r: (r.fecha.fecha if r.fecha else "", r.id), reverse=True)
    return candidates[0], "habitacion_fecha"


def _reservation_context(reserva: HechoReserva, hide_pii: bool) -> dict:
    return {
        "id": reserva.id,
        "codigo": "" if hide_pii else (reserva.codigo or ""),
        "huesped": "" if hide_pii else (reserva.huesped or ""),
        "habitacion": reserva.habitacion or "",
        "tipo_habitacion": reserva.tipo_habitacion.nombre if reserva.tipo_habitacion else "",
        "fecha": reserva.fecha.fecha if reserva.fecha else None,
        "noches": float(reserva.noches or 0.0),
        "valor": None if hide_pii else float(reserva.valor or 0.0),
        "moneda": reserva.moneda or "CLP",
        "estado": reserva.estado or "",
        "canal": reserva.canal.nombre if reserva.canal else "",
        "nacionalidad": reserva.nacionalidad.nombre if reserva.nacionalidad else "",
        "propiedad_id": reserva.marca_id,
        "propiedad": reserva.marca.nombre if reserva.marca else "",
    }


@router.get("/incidencias")
def incidents(
    propiedad_id: int | None = Query(None, ge=1),
    habitacion: str | None = Query(None, max_length=30),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    query = db.query(IncidenciaOperativa)
    query = _filter_property(query, IncidenciaOperativa, _property_scope(db, user, propiedad_id))
    if habitacion:
        query = query.filter(IncidenciaOperativa.habitacion == habitacion.strip())
    return query.order_by(IncidenciaOperativa.creado_en.desc()).limit(limit).all()


@router.get("/incidencias/{item_id}/contexto")
def incident_context(
    item_id: int,
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    """Resolve the guest stay behind a room incident.

    Editors and administrators see the linked guest; readers receive the
    operational context with personal data and reservation value removed.
    """
    item = db.get(IncidenciaOperativa, item_id)
    if not item:
        raise HTTPException(404, "Incidencia no encontrada.")
    assert_property_access(db, user, item.propiedad_id)
    reserva, link_kind = _resolve_guest_reservation(db, item)
    hide_pii = user.rol not in ("admin", "editor")

    # Internal linkage with guest management: show the other incidents and the
    # most recent stays for the same room so the response team has context.
    room = (item.habitacion or "").strip()
    room_incidents: list[dict] = []
    room_stays: list[dict] = []
    if room:
        incident_query = db.query(IncidenciaOperativa).filter(IncidenciaOperativa.habitacion == room)
        stay_query = db.query(HechoReserva).filter(HechoReserva.habitacion == room)
        if item.propiedad_id is not None:
            incident_query = incident_query.filter(IncidenciaOperativa.propiedad_id == item.propiedad_id)
            stay_query = stay_query.filter(HechoReserva.marca_id == item.propiedad_id)
        for other in incident_query.order_by(IncidenciaOperativa.creado_en.desc()).limit(20).all():
            room_incidents.append({
                "id": other.id,
                "titulo": other.titulo,
                "categoria": other.categoria,
                "severidad": other.severidad,
                "estado": other.estado,
                "habitacion": other.habitacion,
                "creado_en": other.creado_en.isoformat() if other.creado_en else None,
                "resuelto_en": other.resuelto_en.isoformat() if other.resuelto_en else None,
                "actual": other.id == item.id,
            })
        for stay in stay_query.order_by(HechoReserva.id.desc()).limit(8).all():
            room_stays.append(_reservation_context(stay, hide_pii))

    return {
        "incidencia": {
            "id": item.id,
            "titulo": item.titulo,
            "habitacion": item.habitacion or "",
            "propiedad_id": item.propiedad_id,
            "estado": item.estado,
            "reserva_id": item.reserva_id,
        },
        "vinculo": link_kind,
        "reserva": _reservation_context(reserva, hide_pii) if reserva else None,
        "pii_visible": not hide_pii,
        "incidencias_habitacion": room_incidents,
        "estancias_habitacion": room_stays,
        "resumen_habitacion": {
            "habitacion": room,
            "incidencias_abiertas": sum(
                1 for inc in room_incidents if inc["estado"] in ("abierta", "en_progreso")
            ),
            "incidencias_total": len(room_incidents),
            "estancias_recientes": len(room_stays),
        },
        "nota": (
            "El contexto se resuelve por vínculo explícito o por coincidencia de "
            "propiedad, habitación y fecha de estancia. Sin coincidencia no se "
            "asocian huéspedes."
        ),
    }


@router.patch("/incidencias/{item_id}/reserva")
def link_incident_reservation(
    item_id: int,
    body: IncidenciaReservaUpdate,
    db: Session = Depends(get_db),
    user: Usuario = Depends(require_editor),
):
    item = db.get(IncidenciaOperativa, item_id)
    if not item:
        raise HTTPException(404, "Incidencia no encontrada.")
    assert_property_access(db, user, item.propiedad_id)
    if "reserva_id" not in body.model_fields_set:
        raise HTTPException(422, "Indique reserva_id; use null para desvincular.")

    if body.reserva_id is None:
        item.reserva_id = None
    else:
        reserva = db.get(HechoReserva, body.reserva_id)
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada.")
        assert_property_access(db, user, reserva.marca_id)
        if item.propiedad_id is not None and reserva.marca_id != item.propiedad_id:
            raise HTTPException(422, "La reserva pertenece a otra propiedad.")
        if item.propiedad_id is None:
            item.propiedad_id = reserva.marca_id
        item.reserva_id = reserva.id
    _audit(db, user, "ops_incidencia_reserva", f"{item.id}:{item.reserva_id}")
    db.commit()
    db.refresh(item)
    return item


@router.post("/incidencias", status_code=201)
def create_incident(body: IncidenciaCreate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    prop = _require_property(db, user, body.propiedad_id)
    reserva = None
    if body.reserva_id is not None:
        reserva = db.get(HechoReserva, body.reserva_id)
        if not reserva:
            raise HTTPException(404, "Reserva no encontrada.")
        assert_property_access(db, user, reserva.marca_id)
        if prop is not None and reserva.marca_id != prop:
            raise HTTPException(422, "La reserva pertenece a otra propiedad.")
        if prop is None:
            prop = reserva.marca_id
    item = IncidenciaOperativa(
        **body.model_dump(exclude={"propiedad_id", "reserva_id"}),
        reserva_id=reserva.id if reserva else None,
        propiedad_id=prop,
        creado_por=user.username,
    )
    db.add(item)
    _audit(db, user, "ops_incidencia_crear", body.titulo)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/incidencias/{item_id}")
def update_incident(item_id: int, body: EstadoUpdate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    if body.estado not in INCIDENT_STATES:
        raise HTTPException(422, "Estado de incidencia inválido.")
    item = db.get(IncidenciaOperativa, item_id)
    if not item:
        raise HTTPException(404, "Incidencia no encontrada.")
    assert_property_access(db, user, item.propiedad_id)
    item.estado = body.estado
    item.resuelto_en = datetime.now(UTC) if body.estado == "resuelta" else None
    _audit(db, user, "ops_incidencia_estado", f"{item.id}:{body.estado}")
    db.commit()
    db.refresh(item)
    return item


# ------------------------------------------------------------
# Mantenimiento
# ------------------------------------------------------------

@router.get("/mantenimiento")
def maintenance(
    propiedad_id: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    return _items(db, OrdenMantenimiento, _property_scope(db, user, propiedad_id), limit)


@router.post("/mantenimiento", status_code=201)
def create_maintenance(body: MantenimientoCreate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    item = OrdenMantenimiento(
        **body.model_dump(exclude={"propiedad_id"}),
        propiedad_id=_require_property(db, user, body.propiedad_id),
        creado_por=user.username,
    )
    db.add(item)
    _audit(db, user, "ops_mantenimiento_crear", body.titulo)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/mantenimiento/{item_id}")
def update_maintenance(item_id: int, body: EstadoUpdate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    if body.estado not in TASK_STATES:
        raise HTTPException(422, "Estado de mantenimiento inválido.")
    item = db.get(OrdenMantenimiento, item_id)
    if not item:
        raise HTTPException(404, "Orden no encontrada.")
    assert_property_access(db, user, item.propiedad_id)
    item.estado = body.estado
    item.cerrado_en = datetime.now(UTC) if body.estado in {"completada", "cancelada"} else None
    _audit(db, user, "ops_mantenimiento_estado", f"{item.id}:{body.estado}")
    db.commit()
    db.refresh(item)
    return item


# ------------------------------------------------------------
# Inventario
# ------------------------------------------------------------

@router.get("/inventario")
def inventory(
    propiedad_id: int | None = Query(None, ge=1),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    return _items(db, ItemInventario, _property_scope(db, user, propiedad_id), limit)


@router.post("/inventario", status_code=201)
def create_inventory(body: InventarioCreate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    item = ItemInventario(
        **body.model_dump(exclude={"propiedad_id"}),
        propiedad_id=_require_property(db, user, body.propiedad_id),
        actualizado_por=user.username,
    )
    db.add(item)
    _audit(db, user, "ops_inventario_crear", body.nombre)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/inventario/{item_id}")
def update_inventory(item_id: int, body: StockUpdate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    item = db.get(ItemInventario, item_id)
    if not item:
        raise HTTPException(404, "Ítem no encontrado.")
    assert_property_access(db, user, item.propiedad_id)
    item.cantidad = body.cantidad
    item.actualizado_por = user.username
    _audit(db, user, "ops_inventario_ajuste", f"{item.id}:{body.cantidad:g} {item.unidad}")
    db.commit()
    db.refresh(item)
    return item


# ------------------------------------------------------------
# Objetos perdidos
# ------------------------------------------------------------

@router.get("/objetos-perdidos")
def lost_found(
    propiedad_id: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    return _items(db, ObjetoPerdido, _property_scope(db, user, propiedad_id), limit)


@router.post("/objetos-perdidos", status_code=201)
def create_lost_found(body: ObjetoPerdidoCreate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    item = ObjetoPerdido(
        **body.model_dump(exclude={"propiedad_id"}),
        propiedad_id=_require_property(db, user, body.propiedad_id),
        registrado_por=user.username,
    )
    db.add(item)
    # La descripción es PII: no se duplica en el registro de auditoría.
    _audit(db, user, "ops_objeto_registrar", f"Objeto #{item.id}")
    db.commit()
    db.refresh(item)
    return item


@router.patch("/objetos-perdidos/{item_id}")
def update_lost_found(item_id: int, body: EstadoUpdate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    if body.estado not in LOST_STATES:
        raise HTTPException(422, "Estado de objeto perdido inválido.")
    item = db.get(ObjetoPerdido, item_id)
    if not item:
        raise HTTPException(404, "Registro no encontrado.")
    assert_property_access(db, user, item.propiedad_id)
    item.estado = body.estado
    _audit(db, user, "ops_objeto_estado", f"{item.id}:{body.estado}")
    db.commit()
    db.refresh(item)
    return item


# ------------------------------------------------------------
# Checklists
# ------------------------------------------------------------

@router.get("/checklists")
def checklists(
    propiedad_id: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    user: Usuario = Depends(get_current_user),
):
    return [_checklist(row) for row in _items(db, EjecucionChecklist, _property_scope(db, user, propiedad_id), limit)]


@router.post("/checklists", status_code=201)
def create_checklist(body: ChecklistCreate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    if any(not x.strip() or len(x) > 180 for x in body.elementos):
        raise HTTPException(422, "Cada punto de control debe tener entre 1 y 180 caracteres.")
    item = EjecucionChecklist(
        nombre=body.nombre,
        area=body.area,
        elementos=json.dumps([x.strip() for x in body.elementos], ensure_ascii=False),
        propiedad_id=_require_property(db, user, body.propiedad_id),
        ejecutado_por=user.username,
    )
    db.add(item)
    _audit(db, user, "ops_checklist_crear", body.nombre)
    db.commit()
    db.refresh(item)
    return _checklist(item)


@router.patch("/checklists/{item_id}")
def update_checklist(item_id: int, body: EstadoUpdate, db: Session = Depends(get_db), user: Usuario = Depends(require_editor)):
    if body.estado not in CHECKLIST_STATES:
        raise HTTPException(422, "Estado de checklist inválido.")
    item = db.get(EjecucionChecklist, item_id)
    if not item:
        raise HTTPException(404, "Checklist no encontrado.")
    assert_property_access(db, user, item.propiedad_id)
    item.estado = body.estado
    item.completado_en = datetime.now(UTC) if body.estado == "completada" else None
    _audit(db, user, "ops_checklist_estado", f"{item.id}:{body.estado}")
    db.commit()
    db.refresh(item)
    return _checklist(item)
