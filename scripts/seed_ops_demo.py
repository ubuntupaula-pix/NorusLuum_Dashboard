# -*- coding: utf-8 -*-
"""Carga operativa de demostración derivada de los datos demo ya presentes.

El origen es la propia base Norus Luum: ``hecho_incidencia`` (satisfacción y
NPS de muestra) y ``hecho_reserva``. El script **no modifica** esas filas; solo
inserta registros nuevos en las tablas ``ops_*`` que estén vacías, marcados con
``creado_por='seed_ops_demo'`` para poder identificarlos y borrarlos sin afectar
datos reales.

Uso::

    python -m scripts.seed_ops_demo
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta

from app.database import SessionLocal
from app.models import (
    DimFecha,
    DimMarca,
    EjecucionChecklist,
    HechoIncidencia,
    HechoReserva,
    IncidenciaOperativa,
    ItemInventario,
    ObjetoPerdido,
    OrdenMantenimiento,
    TareaOperativa,
)

SEED = "seed_ops_demo"
RNG = random.Random(20261126)

AREAS = {
    "Wifi": "TI",
    "Limpieza": "Housekeeping",
    "Facturación": "Administración",
    "Ruido": "Recepción",
    "Agua caliente": "Mantenimiento",
    "Climatización": "Mantenimiento",
}


def _ya_cargado(db, model, campo: str) -> bool:
    return db.query(model).filter(getattr(model, campo) == SEED).count() > 0


def _severidad(tiempo_h: float | None, satisfaccion: float | None) -> str:
    if satisfaccion is not None and satisfaccion < 3.2:
        return "alta"
    if tiempo_h is not None and tiempo_h > 8:
        return "media"
    return "baja"


def main() -> int:
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
            print("No hay propiedades Norus Luum en la base.", flush=True)
            return 1

        chk = [
            (IncidenciaOperativa, "creado_por"),
            (TareaOperativa, "creado_por"),
            (OrdenMantenimiento, "creado_por"),
            (ItemInventario, "actualizado_por"),
            (ObjetoPerdido, "registrado_por"),
            (EjecucionChecklist, "ejecutado_por"),
        ]
        if all(_ya_cargado(db, model, campo) for model, campo in chk):
            print("La operativa de demostración ya está cargada; sin cambios.")
            return 0

        reservas_por_propiedad: dict[int, list[tuple[int, str]]] = {}
        for pid in propiedades:
            reservas_por_propiedad[pid] = [
                (row[0], row[1] or "")
                for row in db.query(HechoReserva.id, HechoReserva.habitacion)
                .filter(HechoReserva.marca_id == pid)
                .order_by(HechoReserva.id.desc())
                .limit(400)
                .all()
            ]

        incidencias = (
            db.query(HechoIncidencia, DimFecha.fecha)
            .join(DimFecha, HechoIncidencia.fecha_id == DimFecha.id)
            .filter(HechoIncidencia.marca_id.in_(propiedades))
            .order_by(HechoIncidencia.id.desc())
            .limit(600)
            .all()
        )

        ahora = datetime.now()
        creadas = 0
        if not _ya_cargado(db, IncidenciaOperativa, "creado_por"):
            for incidencia, fecha_str in incidencias:
                pid = int(incidencia.marca_id)
                reservas = reservas_por_propiedad.get(pid) or []
                reserva_id, habitacion = RNG.choice(reservas) if reservas else (None, "")
                try:
                    creado = datetime.combine(
                        date.fromisoformat(fecha_str), datetime.min.time()
                    ) + timedelta(hours=RNG.randint(7, 22))
                except (TypeError, ValueError):
                    creado = ahora
                estado = RNG.choices(
                    ["abierta", "en_progreso", "resuelta", "cancelada"], [15, 20, 60, 5]
                )[0]
                satisfaccion = incidencia.satisfaccion
                detalle = (
                    f"Incidencia de demostración Norus Luum · satisfacción "
                    f"{satisfaccion:.2f}/5 · NPS {incidencia.nps:.0f}."
                    if satisfaccion is not None and incidencia.nps is not None
                    else "Incidencia de demostración Norus Luum."
                )
                db.add(IncidenciaOperativa(
                    titulo=f"{incidencia.tipo} · {incidencia.area}",
                    detalle=detalle,
                    categoria=incidencia.tipo or "General",
                    severidad=_severidad(incidencia.tiempo_resolucion_h, satisfaccion),
                    estado=estado,
                    area=incidencia.area or AREAS.get(incidencia.tipo, "Recepción"),
                    habitacion=habitacion,
                    reserva_id=reserva_id,
                    responsable=RNG.choice(["Recepción", "Housekeeping", "Mantenimiento", "TI"]),
                    propiedad_id=pid,
                    creado_por=SEED,
                    creado_en=creado,
                    resuelto_en=creado + timedelta(hours=incidencia.tiempo_resolucion_h or 2)
                    if estado == "resuelta" else None,
                ))
                creadas += 1
            print(f"  · incidencias operativas con satisfacción: {creadas:,}")

        if not _ya_cargado(db, TareaOperativa, "creado_por"):
            tareas = [
                ("Revisión de habitaciones", "Housekeeping", "alta"),
                ("Cuadratura de caja", "Administración", "media"),
                ("Repaso de amenities", "Housekeeping", "baja"),
                ("Verificación de tarifas", "Revenue", "alta"),
                ("Control de Accesos", "Recepción", "media"),
            ]
            for idx in range(120):
                titulo, area, prioridad = RNG.choice(tareas)
                db.add(TareaOperativa(
                    titulo=f"{titulo} #{idx + 1}",
                    descripcion="Tarea operativa de demostración Norus Luum.",
                    area=area,
                    prioridad=prioridad,
                    estado=RNG.choice(["pendiente", "en_progreso", "completada"]),
                    asignado_a=RNG.choice(["", "Ana", "Luis", "Marta", "Carlos"]),
                    propiedad_id=RNG.choice(propiedades),
                    vence_en=ahora + timedelta(days=RNG.randint(-3, 20)),
                    creado_por=SEED,
                    creado_en=ahora,
                    actualizado_en=ahora,
                ))
            print("  · tareas operativas: 120")

        if not _ya_cargado(db, OrdenMantenimiento, "creado_por"):
            tipos = ["Agua caliente", "Climatización", "Wifi"]
            for idx in range(90):
                tipo = RNG.choice(tipos)
                db.add(OrdenMantenimiento(
                    titulo=f"{tipo} · orden {idx + 1}",
                    detalle="Orden de mantenimiento de demostración derivada de incidencias Norus Luum.",
                    tipo=RNG.choice(["Preventivo", "Correctivo"]),
                    prioridad=RNG.choice(["baja", "media", "alta"]),
                    estado=RNG.choice(["pendiente", "en_progreso", "cerrado"]),
                    ubicacion=f"Piso {RNG.randint(1, 8)}",
                    responsable="Equipo técnico",
                    propiedad_id=RNG.choice(propiedades),
                    creado_por=SEED,
                    creado_en=ahora - timedelta(days=RNG.randint(0, 60)),
                ))
            print("  · órdenes de mantenimiento: 90")

        if not _ya_cargado(db, ItemInventario, "actualizado_por"):
            items = [
                ("Toallas", "Textiles", "unidad", 180, 60),
                ("Papel higiénico", "Aseo", "unidad", 240, 80),
                ("Amenidades", "Amenities", "caja", 90, 30),
                ("Sábanas", "Textiles", "unidad", 160, 50),
                ("Detergente", "Aseo", "litro", 70, 20),
                ("Café", "Alimentos", "kg", 35, 10),
                ("Vajilla", "General", "unidad", 120, 40),
                ("Gel de ducha", "Amenities", "unidad", 200, 70),
            ]
            for pid in propiedades:
                for nombre, categoria, unidad, cantidad, minimo in items:
                    db.add(ItemInventario(
                        nombre=nombre,
                        categoria=categoria,
                        unidad=unidad,
                        cantidad=round(cantidad * RNG.uniform(0.8, 1.2), 2),
                        stock_minimo=minimo,
                        proveedor="Proveedor Norus Luum (demo)",
                        propiedad_id=pid,
                        actualizado_por=SEED,
                        actualizado_en=ahora,
                    ))
            print(f"  · items de inventario: {len(items) * len(propiedades)}")

        if not _ya_cargado(db, ObjetoPerdido, "registrado_por"):
            objetos = ["Llave de habitación", "Cargador", "Gafas", "Documento", "Bolsa"]
            for idx in range(24):
                db.add(ObjetoPerdido(
                    descripcion=f"{RNG.choice(objetos)} · objeto de demostración {idx + 1}",
                    categoria=RNG.choice(["Electrónica", "Ropa", "Documentos", "Otros"]),
                    ubicacion=f"Habitación {RNG.randint(101, 899)}",
                    fecha_encontrado=ahora - timedelta(days=RNG.randint(0, 60)),
                    estado=RNG.choice(["resguardado", "reclamado", "entregado"]),
                    responsable="Recepción",
                    propiedad_id=RNG.choice(propiedades),
                    registrado_por=SEED,
                    creado_en=ahora,
                ))
            print("  · objetos perdidos: 24")

        if not _ya_cargado(db, EjecucionChecklist, "ejecutado_por"):
            plantillas = [
                ("Checklist de llegada", "Recepción", ["Verificar identificación", "Entregar llave", "Confirmar amenities"]),
                ("Checklist de salida", "Housekeeping", ["Revisar habitación", "Reponer amenities", "Cerrar consumo"]),
            ]
            for pid in propiedades:
                for nombre, area, pasos in plantillas:
                    db.add(EjecucionChecklist(
                        nombre=nombre,
                        area=area,
                        elementos=json.dumps(pasos, ensure_ascii=False),
                        estado=RNG.choice(["en_progreso", "completada"]),
                        propiedad_id=pid,
                        ejecutado_por=SEED,
                        creado_en=ahora,
                        completado_en=ahora if RNG.random() > 0.4 else None,
                    ))
            print(f"  · checklists: {len(plantillas) * len(propiedades)}")

        db.commit()
        print("Operativa de demostración cargada. Los datos existentes no fueron modificados.")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"Error al cargar operativa: {exc}", flush=True)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
