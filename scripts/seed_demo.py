"""============================================================
   NORUS & LUUM · Datos de demostración
   ============================================================
Genera un conjunto determinista y claramente marcado como DEMO para
probar el dashboard con volumen real (>100.000 filas): propiedades,
tipos de habitación, canales, nacionalidades, reservas, métricas
diarias/mensuales, incidencias, operación y referencias de mercado.

Reglas de seguridad:
  * Nunca toca la tabla ``usuario``: las cuentas existentes sobreviven.
  * Los valores de mercado llevan ``fuente="demo"`` y una nota que
    aclara que son ilustrativos; jamás se presentan como estadísticas
    públicas reales.
  * Los datos son reproducibles: usa una semilla fija (``--seed``).

Uso:
    python -m scripts.seed_demo --rows 150000 --reset --out data/demo
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, insert, select

from app.database import SessionLocal
from app.models import (
    CapacidadHabitacion,
    CargaArchivo,
    DimCanal,
    DimFecha,
    DimMarca,
    DimNacionalidad,
    DimSegmento,
    DimTipoHabitacion,
    EjecucionChecklist,
    HechoIncidencia,
    HechoMetricaDiaria,
    HechoMetricaMensual,
    HechoReserva,
    IncidenciaOperativa,
    ItemInventario,
    ObjetoPerdido,
    OrdenMantenimiento,
    ReferenciaMercado,
    TareaOperativa,
    UsuarioPropiedad,
)

# ============================================================
# CATÁLOGOS DE DEMOSTRACIÓN
# ============================================================

PROPIEDADES = [
    ("Norus Luum Providencia", 96, "CLP"),
    ("Norus Luum Vitacura", 120, "CLP"),
    ("Norus Luum Las Condes", 132, "CLP"),
    ("Norus Luum Costanera", 180, "CLP"),
    ("Norus Luum Valparaíso", 84, "CLP"),
    ("Norus Luum Concón", 140, "CLP"),
    ("Norus Luum La Serena", 110, "CLP"),
    ("Norus Luum Pucón", 76, "USD"),
    ("Norus Luum Chiloé", 64, "USD"),
]

# (nombre, multiplicador de tarifa base, peso de demanda)
TIPOS_HABITACION = [
    ("Estándar", 1.00, 34),
    ("Superior", 1.25, 28),
    ("Deluxe", 1.55, 20),
    ("Familiar", 1.70, 10),
    ("Suite", 2.20, 8),
]

CANALES = [
    ("Directo", "directo", 30),
    ("Booking.com", "ota", 26),
    ("Expedia", "ota", 14),
    ("Despegar", "ota", 10),
    ("Agencia de viajes", "agencia", 12),
    ("Walk-in", "directo", 8),
]

NACIONALIDADES = [
    ("Chile", 42),
    ("Argentina", 14),
    ("Brasil", 12),
    ("Estados Unidos", 9),
    ("España", 7),
    ("Alemania", 5),
    ("Francia", 4),
    ("Perú", 4),
    ("Colombia", 3),
]

SEGMENTOS = [
    ("Corporativo", 34),
    ("Ocio", 40),
    ("Grupos", 16),
    ("Larga estancia", 10),
]

ESTADOS_RESERVA = [
    ("Confirmada", 62),
    ("Check-in", 8),
    ("Check-out", 20),
    ("Cancelada", 6),
    ("No show", 4),
]

INCIDENCIAS_TIPO = [
    ("Limpieza", "Housekeeping"),
    ("Climatización", "Mantenimiento"),
    ("Ruido", "Recepción"),
    ("Wifi", "TI"),
    ("Agua caliente", "Mantenimiento"),
    ("Facturación", "Administración"),
]

TAREAS_DEMO = [
    ("Revisar habitaciones bloqueadas", "Housekeeping", "alta"),
    ("Confirmar llegada de grupo", "Recepción", "media"),
    ("Reponer minibar", "Housekeeping", "baja"),
    ("Inspección de seguridad", "Mantenimiento", "alta"),
    ("Preparar sala de eventos", "Eventos", "media"),
    ("Conciliar caja del turno", "Administración", "media"),
]

MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

# Factor de temporada por mes (hemisferio sur, destino vacacional).
TEMPORADA = {
    1: 1.28, 2: 1.24, 3: 1.08, 4: 1.00, 5: 0.94, 6: 0.90,
    7: 1.10, 8: 0.96, 9: 0.98, 10: 1.02, 11: 1.06, 12: 1.18,
}

TARIFA_BASE_CLP = 68_000.0
TARIFA_BASE_USD = 78.0

# Toda fila generada aquí queda vinculada a una carga con este nombre. Ese
# marcador permite borrar únicamente los datos demo sin tocar importaciones
# reales que el usuario haya cargado después.
DEMO_CARGA_NOMBRE = "seed_demo"


def demo_carga_ids(db) -> list[int]:
    """Ids de las cargas de demostración presentes en la base de datos."""
    return list(db.scalars(select(CargaArchivo.id).where(CargaArchivo.nombre_archivo == DEMO_CARGA_NOMBRE)))


def _weighted(rng: random.Random, options):
    total = sum(item[-1] for item in options)
    pick = rng.uniform(0, total)
    acc = 0.0
    for item in options:
        acc += item[-1]
        if pick <= acc:
            return item
    return options[-1]


def _habitaciones(propiedad_idx: int, total: int) -> list[str]:
    """Room numbers per property: 20 rooms per floor, numbered floor*100+room."""
    rooms: list[str] = []
    floor = 1
    while len(rooms) < total:
        for room in range(1, 21):
            rooms.append(f"{floor}{room:02d}")
            if len(rooms) >= total:
                break
        floor += 1
    return rooms


def _bulk(db, model, rows, chunk: int = 5000) -> None:
    for start in range(0, len(rows), chunk):
        db.execute(insert(model), rows[start:start + chunk])
    db.commit()


def _get_or_create_dim(db, model, filas: list[dict], clave: str) -> list:
    """Reutiliza dimensiones existentes en vez de violar su constraint única."""
    claves = [fila[clave] for fila in filas]
    existentes = {
        getattr(obj, clave): obj
        for obj in db.scalars(select(model).where(getattr(model, clave).in_(claves)))
    }
    objetos: list = []
    nuevos: list = []
    for fila in filas:
        obj = existentes.get(fila[clave])
        if obj is None:
            obj = model(**fila)
            nuevos.append(obj)
        objetos.append(obj)
    if nuevos:
        db.add_all(nuevos)
        db.flush()
    return objetos


# ============================================================
# GENERACIÓN
# ============================================================

def generar(rows: int, days: int, seed: int, out: Path | None, db=None) -> None:
    """Genera los datos demo.

    Si se pasa una sesión ``db``, la reutiliza y no la cierra (la usa la API).
    Si no se pasa, crea y cierra su propia sesión (uso desde CLI).
    """
    rng = random.Random(seed)
    propia_sesion = db is None
    if propia_sesion:
        db = SessionLocal()
    try:
        # ---------- Carga que marca todas las filas como demo ----------
        carga = CargaArchivo(
            nombre_archivo=DEMO_CARGA_NOMBRE, formato="demo",
            tamano_bytes=0, filas_procesadas=0, filas_exitosas=0, filas_fallidas=0,
            estado="completada", mensaje_error="", usuario="demo",
            creado_en=datetime.now(),
        )
        db.add(carga)
        db.commit()
        db.refresh(carga)

        # ---------- Dimensiones (reutiliza las que ya existan) ----------
        marcas = _get_or_create_dim(
            db, DimMarca,
            [{"nombre": nombre, "habitaciones": habitaciones} for nombre, habitaciones, _ in PROPIEDADES],
            "nombre",
        )
        tipos = _get_or_create_dim(
            db, DimTipoHabitacion,
            [{"nombre": nombre} for nombre, _, _ in TIPOS_HABITACION], "nombre",
        )
        canales = _get_or_create_dim(
            db, DimCanal,
            [{"nombre": nombre, "tipo": tipo} for nombre, tipo, _ in CANALES], "nombre",
        )
        nacionalidades = _get_or_create_dim(
            db, DimNacionalidad,
            [{"nombre": nombre} for nombre, _ in NACIONALIDADES], "nombre",
        )
        segmentos = _get_or_create_dim(
            db, DimSegmento,
            [{"nombre": nombre} for nombre, _ in SEGMENTOS], "nombre",
        )

        tipo_ids = {t.nombre: t.id for t in tipos}
        canal_ids = {c.nombre: c.id for c in canales}
        nac_ids = {n.nombre: n.id for n in nacionalidades}
        seg_ids = {s.nombre: s.id for s in segmentos}

        hoy = date.today()
        inicio = hoy - timedelta(days=days - 1)
        filas_fecha = []
        for i in range(days):
            d = inicio + timedelta(days=i)
            filas_fecha.append({
                "fecha": d.isoformat(),
                "anio": d.year,
                "mes": d.month,
                "mes_nombre": MESES[d.month - 1],
                "trimestre": (d.month - 1) // 3 + 1,
                "dia_semana": d.weekday(),
                "nombre_dia": DIAS[d.weekday()],
                "es_fin_semana": 1 if d.weekday() >= 5 else 0,
            })
        fechas = _get_or_create_dim(db, DimFecha, filas_fecha, "fecha")
        fecha_ids = {f.fecha: f.id for f in fechas}

        # ---------- Capacidad declarada (única por marca + tipo) ----------
        capacidad_existente = {
            (c.marca_id, c.tipo_habitacion_id): c
            for c in db.scalars(select(CapacidadHabitacion))
        }
        nuevas_capacidades = []
        for marca in marcas:
            reparto = rng.sample(range(1, 100), len(TIPOS_HABITACION))
            total_reparto = sum(reparto)
            for (tipo_nombre, _, _), peso in zip(TIPOS_HABITACION, reparto):
                clave = (marca.id, tipo_ids[tipo_nombre])
                habitaciones = max(1, round(marca.habitaciones * peso / total_reparto))
                actual = capacidad_existente.get(clave)
                if actual is not None:
                    actual.habitaciones = habitaciones
                    actual.actualizado_por = "demo"
                else:
                    nueva = CapacidadHabitacion(
                        marca_id=marca.id,
                        tipo_habitacion_id=tipo_ids[tipo_nombre],
                        habitaciones=habitaciones,
                        actualizado_por="demo",
                    )
                    nuevas_capacidades.append(nueva)
                    capacidad_existente[clave] = nueva
        if nuevas_capacidades:
            db.add_all(nuevas_capacidades)
        db.flush()

        # ---------- Reservas + agregados ----------
        rooms_by_marca = {
            marca.id: _habitaciones(i, habitaciones)
            for i, (marca, (_, habitaciones, _)) in enumerate(zip(marcas, PROPIEDADES))
        }
        moneda_by_marca = {m.id: moneda for m, (_, _, moneda) in zip(marcas, PROPIEDADES)}
        marca_weights = [(m, 40 + m.habitaciones // 4) for m in marcas]

        reservas: list[dict] = []
        export_rows: list[dict] = []
        incidencias: list[dict] = []
        # (fecha, marca_id) -> [ingresos, noches]
        daily: dict[tuple[str, int], list[float]] = {}
        incidente_seq = 0
        ahora = datetime.now()

        for i in range(rows):
            marca = _weighted(rng, marca_weights)[0]
            tipo_nombre, mult, _ = _weighted(rng, TIPOS_HABITACION)
            canal_nombre, _, _ = _weighted(rng, CANALES)
            nac_nombre, _ = _weighted(rng, NACIONALIDADES)
            seg_nombre, _ = _weighted(rng, SEGMENTOS)
            estado, _ = _weighted(rng, ESTADOS_RESERVA)

            fecha = rng.choice(fechas).fecha
            d = date.fromisoformat(fecha)
            noches = max(1, int(rng.triangular(1, 7, 2)))
            anticipacion = max(0.0, rng.gauss(21, 18))
            habitacion = rng.choice(rooms_by_marca[marca.id])

            base = TARIFA_BASE_USD if moneda_by_marca[marca.id] == "USD" else TARIFA_BASE_CLP
            tarifa = base * mult * TEMPORADA[d.month] * rng.uniform(0.9, 1.12)
            cancelada = estado in ("Cancelada", "No show")
            valor = 0.0 if cancelada else round(tarifa * noches, 2)
            noches_efectivas = 0.0 if cancelada else float(noches)

            reservas.append({
                "codigo": f"DEMO-{i:09d}",
                "moneda": moneda_by_marca[marca.id],
                "huesped": f"Huésped Demo {i:07d}",
                "habitacion": habitacion,
                "estado": estado,
                "valor": valor,
                "noches": noches_efectivas,
                "anticipacion_dias": round(anticipacion, 1),
                "fecha_id": fecha_ids[fecha],
                "canal_id": canal_ids[canal_nombre],
                "nacionalidad_id": nac_ids[nac_nombre],
                "marca_id": marca.id,
                "segmento_id": seg_ids[seg_nombre],
                "tipo_habitacion_id": tipo_ids[tipo_nombre],
                "carga_id": carga.id,
                "creado_en": ahora,
            })
            export_rows.append({
                "codigo": f"DEMO-{i:09d}",
                "huesped": f"Huésped Demo {i:07d}",
                "habitacion": habitacion,
                "fecha": fecha,
                "marca": marca.nombre,
                "moneda": moneda_by_marca[marca.id],
                "ocupacion": "",
                "adr": "",
                "revpar": "",
                "ingresos": valor,
                "noches": noches_efectivas,
                "habitaciones_disp": "",
                "canal": canal_nombre,
                "nacionalidad": nac_nombre,
                "segmento": seg_nombre,
                "anticipacion": round(anticipacion, 1),
                "tipo_habitacion": tipo_nombre,
                "estado": estado,
            })

            acc = daily.setdefault((fecha, marca.id), [0.0, 0.0])
            acc[0] += valor
            acc[1] += noches_efectivas

            # Incidencias de satisfacción (una por cada ~25 reservas).
            if rng.random() < 0.04:
                tipo_inc, area_inc = rng.choice(INCIDENCIAS_TIPO)
                incidente_seq += 1
                incidencias.append({
                    "origen_hash": f"demo-{marca.id}-{fecha}-{tipo_inc}-{incidente_seq}",
                    "tipo": tipo_inc,
                    "area": area_inc,
                    "cantidad": 1.0,
                    "tiempo_resolucion_h": round(rng.uniform(0.5, 12.0), 2),
                    "satisfaccion": round(rng.uniform(2.5, 5.0), 2),
                    "nps": float(rng.randint(-20, 100)),
                    "fecha_id": fecha_ids[fecha],
                    "marca_id": marca.id,
                    "carga_id": carga.id,
                })

        print(f"  · reservas: {len(reservas):,}")
        _bulk(db, HechoReserva, reservas)
        print(f"  · incidencias de satisfacción: {len(incidencias):,}")
        _bulk(db, HechoIncidencia, incidencias)

        # ---------- Métricas diaria y mensual ----------
        diarias: list[dict] = []
        mensual: dict[tuple[int, int, int], list[float]] = {}
        marca_habitaciones = {m.id: m.habitaciones for m in marcas}
        for (fecha, marca_id), (ingresos, noches) in daily.items():
            disponibles = float(marca_habitaciones[marca_id])
            adr = ingresos / noches if noches else None
            ocupacion = (noches / disponibles * 100) if disponibles else None
            revpar = ingresos / disponibles if disponibles else None
            d = date.fromisoformat(fecha)
            diarias.append({
                "fecha": fecha,
                "anio": d.year,
                "mes": d.month,
                "marca_id": marca_id,
                "moneda": moneda_by_marca[marca_id],
                "ocupacion": round(ocupacion, 2) if ocupacion is not None else None,
                "adr": round(adr, 2) if adr is not None else None,
                "revpar": round(revpar, 2) if revpar is not None else None,
                "ingresos": round(ingresos, 2),
                "noches_vendidas": round(noches, 2),
                "habitaciones_disponibles": disponibles,
                "carga_id": carga.id,
            })
            m = mensual.setdefault((d.year, d.month, marca_id), [0.0, 0.0, 0.0])
            m[0] += ingresos
            m[1] += noches
            m[2] += disponibles

        mensuales: list[dict] = []
        for (anio, mes, marca_id), (ingresos, noches, disponibles) in mensual.items():
            adr = ingresos / noches if noches else None
            ocupacion = (noches / disponibles * 100) if disponibles else None
            revpar = ingresos / disponibles if disponibles else None
            mensuales.append({
                "anio": anio, "mes": mes, "marca_id": marca_id,
                "moneda": moneda_by_marca[marca_id],
                "ocupacion": round(ocupacion, 2) if ocupacion is not None else None,
                "adr": round(adr, 2) if adr is not None else None,
                "revpar": round(revpar, 2) if revpar is not None else None,
                "ingresos": round(ingresos, 2),
                "noches_vendidas": round(noches, 2),
                "habitaciones_disponibles": round(disponibles, 2),
                "carga_id": carga.id,
            })
        # No sobrescribir agregados reales que ya existan para la misma clave.
        ya_diarias = set(db.execute(select(
            HechoMetricaDiaria.fecha, HechoMetricaDiaria.marca_id, HechoMetricaDiaria.moneda
        )).all())
        diarias = [
            fila for fila in diarias
            if (fila["fecha"], fila["marca_id"], fila["moneda"]) not in ya_diarias
        ]
        ya_mensuales = set(db.execute(select(
            HechoMetricaMensual.anio, HechoMetricaMensual.mes,
            HechoMetricaMensual.marca_id, HechoMetricaMensual.moneda
        )).all())
        mensuales = [
            fila for fila in mensuales
            if (fila["anio"], fila["mes"], fila["marca_id"], fila["moneda"]) not in ya_mensuales
        ]
        print(f"  · métricas diarias: {len(diarias):,} · mensuales: {len(mensuales):,}")
        if diarias:
            _bulk(db, HechoMetricaDiaria, diarias)
        if mensuales:
            _bulk(db, HechoMetricaMensual, mensuales)

        # ---------- Operación hotelera (incidencias vinculadas) ----------
        ops_incidencias: list[dict] = []
        muestra = rng.sample(reservas, min(len(reservas), 2500))
        estados_ops = [("abierta", 30), ("en_progreso", 25), ("resuelta", 40), ("cancelada", 5)]
        for idx, reserva in enumerate(muestra):
            estado_ops, _ = _weighted(rng, estados_ops)
            titulo, area_ops = rng.choice(INCIDENCIAS_TIPO)
            creado = ahora - timedelta(days=rng.randint(0, days), hours=rng.randint(0, 23))
            resuelto = creado + timedelta(hours=rng.uniform(1, 36)) if estado_ops == "resuelta" else None
            ops_incidencias.append({
                "titulo": f"{titulo} en habitación {reserva['habitacion']}",
                "detalle": "Incidencia de demostración vinculada a la estancia de la habitación.",
                "categoria": titulo,
                "severidad": rng.choice(["baja", "media", "alta", "critica"]),
                "estado": estado_ops,
                "area": area_ops,
                "habitacion": reserva["habitacion"],
                "reserva_id": None,  # el vínculo se resuelve por propiedad + habitación + fecha
                "responsable": rng.choice(["Recepción", "Housekeeping", "Mantenimiento"]),
                "propiedad_id": reserva["marca_id"],
                "creado_por": "demo",
                "creado_en": creado,
                "resuelto_en": resuelto,
            })
        print(f"  · incidencias operativas: {len(ops_incidencias):,}")
        _bulk(db, IncidenciaOperativa, ops_incidencias)

        tareas = []
        for idx in range(120):
            titulo, area, prioridad = rng.choice(TAREAS_DEMO)
            tareas.append({
                "titulo": f"{titulo} ({idx + 1})",
                "descripcion": "Tarea de demostración.",
                "area": area,
                "prioridad": prioridad,
                "estado": rng.choice(["pendiente", "en_progreso", "completada", "cancelada"]),
                "asignado_a": rng.choice(["", "Ana", "Luis", "Marta"]),
                "propiedad_id": rng.choice(marcas).id,
                "vence_en": ahora + timedelta(days=rng.randint(-5, 20)),
                "creado_por": "demo",
                "creado_en": ahora,
                "actualizado_en": ahora,
            })
        _bulk(db, TareaOperativa, tareas)

        mantenciones = []
        for idx in range(90):
            mantenciones.append({
                "titulo": f"Mantención preventiva {idx + 1}",
                "detalle": "Orden de demostración.",
                "tipo": rng.choice(["Preventivo", "Correctivo"]),
                "prioridad": rng.choice(["baja", "media", "alta"]),
                "estado": rng.choice(["pendiente", "en_progreso", "cerrado"]),
                "ubicacion": f"Piso {rng.randint(1, 8)}",
                "responsable": rng.choice(["", "Equipo técnico"]),
                "propiedad_id": rng.choice(marcas).id,
                "creado_por": "demo",
                "creado_en": ahora,
                "cerrado_en": None,
            })
        _bulk(db, OrdenMantenimiento, mantenciones)

        inventario = []
        for idx in range(80):
            cantidad = round(rng.uniform(0, 400), 2)
            inventario.append({
                "nombre": f"Insumo demo {idx + 1}",
                "categoria": rng.choice(["Aseo", "Amenities", "Textiles", "Alimentos"]),
                "unidad": rng.choice(["unidad", "litro", "kg", "caja"]),
                "cantidad": cantidad,
                "stock_minimo": round(cantidad * rng.uniform(0.1, 0.5), 2),
                "proveedor": rng.choice(["", "Proveedor Demo"]),
                "propiedad_id": rng.choice(marcas).id,
                "actualizado_por": "demo",
                "actualizado_en": ahora,
            })
        _bulk(db, ItemInventario, inventario)

        perdidos = []
        for idx in range(60):
            perdidos.append({
                "descripcion": f"Objeto de demostración {idx + 1}",
                "categoria": rng.choice(["Electrónica", "Ropa", "Documentos", "Otros"]),
                "ubicacion": f"Habitación {rng.randint(101, 899)}",
                "fecha_encontrado": ahora - timedelta(days=rng.randint(0, 60)),
                "estado": rng.choice(["resguardado", "reclamado", "entregado", "desechado"]),
                "responsable": "demo",
                "propiedad_id": rng.choice(marcas).id,
                "registrado_por": "demo",
                "creado_en": ahora,
            })
        _bulk(db, ObjetoPerdido, perdidos)

        checklists = []
        for idx in range(40):
            checklists.append({
                "nombre": f"Checklist demo {idx + 1}",
                "area": rng.choice(["Housekeeping", "Recepción", "Mantenimiento"]),
                "elementos": json.dumps([f"Paso {n + 1}" for n in range(6)]),
                "estado": rng.choice(["en_progreso", "completada", "cancelada"]),
                "propiedad_id": rng.choice(marcas).id,
                "ejecutado_por": "demo",
                "creado_en": ahora,
                "completado_en": None,
            })
        _bulk(db, EjecucionChecklist, checklists)

        # ---------- Referencias de mercado (marcadas como demo) ----------
        referencias: list[dict] = []
        nota = "Referencia de DEMOSTRACIÓN; no es una estadística pública real."
        for marca in marcas:
            # Últimos 24 meses completos.
            cursor = date(hoy.year, hoy.month, 1)
            for _ in range(24):
                periodo = cursor.strftime("%Y-%m")
                factor = TEMPORADA[cursor.month]
                base = TARIFA_BASE_USD if moneda_by_marca[marca.id] == "USD" else TARIFA_BASE_CLP
                ref_adr = round(base * factor * rng.uniform(1.0, 1.25), 2)
                referencias.append({
                    "marca_id": marca.id, "fuente": "demo (generado)", "url": "",
                    "tipo": "mercado", "metrica": "adr", "periodo": periodo,
                    "valor": ref_adr, "moneda": moneda_by_marca[marca.id],
                    "tipo_habitacion": "", "notas": nota,
                    "creado_por": "demo", "creado_en": ahora, "actualizado_en": ahora,
                })
                referencias.append({
                    "marca_id": marca.id, "fuente": "demo (generado)", "url": "",
                    "tipo": "mercado", "metrica": "precio", "periodo": periodo,
                    "valor": round(ref_adr * 1.35, 2), "moneda": moneda_by_marca[marca.id],
                    "tipo_habitacion": "", "notas": nota + " Precio público de largo plazo (rack).",
                    "creado_por": "demo", "creado_en": ahora, "actualizado_en": ahora,
                })
                referencias.append({
                    "marca_id": marca.id, "fuente": "demo (generado)", "url": "",
                    "tipo": "mercado", "metrica": "ocupacion", "periodo": periodo,
                    "valor": round(min(95.0, rng.uniform(45, 88) * factor), 2),
                    "moneda": moneda_by_marca[marca.id], "tipo_habitacion": "", "notas": nota,
                    "creado_por": "demo", "creado_en": ahora, "actualizado_en": ahora,
                })
                referencias.append({
                    "marca_id": marca.id, "fuente": "demo (generado)", "url": "",
                    "tipo": "mercado", "metrica": "revpar", "periodo": periodo,
                    "valor": round(ref_adr * rng.uniform(0.6, 0.85), 2),
                    "moneda": moneda_by_marca[marca.id], "tipo_habitacion": "", "notas": nota,
                    "creado_por": "demo", "creado_en": ahora, "actualizado_en": ahora,
                })
                prev = cursor - timedelta(days=1)
                cursor = date(prev.year, prev.month, 1)

        # Referencias globales (sin propiedad) para que un usuario con acceso
        # global vea la comparación de mercado cuando no selecciona propiedad.
        for moneda in ("CLP", "USD"):
            base = TARIFA_BASE_CLP if moneda == "CLP" else TARIFA_BASE_USD
            cursor = date(hoy.year, hoy.month, 1)
            for _ in range(24):
                periodo = cursor.strftime("%Y-%m")
                factor = TEMPORADA[cursor.month]
                ref_adr = round(base * factor * rng.uniform(1.05, 1.3), 2)
                for metrica, valor in (
                    ("adr", ref_adr),
                    ("precio", round(ref_adr * 1.4, 2)),
                    ("ocupacion", round(min(95.0, rng.uniform(45, 88) * factor), 2)),
                    ("revpar", round(ref_adr * rng.uniform(0.6, 0.85), 2)),
                ):
                    referencias.append({
                        "marca_id": None, "fuente": "demo (generado)", "url": "",
                        "tipo": "mercado", "metrica": metrica, "periodo": periodo,
                        "valor": valor, "moneda": moneda, "tipo_habitacion": "",
                        "notas": nota, "creado_por": "demo",
                        "creado_en": ahora, "actualizado_en": ahora,
                    })
                prev = cursor - timedelta(days=1)
                cursor = date(prev.year, prev.month, 1)
        print(f"  · referencias de mercado: {len(referencias):,}")
        _bulk(db, ReferenciaMercado, referencias)

        carga.filas_procesadas = len(reservas)
        carga.filas_exitosas = len(reservas)
        db.commit()

        print("  · datos de demostración cargados")

        if out is not None:
            _escribir_archivos(out, export_rows, referencias)
    finally:
        if propia_sesion:
            db.close()


# ============================================================
# ARCHIVOS DE MUESTRA (prueba de importación multi-formato)
# ============================================================

def _escribir_archivos(out: Path, reservas: list[dict], referencias: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cabecera = [
        "codigo", "huesped", "habitacion", "fecha", "marca", "moneda",
        "ocupacion", "adr", "revpar", "ingresos", "noches", "habitaciones_disp",
        "canal", "nacionalidad", "segmento", "anticipacion", "tipo_habitacion", "estado",
    ]
    print(f"  · escribiendo archivos de muestra en {out} …")

    rows = [dict(r) for r in reservas]

    with (out / "reservas_demo.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=cabecera)
        writer.writeheader()
        writer.writerows(rows)

    with (out / "reservas_demo.tsv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cabecera, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    with (out / "reservas_demo.json").open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)

    # XLSX completo: la muestra debe tener el mismo volumen que el CSV/TSV/JSON
    # para probar de verdad la carga de más de 100.000 filas en todos los formatos.
    try:
        from openpyxl import Workbook

        wb = Workbook(write_only=True)
        ws = wb.create_sheet("reservas_demo")
        ws.append(cabecera)
        for row in rows:
            ws.append([row[c] for c in cabecera])
        wb.save(out / "reservas_demo.xlsx")
    except Exception as exc:  # pragma: no cover - openpyxl es opcional aquí
        print(f"    (XLSX omitido: {exc})")

    ref_cabecera = ["fuente", "metrica", "periodo", "valor", "moneda", "tipo_habitacion", "url", "notas", "propiedad"]
    with (out / "referencias_mercado_demo.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=ref_cabecera)
        writer.writeheader()
        for ref in referencias:
            writer.writerow({
                "fuente": ref["fuente"], "metrica": ref["metrica"], "periodo": ref["periodo"],
                "valor": ref["valor"], "moneda": ref["moneda"],
                "tipo_habitacion": ref["tipo_habitacion"], "url": ref["url"],
                "notas": ref["notas"], "propiedad": "",
            })
    print("  · archivos de muestra listos")


# ============================================================
# BORRADO SELECTIVO DE DATOS DEMO
# ============================================================

def limpiar_demo(db) -> dict:
    """Borra únicamente los datos generados por este módulo.

    Conserva usuarios, cuentas, eventos de auditoría y cualquier importación
    real. Devuelve un resumen de filas eliminadas por tabla.
    """
    ids = demo_carga_ids(db)
    borrados: dict[str, int] = {}

    def _borrar(model, condicion) -> None:
        borrados[model.__tablename__] = db.query(model).filter(condicion).delete(synchronize_session=False)

    if ids:
        for model in (
            HechoReserva, HechoMetricaDiaria, HechoMetricaMensual, HechoIncidencia,
        ):
            _borrar(model, model.carga_id.in_(ids))

    # ReferenciaMercado no tiene carga_id: se identifica por su fuente.
    _borrar(ReferenciaMercado, ReferenciaMercado.fuente == "demo (generado)")

    # Los módulos de operación no tienen carga_id: se identifican por el autor.
    for model, campo in (
        (IncidenciaOperativa, "creado_por"),
        (TareaOperativa, "creado_por"),
        (OrdenMantenimiento, "creado_por"),
        (ItemInventario, "actualizado_por"),
        (ObjetoPerdido, "registrado_por"),
        (EjecucionChecklist, "ejecutado_por"),
        (CapacidadHabitacion, "actualizado_por"),
    ):
        _borrar(model, getattr(model, campo) == "demo")

    if ids:
        _borrar(CargaArchivo, CargaArchivo.id.in_(ids))

    # Dimensiones creadas por el demo que ya no son referenciadas por nada.
    marcas_demo = {nombre for nombre, _, _ in PROPIEDADES}
    # La demo real usa nombres propios; se incluyen para poder borrarla también.
    marcas_demo.update(
        db.scalars(select(DimMarca.nombre).where(DimMarca.nombre.like("Demo real · %")))
    )
    tipos_demo = {nombre for nombre, _, _ in TIPOS_HABITACION}
    canales_demo = {nombre for nombre, _, _ in CANALES}
    nacs_demo = {nombre for nombre, _ in NACIONALIDADES}
    segs_demo = {nombre for nombre, _ in SEGMENTOS}

    referencias_dim = (
        (DimMarca, marcas_demo, (
            (HechoReserva, "marca_id"), (HechoMetricaDiaria, "marca_id"),
            (HechoMetricaMensual, "marca_id"), (HechoIncidencia, "marca_id"),
            (ReferenciaMercado, "marca_id"), (CapacidadHabitacion, "marca_id"),
            (IncidenciaOperativa, "propiedad_id"), (TareaOperativa, "propiedad_id"),
            (OrdenMantenimiento, "propiedad_id"), (ItemInventario, "propiedad_id"),
            (ObjetoPerdido, "propiedad_id"), (EjecucionChecklist, "propiedad_id"),
            (UsuarioPropiedad, "marca_id"),
        )),
        (DimFecha, None, (
            (HechoReserva, "fecha_id"), (HechoIncidencia, "fecha_id"),
        )),
        (DimCanal, canales_demo, ((HechoReserva, "canal_id"),)),
        (DimNacionalidad, nacs_demo, ((HechoReserva, "nacionalidad_id"),)),
        (DimSegmento, segs_demo, ((HechoReserva, "segmento_id"),)),
        (DimTipoHabitacion, tipos_demo, (
            (HechoReserva, "tipo_habitacion_id"), (CapacidadHabitacion, "tipo_habitacion_id"),
        )),
    )

    for dimension, nombres, referencias in referencias_dim:
        consulta = select(dimension.id)
        if nombres is not None:
            consulta = consulta.where(dimension.nombre.in_(nombres))
        huérfanas = []
        for (fila_id,) in db.execute(consulta):
            if any(
                db.scalar(select(model.id).where(getattr(model, campo) == fila_id).limit(1)) is not None
                for model, campo in referencias
            ):
                continue
            huérfanas.append(fila_id)
        if huérfanas:
            borrados[dimension.__tablename__] = db.query(dimension).filter(
                dimension.id.in_(huérfanas)
            ).delete(synchronize_session=False)

    db.commit()
    return borrados


# ============================================================
# LIMPIEZA TOTAL (solo CLI)
# ============================================================

def limpiar(db) -> None:
    """Delete all fact/dimension/ops rows but keep the ``usuario`` table."""
    print("  · limpiando datos previos (se conservan los usuarios)…")
    for model in (
        HechoReserva, HechoMetricaDiaria, HechoMetricaMensual, HechoIncidencia,
        IncidenciaOperativa, OrdenMantenimiento, TareaOperativa, ItemInventario,
        ObjetoPerdido, EjecucionChecklist, CapacidadHabitacion, ReferenciaMercado,
        CargaArchivo, UsuarioPropiedad, DimFecha, DimCanal, DimNacionalidad,
        DimSegmento, DimTipoHabitacion, DimMarca,
    ):
        db.execute(delete(model))
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera datos de demostración para Norus & Luum.")
    parser.add_argument("--rows", type=int, default=150000, help="Número de reservas a generar.")
    parser.add_argument("--days", type=int, default=730, help="Días de historia hacia atrás.")
    parser.add_argument("--seed", type=int, default=20260101, help="Semilla determinista.")
    parser.add_argument("--reset", action="store_true", help="Borra hechos y dimensiones antes de sembrar.")
    parser.add_argument("--out", default="data/demo", help="Carpeta para archivos de muestra (vacío para omitir).")
    args = parser.parse_args()

    if args.rows < 1:
        raise SystemExit("--rows debe ser mayor que cero.")

    out = Path(args.out) if args.out else None
    db = SessionLocal()
    try:
        if args.reset:
            limpiar(db)
    finally:
        db.close()

    print(f"Sembrando {args.rows:,} reservas de demostración…")
    generar(args.rows, args.days, args.seed, out)
    print("Listo. Recuerda: los datos son de demostración, no estadísticas reales.")


if __name__ == "__main__":
    main()
