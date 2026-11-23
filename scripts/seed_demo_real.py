"""============================================================
    NORUS & LUUM · Demo con datos REALES
    ============================================================
Reemplaza la demo sintética por dos conjuntos verificables:

  1. Reservas hoteleras reales de Antonio, de Almeida & Nunes (2019),
     *Hotel booking demand datasets*, Data in Brief 22, 41-49,
     DOI 10.1016/j.dib.2018.11.126 — CC BY 4.0. 119.390 reservas sin PII.
  2. Referencias de mercado reales de Chile del INE (EMAT), descargadas
     desde el libro oficial publicado por el Instituto Nacional de
     Estadísticas.

La ADR original del dataset está expresada en EUR; se convierte a CLP con el
tipo de cambio REAL de open.er-api.com leído en el momento de la carga. La
capacidad (100 habitaciones por hotel, 20 por tipo) es una declaración de
referencia del demo, no un dato observado, y queda indicada en la carga.

Uso:
    python -m scripts.seed_demo_real --filas 119390
    python -m scripts.seed_demo_real --filas 119390 --db   (usa la BD configurada)
"""
from __future__ import annotations

import argparse
import csv
import io
import tempfile
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app import mercado_publico
from app.database import SessionLocal
from app.models import (
    CargaArchivo,
    CapacidadHabitacion,
    DimCanal,
    DimFecha,
    DimMarca,
    DimNacionalidad,
    DimSegmento,
    DimTipoHabitacion,
    HechoMetricaDiaria,
    HechoMetricaMensual,
    HechoReserva,
)
from scripts.seed_demo import (
    DEMO_CARGA_NOMBRE,
    DIAS,
    MESES,
    _bulk,
    _get_or_create_dim,
    limpiar_demo,
)

DATASET_URL = (
    "https://raw.githubusercontent.com/realliyifei/"
    "ml-project-hotel-cancellation-prediction/main/Dataset/hotel_bookings.csv"
)
DATASET_CITA = (
    "Antonio, N., de Almeida, A. & Nunes, L. (2019). Hotel booking demand "
    "datasets. Data in Brief 22, 41-49. DOI 10.1016/j.dib.2018.11.126. CC BY 4.0."
)
MESES_INGLES = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}
PAISES = {
    "PRT": "Portugal", "GBR": "Reino Unido", "USA": "Estados Unidos",
    "ESP": "España", "DEU": "Alemania", "FRA": "Francia", "ITA": "Italia",
    "BRA": "Brasil", "ARG": "Argentina", "CHL": "Chile", "NLD": "Países Bajos",
    "BEL": "Bélgica", "SWE": "Suecia", "CHE": "Suiza", "AUT": "Austria",
    "IRL": "Irlanda", "POL": "Polonia", "CZE": "Chequia", "DNK": "Dinamarca",
    "FIN": "Finlandia", "NOR": "Noruega", "RUS": "Rusia", "KOR": "Corea del Sur",
    "JPN": "Japón", "AUS": "Australia", "MEX": "México", "COL": "Colombia",
    "PER": "Perú", "URY": "Uruguay", "VEN": "Venezuela", "ZAF": "Sudáfrica",
    "IND": "India", "CHN": "China", "CAN": "Canadá", "NZL": "Nueva Zelanda",
    "LUX": "Luxemburgo", "EST": "Estonia", "HUN": "Hungría", "ISR": "Israel",
    "ROU": "Rumania", "TUR": "Turquía", "UKR": "Ucrania",
}
CANALES = {
    "Direct": ("Directo", "directo"),
    "TA/TO": ("Agencia de viajes", "agencia"),
    "Corporate": ("Corporativo", "corporativo"),
    "GDS": ("GDS", "gds"),
    "Undefined": ("Otro", "otro"),
}
SEGMENTOS = {
    "Direct": "Directo",
    "Corporate": "Corporativo",
    "Online TA": "Online TA",
    "Offline TA/TO": "Offline TA/TO",
    "Groups": "Grupos",
    "Complementary": "Complementario",
    "Aviation": "Aviación",
}
ESTADOS = {
    "Check-Out": "Check-out",
    "Canceled": "Cancelada",
    "No Show": "No show",
}
PROPIEDADES = [
    ("Demo real · City Hotel (CC BY 4.0)", 100),
    ("Demo real · Resort Hotel (CC BY 4.0)", 100),
]
# Los códigos de habitación del dataset (A–P) se agrupan en los cinco tipos
# ya usados por la demo, de modo que el borrado selectivo los reconozca.
TIPOS_REALES = ["Estándar", "Superior", "Deluxe", "Familiar", "Suite"]
CODIGO_TIPO = {
    **{c: "Estándar" for c in "ABC"},
    **{c: "Superior" for c in "DEF"},
    **{c: "Deluxe" for c in "GHI"},
    **{c: "Familiar" for c in "JKL"},
    **{c: "Suite" for c in "MNOP"},
}


def _descargar_csv(destino: Path) -> None:
    req = urllib.request.Request(DATASET_URL, headers={"User-Agent": "NorusIntelligence/2.2"})
    with urllib.request.urlopen(req, timeout=60) as response, destino.open("wb") as archivo:
        while True:
            bloque = response.read(1024 * 1024)
            if not bloque:
                break
            archivo.write(bloque)


def _numero(texto: Any, defecto: float = 0.0) -> float:
    try:
        return float(str(texto or "").strip() or defecto)
    except (TypeError, ValueError):
        return defecto


def _entero(texto: Any, defecto: int = 0) -> int:
    return int(_numero(texto, defecto))


def generar_real(filas: int = 25000, db=None) -> dict[str, Any]:
    """Carga la demo real. Devuelve un resumen con fuentes y filas."""
    propia = db is None
    if propia:
        db = SessionLocal()
    try:
        borrados = limpiar_demo(db)
        tasa = mercado_publico.fx_rate("EUR", "CLP", cache_ttl=0, force=True)
        hoy = date.today().isoformat()
        carga = CargaArchivo(
            nombre_archivo=DEMO_CARGA_NOMBRE,
            formato="csv_real",
            tamano_bytes=0,
            filas_procesadas=0,
            filas_exitosas=0,
            filas_fallidas=0,
            estado="completada",
            mensaje_error=(
                f"{DATASET_CITA} ADR original en EUR convertido a CLP con tipo de cambio "
                f"real de open.er-api.com ({tasa:.4f}) leído el {hoy}. "
                "Capacidad de referencia: 100 habitaciones por hotel (20 por tipo); "
                "no es un dato observado. Referencias de mercado: INE-EMAT (Chile)."
            ),
            usuario="demo_real",
            creado_en=datetime.now(),
        )
        db.add(carga)
        db.commit()
        db.refresh(carga)

        marcas = _get_or_create_dim(
            db, DimMarca,
            [{"nombre": nombre, "habitaciones": habitaciones} for nombre, habitaciones in PROPIEDADES],
            "nombre",
        )
        marca_ids = {m.nombre: m.id for m in marcas}

        # Capacidad declarada explícitamente (transparente en la carga).
        tipos = _get_or_create_dim(
            db, DimTipoHabitacion,
            [{"nombre": nombre} for nombre in TIPOS_REALES],
            "nombre",
        )
        tipo_ids = {t.nombre: t.id for t in tipos}
        existe_cap = {
            (c.marca_id, c.tipo_habitacion_id)
            for c in db.scalars(select(CapacidadHabitacion))
        }
        nuevas_cap = [
            CapacidadHabitacion(
                marca_id=mid, tipo_habitacion_id=tid, habitaciones=20,
                actualizado_por="demo",
            )
            for mid in marca_ids.values() for tid in tipo_ids.values()
            if (mid, tid) not in existe_cap
        ]
        if nuevas_cap:
            db.add_all(nuevas_cap)
            db.commit()

        with tempfile.TemporaryDirectory() as temporal:
            destino = Path(temporal) / "hotel_bookings.csv"
            _descargar_csv(destino)
            with destino.open("r", encoding="utf-8", newline="") as archivo:
                lector = csv.DictReader(archivo)
                filas_reserva: list[dict] = []
                fechas: dict[str, dict] = {}
                canales: dict[str, dict] = {}
                nacionalidades: dict[str, dict] = {}
                segmentos: dict[str, dict] = {}
                agregado: dict[tuple[Any, ...], dict[str, float]] = defaultdict(
                    lambda: {"noches": 0.0, "ingresos": 0.0, "reservas": 0.0}
                )
                errores = 0
                procesadas = 0

                for fila in lector:
                    if procesadas >= filas:
                        break
                    procesadas += 1
                    try:
                        anio = _entero(fila.get("arrival_date_year"))
                        mes = MESES_INGLES[str(fila.get("arrival_date_month") or "").strip()]
                        dia = _entero(fila.get("arrival_date_day_of_month"))
                        fecha = date(anio, mes, dia)
                        noches = _numero(fila.get("stays_in_weekend_nights")) + _numero(
                            fila.get("stays_in_week_nights")
                        )
                        if anio < 1900 or mes < 1 or dia < 1 or noches <= 0:
                            errores += 1
                            continue
                        adr = _numero(fila.get("adr"))
                        if adr < 0:
                            errores += 1
                            continue
                        valor = round(adr * tasa * noches, 2)
                        nombre_prop = (
                            "Demo real · Resort Hotel (CC BY 4.0)"
                            if str(fila.get("hotel") or "").strip() == "Resort Hotel"
                            else "Demo real · City Hotel (CC BY 4.0)"
                        )
                        clave_fecha = fecha.isoformat()
                        fechas[clave_fecha] = {
                            "fecha": clave_fecha,
                            "anio": anio, "mes": mes, "mes_nombre": MESES[mes - 1],
                            "trimestre": (mes - 1) // 3 + 1,
                            "dia_semana": fecha.weekday(), "nombre_dia": DIAS[fecha.weekday()],
                            "es_fin_semana": 1 if fecha.weekday() >= 5 else 0,
                        }
                        canal_nombre, canal_tipo = CANALES.get(
                            str(fila.get("distribution_channel") or "").strip(),
                            ("Otro", "otro"),
                        )
                        canales[canal_nombre] = {"nombre": canal_nombre, "tipo": canal_tipo}
                        codigo_pais = str(fila.get("country") or "UNK").strip()[:3] or "UNK"
                        nombre_pais = PAISES.get(codigo_pais, codigo_pais)
                        nacionalidades[nombre_pais] = {"nombre": nombre_pais, "region": ""}
                        segmento = SEGMENTOS.get(
                            str(fila.get("market_segment") or "").strip(), "Otro"
                        )
                        segmentos[segmento] = {"nombre": segmento}
                        codigo = str(
                            fila.get("assigned_room_type") or fila.get("reserved_room_type") or "A"
                        ).strip()[:1].upper()
                        tipo = CODIGO_TIPO.get(codigo, "Estándar")
                        estado = ESTADOS.get(
                            str(fila.get("reservation_status") or "").strip(), "Confirmada"
                        )
                        cancelada = _entero(fila.get("is_canceled")) == 1
                        if cancelada:
                            estado = "Cancelada"
                        anticipacion = _numero(fila.get("lead_time"))
                        creado = datetime.combine(fecha, datetime.min.time()) - timedelta(days=anticipacion)

                        filas_reserva.append({
                            "codigo": f"REAL-{carga.id}-{procesadas:06d}",
                            "moneda": "CLP",
                            "huesped": None,
                            "habitacion": "",
                            "estado": estado,
                            "valor": valor if not cancelada else 0.0,
                            "noches": noches,
                            "anticipacion_dias": anticipacion,
                            "creado_en": creado,
                            "carga_id": carga.id,
                        })
                        # Las FK se resuelven en bloque más abajo.
                        filas_reserva[-1]["_fecha"] = clave_fecha
                        filas_reserva[-1]["_canal"] = canal_nombre
                        filas_reserva[-1]["_nacionalidad"] = nombre_pais
                        filas_reserva[-1]["_segmento"] = segmento
                        filas_reserva[-1]["_tipo"] = tipo
                        filas_reserva[-1]["_marca"] = nombre_prop

                        if not cancelada:
                            base = (marca_ids[nombre_prop], "CLP", clave_fecha, anio, mes)
                            item = agregado[base]
                            item["noches"] += noches
                            item["ingresos"] += valor
                            item["reservas"] += 1
                    except (ValueError, KeyError, TypeError):
                        errores += 1

            dim_fecha = _get_or_create_dim(db, DimFecha, list(fechas.values()), "fecha")
            dim_canal = _get_or_create_dim(db, DimCanal, list(canales.values()), "nombre")
            dim_nac = _get_or_create_dim(db, DimNacionalidad, list(nacionalidades.values()), "nombre")
            dim_seg = _get_or_create_dim(db, DimSegmento, list(segmentos.values()), "nombre")
            ids_fecha = {f.fecha: f.id for f in dim_fecha}
            ids_canal = {c.nombre: c.id for c in dim_canal}
            ids_nac = {n.nombre: n.id for n in dim_nac}
            ids_seg = {s.nombre: s.id for s in dim_seg}

            for fila in filas_reserva:
                fila["fecha_id"] = ids_fecha[fila.pop("_fecha")]
                fila["canal_id"] = ids_canal[fila.pop("_canal")]
                fila["nacionalidad_id"] = ids_nac[fila.pop("_nacionalidad")]
                fila["segmento_id"] = ids_seg[fila.pop("_segmento")]
                fila["tipo_habitacion_id"] = tipo_ids[fila.pop("_tipo")]
                fila["marca_id"] = marca_ids[fila.pop("_marca")]

            _bulk(db, HechoReserva, filas_reserva)

            diarias: list[dict] = []
            mensuales: dict[tuple[int, int, int, str], dict] = {}
            for (marca_id, moneda, fecha, anio, mes), dato in agregado.items():
                disponibles = 100.0  # capacidad declarada, documentada en la carga
                ocupacion = min(100.0, dato["noches"] / disponibles * 100)
                ing = round(dato["ingresos"], 2)
                noches = round(dato["noches"], 2)
                adr = round(ing / noches, 2) if noches else None
                revpar = round(ing / disponibles, 2)
                diarias.append({
                    "fecha": fecha, "anio": anio, "mes": mes, "marca_id": marca_id,
                    "moneda": moneda, "ocupacion": round(ocupacion, 2), "adr": adr,
                    "revpar": revpar, "ingresos": ing, "noches_vendidas": noches,
                    "habitaciones_disponibles": disponibles, "carga_id": carga.id,
                })
                clave_mes = (anio, mes, marca_id, moneda)
                acum = mensuales.setdefault(clave_mes, {
                    "anio": anio, "mes": mes, "marca_id": marca_id, "moneda": moneda,
                    "noches": 0.0, "ingresos": 0.0, "dias": 0, "carga_id": carga.id,
                })
                acum["noches"] += noches
                acum["ingresos"] += ing
                acum["dias"] += 1
            _bulk(db, HechoMetricaDiaria, diarias)
            filas_mensuales = []
            for acum in mensuales.values():
                dias = acum.pop("dias") or 1
                noches = acum.pop("noches")
                ingresos = acum.pop("ingresos")
                disponibles = 100.0 * dias
                filas_mensuales.append({
                    **acum,
                    "ocupacion": round(min(100.0, noches / disponibles * 100), 2),
                    "adr": round(ingresos / noches, 2) if noches else None,
                    "revpar": round(ingresos / disponibles, 2),
                    "ingresos": round(ingresos, 2),
                    "noches_vendidas": round(noches, 2),
                    "habitaciones_disponibles": disponibles,
                })
            # Reemplazar mensuales de esta carga para evitar duplicados.
            db.execute(
                HechoMetricaMensual.__table__.delete().where(HechoMetricaMensual.carga_id == carga.id)
            )
            _bulk(db, HechoMetricaMensual, filas_mensuales)

        # Referencias de mercado reales (INE-EMAT) para que la comparación
        # funcione desde el primer momento, con URL y período verificables.
        referencias, estados = mercado_publico.fetch_public_references("CLP", ["ine_emat"], force=True)
        from app.mercado import _store_reference

        usuario = type("UsuarioDemo", (), {"username": "demo_real"})()
        for dato in referencias:
            _store_reference(db, dato, None, usuario)
        db.commit()

        carga.filas_procesadas = procesadas
        carga.filas_exitosas = max(0, procesadas - errores)
        carga.filas_fallidas = errores
        carga.estado = "completada"
        db.commit()

        return {
            "activo": True,
            "filas": procesadas,
            "exitosas": max(0, procesadas - errores),
            "fallidas": errores,
            "referencias_mercado": len(referencias),
            "fuentes": estados,
            "borrados": borrados,
            "dataset": DATASET_CITA,
            "fx_eur_clp": round(tasa, 4),
        }
    finally:
        if propia:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga la demo con datos reales verificables.")
    parser.add_argument("--filas", type=int, default=25000, help="Número de reservas a cargar")
    args = parser.parse_args()
    resumen = generar_real(args.filas)
    for clave, valor in resumen.items():
        print(f"{clave}: {valor}")


if __name__ == "__main__":
    main()
