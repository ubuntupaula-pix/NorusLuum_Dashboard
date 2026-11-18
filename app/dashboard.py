"""Commercial and operational KPI queries with period/property scoping.

Missing source fields remain null. The dashboard never invents capacity,
occupancy or revenue: when a source does not provide the required fact the
corresponding value is served as ``null`` and the UI renders ``—``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from .models import (
    HechoReserva, HechoMetricaMensual, HechoMetricaDiaria, HechoIncidencia,
    DimFecha, DimCanal, DimNacionalidad, DimMarca, DimSegmento, DimTipoHabitacion,
)

COLORS = ["#1e3a4a", "#4f7466", "#a98b5f", "#6d8ea3", "#7c6a8a", "#a9584d"]
MONTHS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
WEEKDAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
GRANULARIDADES = {"dia", "semana", "mes", "anio"}
CANCELLED = ("cancelada", "cancelado", "cancelled", "canceled", "anulada", "anulado")


def _num(value, digits=1):
    return round(float(value), digits) if value is not None else None


def _rate(income, nights, available, occ_sum, occ_n, adr_sum, adr_n, revpar_sum, revpar_n):
    occ = None
    adr = None
    revpar = None
    if nights is not None and available is not None and available > 0:
        occ = nights / available * 100
    elif occ_n:
        occ = occ_sum / occ_n
    if income is not None and nights is not None and nights > 0:
        adr = income / nights
    elif adr_n:
        adr = adr_sum / adr_n
    if income is not None and available is not None and available > 0:
        revpar = income / available
    elif revpar_n:
        revpar = revpar_sum / revpar_n
    return occ, adr, revpar


def _channel_bucket(name: str) -> str:
    value = (name or "").casefold()
    if "direct" in value or "web propia" in value:
        return "directo"
    if any(x in value for x in ("ota", "online", "booking", "expedia", "airbnb")):
        return "ota"
    if any(x in value for x in ("gds", "agencia", "agency", "tour operador")):
        return "gds"
    return "otros"


def _empty_dashboard(moneda: str, granularidad: str, desde, hasta, propiedad_id):
    empty_kpi = {key: {"valor": None, "delta": None, "sub": ""} for key in (
        "ocupacion", "adr", "revpar", "ingresos", "noches", "nps", "incidencias", "satisfaccion"
    )}
    return {
        "moneda": moneda, "granularidad": granularidad, "desde": desde, "hasta": hasta,
        "propiedad_id": propiedad_id, "reservas_total": 0,
        "kpi_reservas": {"anticipacion_media": None, "mercados": 0, "reservas_anticipadas_pct": None,
                         "canceladas": 0, "cancelacion_pct": None},
        "meses": [], "ocupacion_mensual": [], "adr_mensual": [], "revpar_mensual": [],
        "ingresos_mensuales": [],
        "canales": {"labels": [], "valores": [], "ingresos": [], "anticipacion": []},
        "nacionalidad": {"labels": [], "valores": []},
        "segmentos": {"labels": [], "adr": [], "ocupacion": []},
        "anticipacion": {"labels": ["0–7", "8–30", "31–90", "91–180", ">180"], "valores": [0, 0, 0, 0, 0]},
        "incidencias": {"tipos": [], "valores": [], "meses": [], "serie": [],
                        "resolucion": {"labels": ["<2 h", "2–6 h", "6–12 h", "12–24 h", ">24 h"],
                                       "horas": [0, 0, 0, 0, 0]},
                        "tiempo_resolucion_promedio": None},
        "satisfaccion": {"areas": [], "nota": [], "encuestas": {"labels": [], "satisfecho": [], "neutral": [], "insatisfecho": []}},
        "habitaciones": {"labels": [], "ingresos": []},
        "kpi": {"anio": empty_kpi, "impor": empty_kpi},
        "reservas": [], "marcas": [], "ocupacion_marca": [], "adr_marca": [], "ingresos_marca": [],
        "marca_colores": [], "kpi_marca": [],
        "temporadas": {"meses": [], "series": {}},
        "revenue": {"labels": [], "adr": [], "ocupacion": []},
        "nacionalidad_marca": {"labels": [], "series": {}},
        "nps": {"score": None, "promotores": None, "neutros": None, "detractores": None, "fill": 0},
        "dia_semana": {"labels": [], "valores": []},
        "aportacion_canal": {"directo": None, "ota": None, "gds": None, "otros": None},
    }


def _scope_list(column, values):
    if values is None:
        return []
    if not values:
        return [column == -1]
    return [column.in_(values)]


def _month_key(year, month):
    return int(year) * 100 + int(month)


def _bucket_key_from_date(fecha: str, granularidad: str):
    if granularidad == "dia":
        return fecha
    if granularidad == "semana":
        try:
            iso = date.fromisoformat(fecha).isocalendar()
        except ValueError:
            return fecha
        return (iso.year, iso.week)
    if granularidad == "anio":
        return (int(fecha[:4]),)
    try:
        return (int(fecha[:4]), int(fecha[5:7]))
    except ValueError:
        return fecha


def _bucket_key_from_month(year, month, granularidad: str):
    if granularidad == "anio":
        return (int(year),)
    return (int(year), int(month))


def _bucket_label(key):
    if isinstance(key, tuple):
        if len(key) == 1:
            return str(key[0])
        if len(key) == 2 and key[1] <= 12 and key[0] > 1000:
            return f"{MONTHS[key[1] - 1]} {key[0]}"
    if isinstance(key, tuple) and len(key) == 2:
        return f"{key[0]}-S{key[1]:02d}"
    return str(key)


def _bucket_label_week(key):
    if isinstance(key, tuple) and len(key) == 2 and 1 <= key[1] <= 53 and key[0] > 1000:
        return f"{key[0]}-S{key[1]:02d}"
    return _bucket_label(key)


def _agg():
    return {
        "income": 0.0, "income_n": 0, "nights": 0.0, "nights_n": 0,
        "available": 0.0, "available_n": 0, "occ_sum": 0.0, "occ_n": 0,
        "adr_sum": 0.0, "adr_n": 0, "revpar_sum": 0.0, "revpar_n": 0,
    }


def _add(agg, income=None, nights=None, available=None, occ=None, adr=None, revpar=None):
    if income is not None:
        agg["income"] += float(income); agg["income_n"] += 1
    if nights is not None:
        agg["nights"] += float(nights); agg["nights_n"] += 1
    if available is not None:
        agg["available"] += float(available); agg["available_n"] += 1
    if occ is not None:
        agg["occ_sum"] += float(occ); agg["occ_n"] += 1
    if adr is not None:
        agg["adr_sum"] += float(adr); agg["adr_n"] += 1
    if revpar is not None:
        agg["revpar_sum"] += float(revpar); agg["revpar_n"] += 1


def _finalize(agg):
    income = agg["income"] if agg["income_n"] else None
    nights = agg["nights"] if agg["nights_n"] else None
    available = agg["available"] if agg["available_n"] else None
    return _rate(income, nights, available, agg["occ_sum"], agg["occ_n"],
                 agg["adr_sum"], agg["adr_n"], agg["revpar_sum"], agg["revpar_n"])


def obtener_dashboard(
    db: Session,
    moneda: str = "CLP",
    desde: str | None = None,
    hasta: str | None = None,
    granularidad: str = "mes",
    propiedad_id: int | None = None,
    allowed_property_ids: list[int] | None = None,
) -> dict:
    granularidad = granularidad if granularidad in GRANULARIDADES else "mes"
    if propiedad_id is not None:
        property_ids = [int(propiedad_id)]
    else:
        property_ids = allowed_property_ids
    if property_ids is not None and len(property_ids) == 0:
        return _empty_dashboard(moneda, granularidad, desde, hasta, propiedad_id)

    cancelled_lower = tuple(x.casefold() for x in CANCELLED)

    # Subconsulta de fechas para aplicar el rango a hechos con fecha_id.
    date_subq = None
    if desde or hasta:
        query = db.query(DimFecha.id)
        if desde:
            query = query.filter(DimFecha.fecha >= desde)
        if hasta:
            query = query.filter(DimFecha.fecha <= hasta)
        date_subq = query.subquery()
        date_ids = query

    def reserva_scope(include_status: bool = True):
        filters = [HechoReserva.moneda == moneda]
        filters.extend(_scope_list(HechoReserva.marca_id, property_ids))
        if date_subq is not None:
            filters.append(HechoReserva.fecha_id.in_(date_ids))
        if include_status:
            filters.append(func.lower(func.coalesce(HechoReserva.estado, "")).notin_(cancelled_lower))
        return filters

    def metric_month_scope():
        filters = [HechoMetricaMensual.moneda == moneda]
        filters.extend(_scope_list(HechoMetricaMensual.marca_id, property_ids))
        if desde:
            filters.append((HechoMetricaMensual.anio * 100 + HechoMetricaMensual.mes) >= _month_key(desde[:4], desde[5:7]))
        if hasta:
            filters.append((HechoMetricaMensual.anio * 100 + HechoMetricaMensual.mes) <= _month_key(hasta[:4], hasta[5:7]))
        return filters

    def metric_day_scope():
        filters = [HechoMetricaDiaria.moneda == moneda]
        filters.extend(_scope_list(HechoMetricaDiaria.marca_id, property_ids))
        if desde:
            filters.append(HechoMetricaDiaria.fecha >= desde)
        if hasta:
            filters.append(HechoMetricaDiaria.fecha <= hasta)
        return filters

    def incident_scope():
        filters = []
        filters.extend(_scope_list(HechoIncidencia.marca_id, property_ids))
        if date_subq is not None:
            filters.append(HechoIncidencia.fecha_id.in_(date_ids))
        return filters

    # --------------------------------------------------------
    # Series temporales según granularidad
    # --------------------------------------------------------
    bucket_order: list = []
    bucket_data: dict = {}

    if granularidad in ("dia", "semana"):
        day_rows = db.query(
            HechoMetricaDiaria.fecha,
            func.sum(HechoMetricaDiaria.ingresos),
            func.sum(HechoMetricaDiaria.noches_vendidas),
            func.sum(HechoMetricaDiaria.habitaciones_disponibles),
            func.sum(case((HechoMetricaDiaria.ocupacion.isnot(None), HechoMetricaDiaria.ocupacion), else_=0.0)),
            func.count(HechoMetricaDiaria.ocupacion),
            func.sum(case((HechoMetricaDiaria.adr.isnot(None), HechoMetricaDiaria.adr), else_=0.0)),
            func.count(HechoMetricaDiaria.adr),
            func.sum(case((HechoMetricaDiaria.revpar.isnot(None), HechoMetricaDiaria.revpar), else_=0.0)),
            func.count(HechoMetricaDiaria.revpar),
        ).filter(*metric_day_scope()).group_by(HechoMetricaDiaria.fecha).order_by(HechoMetricaDiaria.fecha).all()
        for fecha, income, nights, available, occ_sum, occ_n, adr_sum, adr_n, revpar_sum, revpar_n in day_rows:
            key = _bucket_key_from_date(str(fecha), granularidad)
            if key not in bucket_data:
                bucket_data[key] = _agg()
                bucket_order.append(key)
            agg = bucket_data[key]
            if income is not None:
                agg["income"] += float(income); agg["income_n"] += 1
            if nights is not None:
                agg["nights"] += float(nights); agg["nights_n"] += 1
            if available is not None:
                agg["available"] += float(available); agg["available_n"] += 1
            agg["occ_sum"] += float(occ_sum or 0); agg["occ_n"] += int(occ_n or 0)
            agg["adr_sum"] += float(adr_sum or 0); agg["adr_n"] += int(adr_n or 0)
            agg["revpar_sum"] += float(revpar_sum or 0); agg["revpar_n"] += int(revpar_n or 0)
    else:
        group_cols = [HechoMetricaMensual.anio]
        if granularidad == "mes":
            group_cols.append(HechoMetricaMensual.mes)
        month_rows = db.query(
            *group_cols,
            func.sum(HechoMetricaMensual.ingresos),
            func.sum(HechoMetricaMensual.noches_vendidas),
            func.sum(HechoMetricaMensual.habitaciones_disponibles),
            func.sum(case((HechoMetricaMensual.ocupacion.isnot(None), HechoMetricaMensual.ocupacion), else_=0.0)),
            func.count(HechoMetricaMensual.ocupacion),
            func.sum(case((HechoMetricaMensual.adr.isnot(None), HechoMetricaMensual.adr), else_=0.0)),
            func.count(HechoMetricaMensual.adr),
            func.sum(case((HechoMetricaMensual.revpar.isnot(None), HechoMetricaMensual.revpar), else_=0.0)),
            func.count(HechoMetricaMensual.revpar),
        ).filter(*metric_month_scope()).group_by(*group_cols).order_by(*group_cols).all()
        for row in month_rows:
            if granularidad == "mes":
                year, month = row[0], row[1]
                values = row[2:]
                key = (int(year), int(month))
            else:
                year = row[0]
                values = row[1:]
                key = (int(year),)
            bucket_order.append(key)
            agg = _agg()
            income, nights, available, occ_sum, occ_n, adr_sum, adr_n, revpar_sum, revpar_n = values
            if income is not None:
                agg["income"] += float(income); agg["income_n"] += 1
            if nights is not None:
                agg["nights"] += float(nights); agg["nights_n"] += 1
            if available is not None:
                agg["available"] += float(available); agg["available_n"] += 1
            agg["occ_sum"] += float(occ_sum or 0); agg["occ_n"] += int(occ_n or 0)
            agg["adr_sum"] += float(adr_sum or 0); agg["adr_n"] += int(adr_n or 0)
            agg["revpar_sum"] += float(revpar_sum or 0); agg["revpar_n"] += int(revpar_n or 0)
            bucket_data[key] = agg

    if granularidad == "semana":
        labels = [_bucket_label_week(key) for key in bucket_order]
    else:
        labels = [_bucket_label(key) for key in bucket_order]
    occupancy_series, adr_series, revpar_series, income_series = [], [], [], []
    for key in bucket_order:
        agg = bucket_data[key]
        occ, adr, revpar = _finalize(agg)
        occupancy_series.append(_num(occ))
        adr_series.append(_num(adr, 2))
        revpar_series.append(_num(revpar, 2))
        income_series.append(_num(agg["income"], 2) if agg["income_n"] else None)

    # --------------------------------------------------------
    # Totales del período para KPI
    # --------------------------------------------------------
    period_agg = _agg()
    if granularidad in ("dia", "semana"):
        day_totals = db.query(
            func.sum(HechoMetricaDiaria.ingresos),
            func.sum(HechoMetricaDiaria.noches_vendidas),
            func.sum(HechoMetricaDiaria.habitaciones_disponibles),
            func.sum(case((HechoMetricaDiaria.ocupacion.isnot(None), HechoMetricaDiaria.ocupacion), else_=0.0)),
            func.count(HechoMetricaDiaria.ocupacion),
            func.sum(case((HechoMetricaDiaria.adr.isnot(None), HechoMetricaDiaria.adr), else_=0.0)),
            func.count(HechoMetricaDiaria.adr),
            func.sum(case((HechoMetricaDiaria.revpar.isnot(None), HechoMetricaDiaria.revpar), else_=0.0)),
            func.count(HechoMetricaDiaria.revpar),
        ).filter(*metric_day_scope()).one()
        if day_totals and any(value is not None for value in day_totals):
            _add(period_agg, day_totals[0], day_totals[1], day_totals[2])
            period_agg["occ_sum"] += float(day_totals[3] or 0); period_agg["occ_n"] += int(day_totals[4] or 0)
            period_agg["adr_sum"] += float(day_totals[5] or 0); period_agg["adr_n"] += int(day_totals[6] or 0)
            period_agg["revpar_sum"] += float(day_totals[7] or 0); period_agg["revpar_n"] += int(day_totals[8] or 0)
    if not period_agg["income_n"] and not period_agg["nights_n"] and not period_agg["available_n"]:
        month_totals = db.query(
            func.sum(HechoMetricaMensual.ingresos),
            func.sum(HechoMetricaMensual.noches_vendidas),
            func.sum(HechoMetricaMensual.habitaciones_disponibles),
            func.sum(case((HechoMetricaMensual.ocupacion.isnot(None), HechoMetricaMensual.ocupacion), else_=0.0)),
            func.count(HechoMetricaMensual.ocupacion),
            func.sum(case((HechoMetricaMensual.adr.isnot(None), HechoMetricaMensual.adr), else_=0.0)),
            func.count(HechoMetricaMensual.adr),
            func.sum(case((HechoMetricaMensual.revpar.isnot(None), HechoMetricaMensual.revpar), else_=0.0)),
            func.count(HechoMetricaMensual.revpar),
        ).filter(*metric_month_scope()).one()
        if month_totals and any(value is not None for value in month_totals):
            _add(period_agg, month_totals[0], month_totals[1], month_totals[2])
            period_agg["occ_sum"] += float(month_totals[3] or 0); period_agg["occ_n"] += int(month_totals[4] or 0)
            period_agg["adr_sum"] += float(month_totals[5] or 0); period_agg["adr_n"] += int(month_totals[6] or 0)
            period_agg["revpar_sum"] += float(month_totals[7] or 0); period_agg["revpar_n"] += int(month_totals[8] or 0)
    occ, adr, revpar = _finalize(period_agg)
    revenue = period_agg["income"] if period_agg["income_n"] else None
    sold = period_agg["nights"] if period_agg["nights_n"] else None

    # --------------------------------------------------------
    # Reservas: canales, mercados, segmentos, anticipación
    # --------------------------------------------------------
    base_res = reserva_scope()
    channel_rows = db.query(
        func.coalesce(DimCanal.nombre, "Sin clasificar"), func.count(HechoReserva.id),
        func.sum(HechoReserva.valor), func.avg(HechoReserva.anticipacion_dias),
    ).outerjoin(DimCanal, HechoReserva.canal_id == DimCanal.id)\
        .filter(*base_res).group_by(DimCanal.nombre).order_by(func.sum(HechoReserva.valor).desc()).all()
    channel_labels = [r[0] for r in channel_rows]
    channel_counts = [int(r[1] or 0) for r in channel_rows]
    channel_revenue = [_num(r[2], 2) or 0 for r in channel_rows]
    channel_lead = [_num(r[3], 1) for r in channel_rows]

    nationality_rows = db.query(
        func.coalesce(DimNacionalidad.nombre, "Sin clasificar"), func.count(HechoReserva.id),
    ).outerjoin(DimNacionalidad, HechoReserva.nacionalidad_id == DimNacionalidad.id)\
        .filter(*base_res).group_by(DimNacionalidad.nombre)\
        .order_by(func.count(HechoReserva.id).desc()).limit(10).all()
    nationality_labels = [r[0] for r in nationality_rows]
    nationality_counts = [int(r[1] or 0) for r in nationality_rows]

    segment_rows = db.query(
        func.coalesce(DimSegmento.nombre, "Sin clasificar"), func.sum(HechoReserva.valor),
        func.sum(HechoReserva.noches),
    ).outerjoin(DimSegmento, HechoReserva.segmento_id == DimSegmento.id)\
        .filter(*base_res).group_by(DimSegmento.nombre)\
        .order_by(func.sum(HechoReserva.valor).desc()).all()
    segment_labels = [r[0] for r in segment_rows]
    segment_adr = [_num(float(r[1] or 0) / float(r[2]), 2) if r[2] and float(r[2]) > 0 else None for r in segment_rows]

    room_rows = db.query(
        func.coalesce(DimTipoHabitacion.nombre, "Sin clasificar"), func.sum(HechoReserva.valor),
    ).outerjoin(DimTipoHabitacion, HechoReserva.tipo_habitacion_id == DimTipoHabitacion.id)\
        .filter(*base_res).group_by(DimTipoHabitacion.nombre)\
        .order_by(func.sum(HechoReserva.valor).desc()).all()

    reservation_total = db.query(func.count(HechoReserva.id)).filter(*reserva_scope(False)).scalar() or 0
    cancelled_total = db.query(func.count(HechoReserva.id)).filter(
        *reserva_scope(False), func.lower(func.coalesce(HechoReserva.estado, "")).in_(cancelled_lower)
    ).scalar() or 0
    valid_lead = base_res + [HechoReserva.anticipacion_dias.isnot(None)]
    lead_avg = db.query(func.avg(HechoReserva.anticipacion_dias)).filter(*valid_lead).scalar()
    lead_total = db.query(func.count(HechoReserva.id)).filter(*valid_lead).scalar() or 0
    early_total = db.query(func.count(HechoReserva.id)).filter(*valid_lead, HechoReserva.anticipacion_dias >= 90).scalar() or 0
    market_total = db.query(func.count(func.distinct(DimNacionalidad.id))).join(
        HechoReserva, HechoReserva.nacionalidad_id == DimNacionalidad.id
    ).filter(*base_res).scalar() or 0
    anticipacion_labels = ["0–7", "8–30", "31–90", "91–180", ">180"]
    anticipacion_values = [
        db.query(func.count(HechoReserva.id)).filter(
            *valid_lead, HechoReserva.anticipacion_dias >= lo, HechoReserva.anticipacion_dias < hi
        ).scalar() or 0
        for lo, hi in [(0, 8), (8, 31), (31, 91), (91, 181), (181, 1000000)]
    ]

    weekday_rows = db.query(DimFecha.dia_semana, func.count(HechoReserva.id))\
        .join(HechoReserva, HechoReserva.fecha_id == DimFecha.id)\
        .filter(*base_res).group_by(DimFecha.dia_semana).all()
    weekday_map = {int(day): int(count) for day, count in weekday_rows if day is not None}

    seasonal_rows = db.query(
        DimFecha.mes, func.coalesce(DimSegmento.nombre, "Sin clasificar"), func.count(HechoReserva.id)
    ).join(HechoReserva, HechoReserva.fecha_id == DimFecha.id)\
        .outerjoin(DimSegmento, HechoReserva.segmento_id == DimSegmento.id)\
        .filter(*base_res).group_by(DimFecha.mes, DimSegmento.nombre).all()
    seasonal_months = sorted({int(month) for month, _, _ in seasonal_rows if month is not None})
    seasonal_segments = sorted({segment for _, segment, _ in seasonal_rows if segment})
    seasonal_values = {(int(month), segment): int(count) for month, segment, count in seasonal_rows}
    seasonal_series = {
        segment: [seasonal_values.get((month, segment), 0) for month in seasonal_months]
        for segment in seasonal_segments
    }

    # --------------------------------------------------------
    # Experiencia del huésped
    # --------------------------------------------------------
    inc_scope = incident_scope()
    incidence_rows = db.query(
        func.coalesce(HechoIncidencia.tipo, "General"), func.sum(HechoIncidencia.cantidad),
    ).filter(*inc_scope, HechoIncidencia.cantidad > 0).group_by(HechoIncidencia.tipo)\
        .order_by(func.sum(HechoIncidencia.cantidad).desc()).all()
    satisfaction_rows = db.query(
        func.coalesce(HechoIncidencia.area, "General"), func.avg(HechoIncidencia.satisfaccion),
    ).filter(*inc_scope, HechoIncidencia.satisfaccion.isnot(None)).group_by(HechoIncidencia.area).all()
    resolution_rows = db.query(HechoIncidencia.tiempo_resolucion_h).filter(
        *inc_scope, HechoIncidencia.tiempo_resolucion_h.isnot(None)
    ).all()
    resolution_bins = [("<2 h", 0, 2), ("2–6 h", 2, 6), ("6–12 h", 6, 12), ("12–24 h", 12, 24), (">24 h", 24, float("inf"))]
    resolution_counts = [0] * len(resolution_bins)
    for (hours,) in resolution_rows:
        hours = float(hours)
        for i, (_, low, high) in enumerate(resolution_bins):
            if low <= hours < high:
                resolution_counts[i] += 1
                break
    incidence_count = int(db.query(func.coalesce(func.sum(HechoIncidencia.cantidad), 0)).filter(*inc_scope).scalar() or 0)
    nps_filter = inc_scope + [HechoIncidencia.nps.isnot(None), HechoIncidencia.nps >= 0, HechoIncidencia.nps <= 10]
    incidence_nps_total = db.query(func.count(HechoIncidencia.id)).filter(*nps_filter).scalar() or 0
    promoters = db.query(func.count(HechoIncidencia.id)).filter(*nps_filter, HechoIncidencia.nps >= 9).scalar() or 0
    detractors = db.query(func.count(HechoIncidencia.id)).filter(*nps_filter, HechoIncidencia.nps <= 6).scalar() or 0
    neutrals = incidence_nps_total - promoters - detractors
    nps_score = round((promoters - detractors) * 100 / incidence_nps_total) if incidence_nps_total else None
    satisfaction_avg = db.query(func.avg(HechoIncidencia.satisfaccion)).filter(
        *inc_scope, HechoIncidencia.satisfaccion.isnot(None)
    ).scalar()
    average_resolution = db.query(func.avg(HechoIncidencia.tiempo_resolucion_h)).filter(
        *inc_scope, HechoIncidencia.tiempo_resolucion_h.isnot(None)
    ).scalar()

    # --------------------------------------------------------
    # Tabla de reservas (vista previa acotada)
    # --------------------------------------------------------
    reservation_rows = (db.query(HechoReserva, DimFecha.fecha, DimCanal.nombre, DimNacionalidad.nombre,
                                 DimMarca.nombre, DimSegmento.nombre, DimTipoHabitacion.nombre)
        .outerjoin(DimFecha, HechoReserva.fecha_id == DimFecha.id)
        .outerjoin(DimCanal, HechoReserva.canal_id == DimCanal.id)
        .outerjoin(DimNacionalidad, HechoReserva.nacionalidad_id == DimNacionalidad.id)
        .outerjoin(DimMarca, HechoReserva.marca_id == DimMarca.id)
        .outerjoin(DimSegmento, HechoReserva.segmento_id == DimSegmento.id)
        .outerjoin(DimTipoHabitacion, HechoReserva.tipo_habitacion_id == DimTipoHabitacion.id)
        .filter(*reserva_scope(False)).order_by(HechoReserva.id.desc()).limit(500).all())

    # --------------------------------------------------------
    # Comparativa por propiedad
    # --------------------------------------------------------
    brand_query = db.query(DimMarca)
    if property_ids is not None:
        brand_query = brand_query.filter(DimMarca.id.in_(property_ids)) if property_ids else brand_query.filter(DimMarca.id == -1)
    brand_models = brand_query.order_by(DimMarca.nombre).all()
    brands = [brand.nombre for brand in brand_models]

    brand_buckets: dict[str, dict] = defaultdict(dict)
    if granularidad in ("dia", "semana"):
        brand_rows = db.query(
            HechoMetricaDiaria.marca_id, HechoMetricaDiaria.fecha,
            HechoMetricaDiaria.ingresos, HechoMetricaDiaria.noches_vendidas,
            HechoMetricaDiaria.habitaciones_disponibles, HechoMetricaDiaria.ocupacion,
            HechoMetricaDiaria.adr, HechoMetricaDiaria.revpar,
        ).filter(*metric_day_scope()).all()
        for marca_id, fecha, income, nights, available, occ, adr_value, revpar_value in brand_rows:
            key = _bucket_key_from_date(str(fecha), granularidad)
            brand_buckets[int(marca_id or 0)].setdefault(key, _agg())
            _add(brand_buckets[int(marca_id or 0)][key], income, nights, available, occ, adr_value, revpar_value)
    else:
        brand_rows = db.query(
            HechoMetricaMensual.marca_id, HechoMetricaMensual.anio, HechoMetricaMensual.mes,
            HechoMetricaMensual.ingresos, HechoMetricaMensual.noches_vendidas,
            HechoMetricaMensual.habitaciones_disponibles, HechoMetricaMensual.ocupacion,
            HechoMetricaMensual.adr, HechoMetricaMensual.revpar,
        ).filter(*metric_month_scope()).all()
        for marca_id, anio, mes, income, nights, available, occ, adr_value, revpar_value in brand_rows:
            key = _bucket_key_from_month(anio, mes, granularidad)
            brand_buckets[int(marca_id or 0)].setdefault(key, _agg())
            _add(brand_buckets[int(marca_id or 0)][key], income, nights, available, occ, adr_value, revpar_value)
    brand_by_id = {brand.id: brand for brand in brand_models}
    brand_occupancy, brand_adr, brand_income, brand_kpis = [], [], [], []
    for brand in brand_models:
        data = brand_buckets.get(brand.id, {})
        occ_values, adr_values, income_values = [], [], []
        for key in bucket_order:
            agg = data.get(key)
            if agg is None:
                occ_values.append(None); adr_values.append(None); income_values.append(None)
            else:
                occ, adr_value, _ = _finalize(agg)
                occ_values.append(_num(occ)); adr_values.append(_num(adr_value, 2))
                income_values.append(_num(agg["income"], 2) if agg["income_n"] else None)
        brand_occupancy.append(occ_values)
        brand_adr.append(adr_values)
        brand_income.append(income_values)

        totals = _agg()
        for agg in data.values():
            for field in ("income", "income_n", "nights", "nights_n", "available", "available_n",
                          "occ_sum", "occ_n", "adr_sum", "adr_n", "revpar_sum", "revpar_n"):
                totals[field] += agg[field]
        occ, adr_value, _ = _finalize(totals)
        brand_kpis.append({
            "nombre": brand.nombre,
            "habitaciones": int(brand.habitaciones) if brand.habitaciones and int(brand.habitaciones) > 0 else None,
            "ocupacion": _num(occ), "adr": _num(adr_value, 2),
            "ingresos": _num(totals["income"], 2) if totals["income_n"] else None,
            "deltas": {},
        })

    nationality_brand_rows = (db.query(DimNacionalidad.nombre, DimMarca.nombre, func.count(HechoReserva.id))
        .join(HechoReserva, HechoReserva.nacionalidad_id == DimNacionalidad.id)
        .outerjoin(DimMarca, HechoReserva.marca_id == DimMarca.id)
        .filter(*base_res).group_by(DimNacionalidad.nombre, DimMarca.nombre).all())
    nationality_brand = defaultdict(dict)
    for nationality, brand, count in nationality_brand_rows:
        nationality_brand[brand or "Sin clasificar"][nationality] = int(count)
    brand_market_series = {brand: [nationality_brand[brand].get(n, 0) for n in nationality_labels] for brand in brands}

    channel_shares = {"directo": 0.0, "ota": 0.0, "gds": 0.0, "otros": 0.0}
    channel_total = sum(channel_revenue)
    if channel_total > 0:
        for label, amount in zip(channel_labels, channel_revenue):
            channel_shares[_channel_bucket(label)] += amount / channel_total * 100
        channel_shares = {key: round(value, 1) for key, value in channel_shares.items()}
    else:
        channel_shares = {key: None for key in channel_shares}

    incidents_monthly = db.query(DimFecha.anio, DimFecha.mes, func.sum(HechoIncidencia.cantidad))\
        .join(HechoIncidencia, HechoIncidencia.fecha_id == DimFecha.id)\
        .filter(*(inc_scope + [HechoIncidencia.cantidad > 0]) if inc_scope else [HechoIncidencia.cantidad > 0])\
        .group_by(DimFecha.anio, DimFecha.mes).order_by(DimFecha.anio, DimFecha.mes).all()
    incidence_month_labels = [f"{MONTHS[m - 1]} {y}" for y, m, _ in incidents_monthly]

    kpi = {
        "ocupacion": {"valor": _num(occ), "delta": None, "sub": "noches vendidas / capacidad disponible"},
        "adr": {"valor": _num(adr, 2), "delta": None, "sub": f"ingreso por noche vendida · {moneda}"},
        "revpar": {"valor": _num(revpar, 2), "delta": None, "sub": f"ingreso por noche disponible · {moneda}"},
        "ingresos": {"valor": _num(revenue, 2), "delta": None, "sub": f"valores de origen en {moneda}; sin conversión"},
        "noches": {"valor": _num(sold, 1), "delta": None, "sub": "noches habitación reportadas"},
        "nps": {"valor": nps_score, "delta": None, "sub": "promotores menos detractores · escala 0–10"},
        "incidencias": {"valor": incidence_count, "delta": None, "sub": "cantidad informada en los registros"},
        "satisfaccion": {"valor": _num(satisfaction_avg, 2), "delta": None, "sub": "promedio observado · escala de origen"},
    }

    return {
        "moneda": moneda, "granularidad": granularidad, "desde": desde, "hasta": hasta,
        "propiedad_id": propiedad_id,
        "reservas_total": int(reservation_total),
        "kpi_reservas": {"anticipacion_media": _num(lead_avg), "mercados": int(market_total),
                         "reservas_anticipadas_pct": round(early_total * 100 / lead_total, 1) if lead_total else None,
                         "canceladas": int(cancelled_total),
                         "cancelacion_pct": round(cancelled_total * 100 / reservation_total, 1) if reservation_total else None},
        "meses": labels, "ocupacion_mensual": occupancy_series, "adr_mensual": adr_series,
        "revpar_mensual": revpar_series, "ingresos_mensuales": income_series,
        "canales": {"labels": channel_labels, "valores": channel_counts, "ingresos": channel_revenue, "anticipacion": channel_lead},
        "nacionalidad": {"labels": nationality_labels, "valores": nationality_counts},
        "segmentos": {"labels": segment_labels, "adr": segment_adr, "ocupacion": [None] * len(segment_labels)},
        "anticipacion": {"labels": anticipacion_labels, "valores": anticipacion_values},
        "incidencias": {"tipos": [r[0] for r in incidence_rows], "valores": [float(r[1] or 0) for r in incidence_rows],
                        "meses": incidence_month_labels, "serie": [float(r[2] or 0) for r in incidents_monthly],
                        "resolucion": {"labels": [x[0] for x in resolution_bins], "horas": resolution_counts},
                        "tiempo_resolucion_promedio": _num(average_resolution, 2)},
        "satisfaccion": {"areas": [r[0] for r in satisfaction_rows], "nota": [_num(r[1], 2) for r in satisfaction_rows],
                         "encuestas": {"labels": [], "satisfecho": [], "neutral": [], "insatisfecho": []}},
        "habitaciones": {"labels": [r[0] for r in room_rows], "ingresos": [_num(r[1], 2) or 0 for r in room_rows]},
        "kpi": {"anio": kpi, "impor": kpi},
        "reservas": [{"id": r[0].id, "codigo": r[0].codigo, "huesped": r[0].huesped or "",
                      "estado": r[0].estado or "", "valor": float(r[0].valor or 0), "moneda": r[0].moneda,
                      "noches": float(r[0].noches or 0), "anticipacion_dias": float(r[0].anticipacion_dias or 0),
                      "fecha": r[1], "canal": r[2], "nacionalidad": r[3], "marca": r[4],
                      "propiedad_id": r[0].marca_id, "segmento": r[5], "tipo_habitacion": r[6]} for r in reservation_rows],
        "marcas": brands, "ocupacion_marca": brand_occupancy, "adr_marca": brand_adr, "ingresos_marca": brand_income,
        "marca_colores": [COLORS[i % len(COLORS)] for i in range(len(brands))], "kpi_marca": brand_kpis,
        "temporadas": {"meses": [MONTHS[m - 1] for m in seasonal_months], "series": seasonal_series},
        "revenue": {"labels": labels, "adr": adr_series, "ocupacion": occupancy_series},
        "nacionalidad_marca": {"labels": nationality_labels, "series": brand_market_series},
        "nps": {"score": nps_score,
                "promotores": round(promoters * 100 / incidence_nps_total) if incidence_nps_total else None,
                "neutros": round(neutrals * 100 / incidence_nps_total) if incidence_nps_total else None,
                "detractores": round(detractors * 100 / incidence_nps_total) if incidence_nps_total else None,
                "fill": max(0, nps_score or 0)},
        "dia_semana": {"labels": [WEEKDAYS[i] for i in range(7) if i in weekday_map],
                       "valores": [weekday_map[i] for i in range(7) if i in weekday_map]},
        "aportacion_canal": channel_shares,
    }
