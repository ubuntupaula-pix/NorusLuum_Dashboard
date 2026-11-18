# -*- coding: utf-8 -*-
"""============================================================
   NORUS & LUUM · Pipeline ETL (Extract, Transform, Load)
   Procesa archivos subidos y los carga en el data warehouse
   con validación, normalización y auditoría.
   ============================================================"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from .models import (
    CargaArchivo, DimFecha, DimCanal, DimNacionalidad, DimMarca,
    DimSegmento, DimTipoHabitacion, HechoReserva, HechoMetricaMensual,
    HechoMetricaDiaria, HechoIncidencia, EventoAuditoria,
)


# ============================================================
# CATÁLOGO DE CAMPOS ESPERADOS (mismo que el frontend)
# ============================================================

CAMPOS_DASHBOARD = [
    {"key": "fecha",        "pattern": r"fecha|date|periodo|period|check.*in|arrival"},
    {"key": "mes",          "pattern": r"mes|month"},
    {"key": "anio",         "pattern": r"año|anio|year|yr"},
    {"key": "ocupacion",    "pattern": r"ocupacion|occup|occupancy|occ%"},
    {"key": "adr",          "pattern": r"adr|tarifa.*prom|avg.*rate|average.*rate"},
    {"key": "revpar",       "pattern": r"revpar"},
    {"key": "ingresos",     "pattern": r"ingreso|revenue|venta|sales|income"},
    {"key": "noches",       "pattern": r"noches|rooms.*sold|roomnights|nights"},
    {"key": "habitaciones_disp", "pattern": r"disponibl|available"},
    {"key": "canal",        "pattern": r"canal|channel|ota|distribuc|booking.*source"},
    {"key": "nacionalidad", "pattern": r"nacionalid|country|nationality|mercado|market"},
    {"key": "segmento",     "pattern": r"segmento|segment"},
    {"key": "anticipacion", "pattern": r"anticipac|lead.*time"},
    {"key": "incidencias",  "pattern": r"incidencia|complaint|issue|cant.*incid"},
    {"key": "tipo_incidencia", "pattern": r"tipo.*incid|issue.*type|complaint.*type"},
    {"key": "area",         "pattern": r"area|department|service.*area"},
    {"key": "tiempo_resol", "pattern": r"resoluc|resolut"},
    {"key": "satisfaccion", "pattern": r"satisfac|satisf|csat"},
    {"key": "nps",          "pattern": r"nps"},
    {"key": "tipo_habitacion", "pattern": r"tipo.*habitaci[oó]n|habitaci[oó]n.*tipo|room.*type|tipo.*cuarto"},
    {"key": "habitacion",   "pattern": r"n[uú]m(ero)?\s*(de\s*)?habitaci[oó]n|habitaci[oó]n\s*(n[uú]m|nro|no\b|#)|^habitaci[oó]n$|^room\s*(number|num|no\b|#)?$|\bhab\.?\s*$"},
    {"key": "marca",        "pattern": r"marca|propiedad|property|brand|hotel"},
    {"key": "valor",        "pattern": r"valor|monto|amount|importe|precio|price"},
    {"key": "moneda",       "pattern": r"moneda|currency|divisa|iso.*4217"},
    {"key": "codigo",       "pattern": r"codigo|code|reserva|booking.*id|res.*id|folio|reference"},
    {"key": "huesped",      "pattern": r"huesped|guest|nombre|name|cliente|customer"},
    {"key": "estado",       "pattern": r"estado|status|state"},
]

MESES_NOMBRE = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
MESES_NUM = {m: i + 1 for i, m in enumerate(MESES_NOMBRE)}


# ============================================================
# UTILIDADES
# ============================================================

def a_numero(v: Any) -> float | None:
    """Convierte un valor a número, limpiando formato de moneda."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\u00a0", "")
    s = re.sub(r"[^0-9,.-]", "", s)
    if "," in s and "." in s:
        # Last separator is the decimal separator; the other is a thousands separator.
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        s = s.replace(thousands, "").replace(decimal, ".")
    elif "," in s:
        tail = s.rsplit(",", 1)[1]
        s = s.replace(",", "." if len(tail) in (1, 2) else "")
    elif s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) in (1, 2) else "".join(parts)
    try:
        return float(s)
    except ValueError:
        return None


def detectar_columnas(columnas: list[str]) -> dict[str, str]:
    """Detecta automáticamente el mapeo de columnas del archivo."""
    detect = {}
    for col in columnas:
        norm = col.lower()
        for campo in CAMPOS_DASHBOARD:
            if re.search(campo["pattern"], norm):
                detect[campo["key"]] = col
                break
    return detect


def _get_or_create_dim(db: Session, model, **kwargs):
    """Obtiene o crea una fila de dimensión usando su clave natural.

    La búsqueda usa solo la clave natural (``fecha`` para ``DimFecha`` y
    ``nombre`` para el resto) para no chocar con la restricción UNIQUE cuando
    los campos descriptivos difieren (por ejemplo, nombre de mes abreviado
    ``May`` frente al completo ``Mayo``, o ``habitaciones`` distinto).
    """
    if "fecha" in kwargs:
        clave = {"fecha": kwargs["fecha"]}
    elif "nombre" in kwargs:
        clave = {"nombre": kwargs["nombre"]}
    else:
        clave = dict(kwargs)
    instance = db.query(model).filter_by(**clave).first()
    if instance is None:
        instance = model(**kwargs)
        db.add(instance)
        db.flush()
    return instance


def _parsear_fecha(fecha_str: str) -> datetime | None:
    """Parsea una fecha en varios formatos."""
    if not fecha_str:
        return None
    s = str(fecha_str).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Intentar parseo flexible
    try:
        from dateutil import parser as date_parser
        return date_parser.parse(s)
    except Exception:
        return None


# ============================================================
# LECTORES DE FORMATOS (servidor)
# ============================================================

def leer_csv(texto: str, delimiter: str | None = None) -> tuple[list[dict], list[str]]:
    """Lee un CSV y devuelve filas + columnas."""
    sample = texto[:8192]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.DictReader(io.StringIO(texto), delimiter=delimiter)
    if len(reader.fieldnames or []) > 100:
        raise ValueError("El archivo supera el máximo permitido de 100 columnas.")
    if len(set(reader.fieldnames or [])) != len(reader.fieldnames or []):
        raise ValueError("El archivo tiene encabezados de columna duplicados.")
    max_rows = int(os.environ.get("NORUS_IMPORT_MAX_ROWS", "1000000"))
    filas = []
    for row in reader:
        if any(v is not None and str(v).strip() for v in row.values()):
            filas.append(dict(row))
            if len(filas) > max_rows:
                raise ValueError("El archivo supera el máximo de filas permitido.")
    if not filas:
        raise ValueError("El CSV no contiene filas de datos.")
    return filas, list(filas[0].keys())


def leer_excel(buffer: bytes) -> tuple[list[dict], list[str]]:
    """Lee un archivo Excel y devuelve filas + columnas."""
    try:
        with zipfile.ZipFile(io.BytesIO(buffer)) as archive:
            members = archive.infolist()
            total_uncompressed = sum(member.file_size for member in members)
            if len(members) > 1000 or total_uncompressed > 512 * 1024 * 1024:
                raise ValueError("El XLSX supera los límites de expansión permitidos.")
            if any(member.file_size > max(1, member.compress_size) * 100 for member in members):
                raise ValueError("El XLSX contiene una relación de compresión no permitida.")
            if "[Content_Types].xml" not in archive.namelist():
                raise ValueError("El archivo no tiene una estructura XLSX válida.")
    except zipfile.BadZipFile as exc:
        raise ValueError("El Excel está dañado o no es un archivo XLSX válido.") from exc
    wb = load_workbook(io.BytesIO(buffer), read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        raise ValueError("La hoja Excel no contiene filas de datos.")
    cols = [str(c) if c else f"col_{i}" for i, c in enumerate(header)]
    if len(cols) > 100:
        wb.close()
        raise ValueError("El archivo supera el máximo permitido de 100 columnas.")
    if len(set(cols)) != len(cols):
        wb.close()
        raise ValueError("El archivo tiene encabezados de columna duplicados.")
    filas = []
    max_rows = int(os.environ.get("NORUS_IMPORT_MAX_ROWS", "1000000"))
    for row in rows:
        if any(v is not None and str(v).strip() for v in row):
            filas.append({cols[i]: row[i] for i in range(len(cols))})
            if len(filas) > max_rows:
                wb.close()
                raise ValueError("El archivo supera el máximo de filas permitido.")
    wb.close()
    if not filas:
        raise ValueError("La hoja Excel no contiene filas de datos.")
    return filas, cols


def leer_json(texto: str) -> tuple[list[dict], list[str]]:
    """Lee un JSON y devuelve filas + columnas."""
    data = json.loads(texto)
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            arr = data["data"]
        else:
            arr = [data]
    elif isinstance(data, list):
        arr = data
    else:
        raise ValueError("El JSON no contiene registros.")
    if not arr:
        raise ValueError("El JSON no contiene registros.")
    if len(arr) > int(
        os.environ.get("NORUS_IMPORT_MAX_ROWS", "1000000")
    ):
        raise ValueError("El archivo supera el máximo de filas permitido.")
    filas = [dict(item) if isinstance(item, dict) else {"valor": item} for item in arr]
    cols = list(dict.fromkeys(key for row in filas for key in row.keys()))
    if len(cols) > 100:
        raise ValueError("El archivo supera el máximo permitido de 100 columnas.")
    return filas, cols


# ============================================================
# DETECTOR DE FORMATO
# ============================================================

def detectar_formato(nombre_archivo: str, contenido: bytes) -> str:
    """Detecta el formato del archivo por extensión y contenido."""
    ext = Path(nombre_archivo).suffix.lower()
    if ext == ".tsv":
        return "tsv"
    if ext in (".csv", ".txt"):
        return "csv"
    if ext == ".xlsx":
        return "excel"
    if ext == ".json":
        return "json"
    raise ValueError("Formato no soportado. Use CSV, TSV, XLSX o JSON.")


# ============================================================
# PIPELINE ETL PRINCIPAL
# ============================================================

def ejecutar_carga(
    db: Session,
    nombre_archivo: str,
    contenido: bytes,
    usuario: str = "",
    mapeo_manual: dict[str, str] | None = None,
    allowed_property_ids: list[int] | None = None,
) -> CargaArchivo:
    """Ejecuta el pipeline ETL completo: extrae, transforma y carga al warehouse."""
    formato = detectar_formato(nombre_archivo, contenido)
    tamano = len(contenido)

    # Registrar carga
    carga = CargaArchivo(
        nombre_archivo=nombre_archivo,
        formato=formato,
        tamano_bytes=tamano,
        usuario=usuario,
    )
    db.add(carga)
    db.flush()

    try:
        from .metrics import etl_files_processed_total, etl_rows_loaded_total

        etl_files_processed_total.labels(formato=formato).inc()
    except Exception:
        etl_rows_loaded_total = None

    try:
        # 1. EXTRACT
        filas, columnas = _extraer(formato, contenido)
        if len(columnas) > 100:
            raise ValueError("El archivo supera el máximo permitido de 100 columnas.")
        if len(filas) > int(os.environ.get("NORUS_IMPORT_MAX_ROWS", "1000000")):
            raise ValueError("El archivo supera el máximo de filas permitido.")
        carga.filas_procesadas = len(filas)

        # 2. TRANSFORM + LOAD
        source_file_hash = hashlib.sha256(contenido).hexdigest()
        exitosas, fallidas, errores = _transformar_y_cargar(
            db, filas, columnas, carga.id, mapeo_manual, source_file_hash, allowed_property_ids
        )
        carga.filas_exitosas = exitosas
        carga.filas_fallidas = fallidas
        carga.estado = "completada" if fallidas == 0 else ("parcial" if exitosas > 0 else "fallida")
        if fallidas:
            detalle = "; ".join(errores[:5])
            carga.mensaje_error = (
                f"{fallidas} filas rechazadas; {exitosas} filas cargadas correctamente."
                + (f" Primeros errores: {detalle}" if detalle else " Revisa los datos y vuelve a importar las filas corregidas.")
            )[:500]
        if etl_rows_loaded_total is not None:
            etl_rows_loaded_total.inc(exitosas)

        # 3. AUDITORÍA
        _auditar(db, "importar", usuario, f"{formato.upper()} · {nombre_archivo} · {exitosas} registros cargados")

    except Exception as e:
        carga.estado = "fallida"
        carga.mensaje_error = str(e)[:500]
        _auditar(db, "importar_fallo", usuario, f"{nombre_archivo}: {str(e)[:200]}")

    db.commit()
    return carga


def _extraer(formato: str, contenido: bytes) -> tuple[list[dict], list[str]]:
    """Extrae filas según el formato."""
    if formato == "csv":
        return leer_csv(contenido.decode("utf-8-sig"))
    if formato == "tsv":
        return leer_csv(contenido.decode("utf-8-sig"), "\t")
    if formato == "excel":
        return leer_excel(contenido)
    if formato == "json":
        return leer_json(contenido.decode("utf-8"))
    raise ValueError(f"Formato no soportado: {formato}")


def leer_tabla(nombre_archivo: str, contenido: bytes) -> tuple[list[dict], list[str]]:
    """Public helper: detect the format and return rows + columns.

    Used by the market-reference importer so CSV, TSV, XLSX and JSON all share
    the same hardened readers and the same environment-configured limits.
    """
    return _extraer(detectar_formato(nombre_archivo, contenido), contenido)



def _transformar_y_cargar(
    db: Session,
    filas: list[dict],
    columnas: list[str],
    carga_id: int,
    mapeo_manual: dict[str, str] | None = None,
    source_file_hash: str = "",
    allowed_property_ids: list[int] | None = None,
) -> tuple[int, int, list[str]]:
    """Load reservation exports and monthly/daily KPI exports without inventing rows."""
    mapeo = detectar_columnas(columnas)
    if mapeo_manual:
        campos_validos = {campo["key"] for campo in CAMPOS_DASHBOARD}
        for key, column in mapeo_manual.items():
            if key not in campos_validos or (column and column not in columnas):
                raise ValueError("El mapeo contiene un campo o encabezado inválido.")
            if column:
                mapeo[key] = column
            else:
                mapeo.pop(key, None)
    exitosas = fallidas = 0
    errores: list[str] = []
    por_dia: dict[tuple[str, int, str], dict] = {}
    por_mes: dict[tuple[int, int, int, str], dict] = {}

    for idx, fila in enumerate(filas, start=1):
        try:
            with db.begin_nested():
                val = lambda key: fila.get(mapeo.get(key, ""), None) if mapeo.get(key) else None
                date_raw = val("fecha")
                if not date_raw and val("mes") not in (None, "") and val("anio") not in (None, ""):
                    month_value = str(val("mes")).strip()
                    parsed_month = a_numero(month_value)
                    if parsed_month is None:
                        full_months = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
                        month_names = {name.casefold(): i + 1 for i, name in enumerate(full_months)}
                        month_names.update({name.casefold(): i + 1 for i, name in enumerate(MESES_NOMBRE)})
                        parsed_month = month_names.get(month_value.casefold())
                    parsed_year = a_numero(val("anio"))
                    if parsed_month and int(parsed_month) == parsed_month and 1 <= int(parsed_month) <= 12 and parsed_year and int(parsed_year) == parsed_year and 1900 <= int(parsed_year) <= 2100:
                        date_raw = f"{int(parsed_year):04d}-{int(parsed_month):02d}-01"
                if not date_raw:
                    date_raw = val("mes")
                dt = _parsear_fecha(str(date_raw)) if date_raw else None
                channel = str(val("canal")).strip()[:100] if val("canal") else ""
                nationality = str(val("nacionalidad")).strip()[:100] if val("nacionalidad") else ""
                segment = str(val("segmento")).strip()[:100] if val("segmento") else ""
                room_type = str(val("tipo_habitacion")).strip()[:100] if val("tipo_habitacion") else ""
                room_number = str(val("habitacion") or "").strip()[:30]
                if room_number and not room_type and not re.search(r"\d", room_number):
                    # Sin columna de tipo y sin dígitos: el valor describe un
                    # tipo de habitación, no un número de puerta.
                    room_type = room_number[:100]
                    room_number = ""
                guest = str(val("huesped") or "").strip()
                reservation_code = str(val("codigo") or "").strip()
                status = str(val("estado") or "").strip()
                is_reservation = bool(reservation_code or guest)
                incident_count = a_numero(val("incidencias"))
                resolution_hours = a_numero(val("tiempo_resol"))
                satisfaction = a_numero(val("satisfaccion"))
                nps = a_numero(val("nps"))
                has_experience_data = any(x is not None for x in (incident_count, resolution_hours, satisfaction, nps))

                if not is_reservation and not has_experience_data and not (dt and any(val(key) not in (None, "") for key in ("ingresos", "valor", "ocupacion", "adr", "revpar", "noches", "habitaciones_disp"))):
                    raise ValueError("La fila no contiene campos reconocibles de reserva ni métricas hoteleras.")

                dim_date = None
                if dt:
                    dim_date = _get_or_create_dim(
                        db, DimFecha, fecha=dt.strftime("%Y-%m-%d"), anio=dt.year, mes=dt.month,
                        mes_nombre=MESES_NOMBRE[dt.month - 1], trimestre=(dt.month - 1) // 3 + 1,
                        dia_semana=dt.weekday(), nombre_dia=["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"][dt.weekday()],
                        es_fin_semana=int(dt.weekday() >= 5),
                    )
                dim_channel = _get_or_create_dim(db, DimCanal, nombre=channel) if channel else None
                dim_nationality = _get_or_create_dim(db, DimNacionalidad, nombre=nationality) if nationality else None
                dim_segment = _get_or_create_dim(db, DimSegmento, nombre=segment) if segment else None
                dim_room = _get_or_create_dim(db, DimTipoHabitacion, nombre=room_type) if room_type else None
                brand_name = str(val("marca") or "Sin clasificar").strip()[:100] or "Sin clasificar"
                dim_brand = _get_or_create_dim(db, DimMarca, nombre=brand_name, habitaciones=0)
                if allowed_property_ids is not None and dim_brand.id not in allowed_property_ids:
                    raise ValueError("La fila pertenece a una propiedad no autorizada para este usuario.")
                currency = str(val("moneda") or os.environ.get("NORUS_DEFAULT_CURRENCY", "CLP")).strip().upper()
                if not re.fullmatch(r"[A-Z]{3}", currency):
                    raise ValueError("La moneda debe ser un código ISO de 3 letras (por ejemplo CLP o USD).")

                income = a_numero(val("ingresos") if val("ingresos") not in (None, "") else val("valor"))
                nights = a_numero(val("noches"))
                adr = a_numero(val("adr"))
                revpar_input = a_numero(val("revpar"))
                occupancy = a_numero(val("ocupacion"))
                available = a_numero(val("habitaciones_disp"))
                if income is not None and income < 0 or nights is not None and nights < 0 or available is not None and available < 0 or adr is not None and adr < 0 or revpar_input is not None and revpar_input < 0:
                    raise ValueError("No se admiten ingresos, noches o capacidad negativos.")
                if occupancy is not None and not 0 <= occupancy <= 100:
                    raise ValueError("La ocupación debe estar entre 0 y 100.")
                if incident_count is not None and incident_count < 0 or resolution_hours is not None and resolution_hours < 0:
                    raise ValueError("Las incidencias y sus tiempos no pueden ser negativos.")
                if satisfaction is not None and not 0 <= satisfaction <= 5:
                    raise ValueError("La satisfacción debe estar en una escala de 0 a 5.")
                if nps is not None and not 0 <= nps <= 10:
                    raise ValueError("Cada respuesta NPS debe estar en una escala de 0 a 10.")

                if is_reservation:
                    code = reservation_code or f"R-{source_file_hash[:32]}-{idx}"
                    values = dict(
                        moneda=currency, huesped=guest[:200], habitacion=room_number,
                        estado=(status or "Confirmada")[:50], valor=income or 0,
                        noches=nights or 0, anticipacion_dias=a_numero(val("anticipacion")) or 0,
                        fecha_id=dim_date.id if dim_date else None, canal_id=dim_channel.id if dim_channel else None,
                        nacionalidad_id=dim_nationality.id if dim_nationality else None,
                        marca_id=dim_brand.id, segmento_id=dim_segment.id if dim_segment else None,
                        tipo_habitacion_id=dim_room.id if dim_room else None, carga_id=carga_id,
                    )
                    existing = db.query(HechoReserva).filter_by(codigo=code, marca_id=dim_brand.id).first()
                    if existing:
                        for field, value in values.items(): setattr(existing, field, value)
                    else:
                        db.add(HechoReserva(codigo=code[:50], **values))

                if has_experience_data:
                    source_key = hashlib.sha256(f"{source_file_hash}:{idx}".encode("utf-8")).hexdigest()
                    experience = db.query(HechoIncidencia).filter_by(origen_hash=source_key).first()
                    experience_values = {
                        "tipo": str(val("tipo_incidencia") or "General").strip()[:100],
                        "area": str(val("area") or "General").strip()[:100],
                        "cantidad": incident_count if incident_count is not None else 0,
                        "tiempo_resolucion_h": resolution_hours,
                        "satisfaccion": satisfaction,
                        "nps": nps,
                        "fecha_id": dim_date.id if dim_date else None,
                        "carga_id": carga_id,
                    }
                    if experience:
                        for field, value in experience_values.items(): setattr(experience, field, value)
                    else:
                        db.add(HechoIncidencia(origen_hash=source_key, **experience_values))

                if dt and any(x is not None for x in (income, nights, adr, revpar_input, occupancy, available)):
                    def empty():
                        return {"income": 0.0, "income_n": 0, "nights": 0.0, "nights_n": 0,
                                "available": 0.0, "available_n": 0, "occ": 0.0, "occ_n": 0,
                                "adr": 0.0, "adr_n": 0, "revpar": 0.0, "revpar_n": 0}

                    day_key = (dt.strftime("%Y-%m-%d"), dim_brand.id, currency)
                    month_key = (dt.year, dt.month, dim_brand.id, currency)
                    day_bucket = por_dia.setdefault(day_key, {"reservation": empty(), "summary": empty()})
                    month_bucket = por_mes.setdefault(month_key, {"reservation": empty(), "summary": empty()})
                    kind = "reservation" if is_reservation else "summary"
                    for aggregate in (day_bucket[kind], month_bucket[kind]):
                        if income is not None: aggregate["income"] += income; aggregate["income_n"] += 1
                        if nights is not None: aggregate["nights"] += nights; aggregate["nights_n"] += 1
                        elif income is not None and adr and adr > 0: aggregate["nights"] += income / adr; aggregate["nights_n"] += 1
                        elif occupancy is not None and available is not None: aggregate["nights"] += available * occupancy / 100; aggregate["nights_n"] += 1
                        if available is not None: aggregate["available"] += available; aggregate["available_n"] += 1
                        elif occupancy and occupancy > 0 and nights is not None: aggregate["available"] += nights / (occupancy / 100); aggregate["available_n"] += 1
                        if occupancy is not None: aggregate["occ"] += occupancy; aggregate["occ_n"] += 1
                        if adr is not None: aggregate["adr"] += adr; aggregate["adr_n"] += 1
                        if revpar_input is not None: aggregate["revpar"] += revpar_input; aggregate["revpar_n"] += 1
                exitosas += 1
        except Exception as exc:
            fallidas += 1
            if len(errores) < 25:
                errores.append(f"fila {idx}: {exc}")

    def compute(sources):
        summary = sources["summary"]
        has_summary_metrics = any(summary[field] for field in ("income_n", "nights_n", "available_n", "occ_n", "adr_n", "revpar_n"))
        item = summary if has_summary_metrics else sources["reservation"]
        nights = item["nights"] if item["nights_n"] else None
        available = item["available"] if item["available_n"] else None
        income = item["income"] if item["income_n"] else None
        occ = nights / available * 100 if nights is not None and available and available > 0 else item["occ"] / item["occ_n"] if item["occ_n"] else None
        adr = income / nights if income is not None and nights and nights > 0 else item["adr"] / item["adr_n"] if item["adr_n"] else None
        revpar = income / available if income is not None and available and available > 0 else (occ / 100 * adr if occ is not None and adr is not None else (item["revpar"] / item["revpar_n"] if item["revpar_n"] else None))
        return {
            "ocupacion": round(occ, 2) if occ is not None else None,
            "adr": round(adr, 2) if adr is not None else None,
            "revpar": round(revpar, 2) if revpar is not None else None,
            "ingresos": round(income, 2) if income is not None else None,
            "noches_vendidas": round(nights, 2) if nights is not None else None,
            "habitaciones_disponibles": round(available, 2) if available is not None else None,
        }

    for (date_text, brand_id, currency), sources in por_dia.items():
        metric = db.query(HechoMetricaDiaria).filter_by(
            fecha=date_text, marca_id=brand_id, moneda=currency
        ).first()
        values = compute(sources)
        values.update({"carga_id": carga_id, "anio": int(date_text[:4]), "mes": int(date_text[5:7])})
        if metric:
            for field, value in values.items():
                setattr(metric, field, value)
        else:
            db.add(HechoMetricaDiaria(fecha=date_text, marca_id=brand_id, moneda=currency, **values))

    for (year, month, brand_id, currency), sources in por_mes.items():
        metric = db.query(HechoMetricaMensual).filter_by(
            anio=year, mes=month, marca_id=brand_id, moneda=currency
        ).first()
        values = compute(sources)
        values["carga_id"] = carga_id
        if metric:
            for field, value in values.items():
                setattr(metric, field, value)
        else:
            db.add(HechoMetricaMensual(anio=year, mes=month, marca_id=brand_id, moneda=currency, **values))

    db.flush()
    return exitosas, fallidas, errores


def _auditar(db: Session, accion: str, usuario: str, detalle: str):
    """Registra un evento de auditoría."""
    evento = EventoAuditoria(accion=accion, usuario=usuario, detalle=detalle)
    db.add(evento)
    db.flush()
