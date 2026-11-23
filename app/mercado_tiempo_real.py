# -*- coding: utf-8 -*-
"""Inteligencia de mercado casi en tiempo real (interno + hoteleras externas).

API dedicada que consolida, en una sola respuesta:

* datos internos del hotel (métricas diarias, reservas recientes y, si se
  configura, el PMS Through un conector HTTPS autenticado);
* datos externos verificables (APIs públicas sin clave y conectores de otras
  hoteleras/OTA definidos en ``NORUS_MARKET_CONNECTORS``);
* análisis comparativo, señales accionables y metadatos de frescura.

"Tiempo real" significa aquí **bajo demanda con caché corta**: las fuentes se
consultan al pedir el dato (o con ``refresh=1``) y se reutilizan unos segundos.
Las APIs públicas publican con su propia latencia; nunca se estima ni se inventa
un precio. Cada valor conserva fuente, URL, período, moneda y hora de lectura.

En Vercel no hay workers persistentes: el último resultado válido se guarda en
``mercado_snapshot`` para sobrevivir a reinicios en frío y degradar con
elegancia si una fuente falla.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import socket
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import mercado_publico
from .auth import SESSION_COOKIE, allowed_property_ids, decode_token, get_current_user, security
from .database import get_db
from .models import DimMarca, HechoMetricaDiaria, HechoReserva, MercadoSnapshot, ReferenciaMercado

router = APIRouter(prefix="/api/mercado", tags=["Mercado en tiempo real"])

CACHE_SECONDS = max(0, int(os.environ.get("NORUS_REALTIME_CACHE_SECONDS", "60")))
PUBLIC_CACHE_SECONDS = max(
    0, int(os.environ.get("NORUS_REALTIME_PUBLIC_CACHE_SECONDS", str(CACHE_SECONDS)))
)
CONNECTOR_TIMEOUT = float(os.environ.get("NORUS_REALTIME_CONNECTOR_TIMEOUT", "5"))
CONNECTOR_MAX_BYTES = int(os.environ.get("NORUS_REALTIME_CONNECTOR_MAX_BYTES", str(2 * 1024 * 1024)))
MAX_CONNECTORS = int(os.environ.get("NORUS_REALTIME_MAX_CONNECTORS", "10"))
USER_AGENT = "NorusIntelligence/2.2 (real-time market analysis)"

METRICAS = {"adr", "revpar", "ocupacion", "precio"}
VENTANAS = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}

_VALUE_KEYS: dict[str, tuple[str, ...]] = {
    "adr": ("adr", "average_daily_rate", "average_rate", "rate_usd", "rate", "tarifa", "precio", "price", "value", "valor"),
    "precio": ("price_long_term", "priceLongTerm", "precio_largo_plazo", "precio", "long_term_price", "price", "tarifa", "rate", "value", "valor"),
    "ocupacion": ("occupancy", "ocupacion", "occupancy_rate", "tasa_ocupacion", "value", "valor"),
    "revpar": ("revpar", "rev_par", "revenue_per_available_room", "value", "valor"),
}
_PERIOD_KEYS = ("stay_date", "fecha", "date", "period", "periodo", "mes", "month", "timestamp", "as_of")
_HOTEL_KEYS = ("hotel", "nombre", "name", "property", "propiedad", "establecimiento", "market", "destino")
_CURRENCY_KEYS = ("currency", "moneda", "divisa")


@dataclass
class EmbedPrincipal:
    """Principal de solo lectura creado a partir de un token de integración."""

    propiedades: list[int] | None
    moneda: str
    parent: str
    username: str = "embed"
    rol: str = "lector"


# ============================================================
# SEGURIDAD DE CONECTORES
# ============================================================

def _public_host(host: str) -> str:
    """Valida HTTPS y bloquea SSRF a direcciones privadas o de metadata."""
    parsed = urllib.parse.urlparse(host)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("El conector debe usar HTTPS.")
    hostname = parsed.hostname.lower().rstrip(".")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("La URL del conector no es válida.")
    if parsed.path in ("", "/") or ".." in parsed.path:
        raise ValueError("La URL del conector debe apuntar a un recurso concreto.")
    if hostname in {"localhost", "metadata", "metadata.google.internal"}:
        raise ValueError("Host del conector no autorizado.")
    if hostname.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Host del conector no autorizado.")

    allowed = {
        item.strip().lower().rstrip(".")
        for item in os.environ.get("NORUS_MARKET_CONNECTOR_HOSTS", "").split(",")
        if item.strip()
    }
    if allowed and hostname not in allowed:
        raise ValueError("El host del conector no está en la lista blanca.")

    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("No se pudo resolver el host del conector.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise ValueError("El conector resuelve a una dirección privada.")
    return hostname


def _load_connectors() -> list[dict[str, Any]]:
    """Lee ``NORUS_MARKET_CONNECTORS`` (JSON) con validación estricta."""
    raw = os.environ.get("NORUS_MARKET_CONNECTORS", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("NORUS_MARKET_CONNECTORS no es JSON válido.") from exc
    if not isinstance(data, list):
        raise ValueError("NORUS_MARKET_CONNECTORS debe ser una lista.")
    connectors: list[dict[str, Any]] = []
    for item in data[:MAX_CONNECTORS]:
        if not isinstance(item, dict):
            raise ValueError("Cada conector debe ser un objeto JSON.")
        url = str(item.get("url") or "").strip()
        _public_host(url)
        clave = str(item.get("clave") or item.get("nombre") or "conector")[:60]
        metrica = str(item.get("metrica") or "adr").strip().lower()
        if metrica not in METRICAS:
            raise ValueError(f"Métrica de conector no válida: {metrica}")
        tipo = str(item.get("tipo") or "hotelero").strip().lower()
        if tipo not in {"hotelero", "interno", "ota"}:
            raise ValueError(f"Tipo de conector no válido: {tipo}")
        moneda_origen = str(item.get("moneda_origen") or "USD").strip().upper()[:3]
        key_env = str(item.get("api_key_env") or "").strip()[:100]
        if key_env and not os.environ.get(key_env, "").strip():
            # Se registra igual: el estado dirá que falta la credencial.
            pass
        connectors.append({
            "clave": clave,
            "nombre": str(item.get("nombre") or clave)[:120],
            "url": url,
            "api_key_env": key_env,
            "metrica": metrica,
            "moneda_origen": moneda_origen,
            "tipo": tipo,
            "hotel": str(item.get("hotel") or "")[:100],
        })
    return connectors


def _pick(row: dict, keys: tuple[str, ...]) -> Any:
    lowered = {str(k).strip().lower().replace("-", "_"): v for k, v in row.items()}
    for key in keys:
        if key in lowered and lowered[key] not in (None, ""):
            return lowered[key]
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", ".").replace("$", "").strip()
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _fetch_json_connector(cfg: dict[str, Any]) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-Norus-Connector": cfg["clave"][:60],
    }
    api_key = os.environ.get(cfg["api_key_env"], "").strip() if cfg["api_key_env"] else ""
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(cfg["url"], headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
    with opener.open(request, timeout=CONNECTOR_TIMEOUT) as response:
        raw = response.read(CONNECTOR_MAX_BYTES + 1)
    if len(raw) > CONNECTOR_MAX_BYTES:
        raise ValueError("La respuesta del conector supera el tamaño permitido.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("El conector devolvió JSON inválido.") from exc


def _connector_references(
    cfg: dict[str, Any], moneda: str, force: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    status = {
        "clave": cfg["clave"],
        "nombre": cfg["nombre"],
        "tipo": cfg["tipo"],
        "estado": "error",
        "referencias": 0,
        "url": cfg["url"],
        "detalle": "",
        "obtenido_en": _iso_now(),
    }
    try:
        if cfg["api_key_env"] and not os.environ.get(cfg["api_key_env"], "").strip():
            raise ValueError(f"Falta la variable de entorno {cfg['api_key_env']}.")
        data = _fetch_json_connector(cfg)
        if isinstance(data, dict):
            rows = data.get("data") or data.get("observations") or data.get("rates") or data.get("prices") or [data]
        else:
            rows = data
        if not isinstance(rows, list):
            raise ValueError("Formato de respuesta no reconocido.")

        fx_cache: dict[str, float] = {}
        refs: list[dict[str, Any]] = []
        for row in rows[:500]:
            if not isinstance(row, dict):
                continue
            valor = _number(_pick(row, _VALUE_KEYS[cfg["metrica"]]))
            if valor is None:
                continue
            periodo = mercado_publico._period(_pick(row, _PERIOD_KEYS)) or datetime.now(UTC).strftime("%Y-%m")
            origen = str(_pick(row, _CURRENCY_KEYS) or cfg["moneda_origen"]).strip().upper()[:3]
            if origen != moneda:
                if origen not in fx_cache:
                    fx_cache[origen] = mercado_publico.fx_rate(
                        origen, moneda, cache_ttl=PUBLIC_CACHE_SECONDS, force=force
                    )
                valor = round(valor * fx_cache[origen], 2)
            hotel = str(_pick(row, _HOTEL_KEYS) or cfg["hotel"] or cfg["nombre"]).strip()[:100]
            refs.append({
                "fuente": cfg["nombre"],
                "url": cfg["url"],
                "tipo": cfg["tipo"],
                "metrica": cfg["metrica"],
                "periodo": periodo,
                "valor": valor,
                "moneda": moneda,
                "tipo_habitacion": hotel,
                "notas": f"Conector {cfg['clave']} · leído {status['obtenido_en']} · conversión desde {origen}.",
                "en_vivo": True,
            })
        if not refs:
            raise ValueError("El conector no entregó valores utilizables.")
        status.update(estado="ok", referencias=len(refs), detalle=f"{len(refs)} observaciones.normalizadas")
        return refs, status
    except Exception as exc:  # noqa: BLE001 - un conector no bloquea a los demás
        status["detalle"] = str(exc)[:300]
        return [], status


# ============================================================
# DATOS INTERNOS
# ============================================================

def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    return round(sum(clean) / len(clean), 2) if clean else None


def _pct(hotel: float | None, market: float | None) -> float | None:
    if hotel is None or market in (None, 0):
        return None
    return round((hotel - market) / market * 100, 2)


def _scope_ids(principal: Any, db: Session, requested: int | None) -> list[int] | None:
    if isinstance(principal, EmbedPrincipal):
        allowed = principal.propiedades
    else:
        allowed = allowed_property_ids(db, principal)
    if requested is not None:
        if allowed is not None and requested not in allowed:
            raise HTTPException(status_code=403, detail="No tiene permisos para esta propiedad.")
        if not db.get(DimMarca, requested):
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
        return [requested]
    return allowed


def _filter_scope(query, model, scope: list[int] | None, requested: int | None):
    if requested is not None:
        return query.filter(model.marca_id == requested)
    if scope is None:
        return query
    if not scope:
        return query.filter(model.marca_id == -1)
    return query.filter(model.marca_id.in_(scope))


def _internal_snapshot(
    db: Session, scope: list[int] | None, requested: int | None, moneda: str, horas: int
) -> dict[str, Any]:
    query = _filter_scope(db.query(HechoMetricaDiaria), HechoMetricaDiaria, scope, requested)
    rows = query.filter(HechoMetricaDiaria.moneda == moneda).all()
    latest: dict[int, HechoMetricaDiaria] = {}
    for row in rows:
        current = latest.get(row.marca_id)
        if current is None or str(row.fecha) > str(current.fecha):
            latest[row.marca_id] = row
    selected = list(latest.values())

    ingresos = sum(r.ingresos for r in selected if r.ingresos is not None) if selected else None
    noches = sum(r.noches_vendidas for r in selected if r.noches_vendidas is not None) if selected else None
    disponibles = sum(r.habitaciones_disponibles for r in selected if r.habitaciones_disponibles is not None) if selected else None

    if ingresos is not None and noches:
        adr = round(ingresos / noches, 2)
    else:
        adr = _mean([r.adr for r in selected if r.adr is not None])
    if disponibles:
        ocupacion = round(noches / disponibles * 100, 2) if noches is not None else None
        revpar = round(ingresos / disponibles, 2) if ingresos is not None else None
    else:
        ocupacion = _mean([r.ocupacion for r in selected if r.ocupacion is not None])
        revpar = _mean([r.revpar for r in selected if r.revpar is not None])

    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    since = now - timedelta(hours=horas)
    previous = since - timedelta(hours=horas)

    def booking_count(start: datetime, end: datetime) -> tuple[int, float, float]:
        q = _filter_scope(db.query(HechoReserva), HechoReserva, scope, requested)
        q = q.filter(
            HechoReserva.moneda == moneda,
            HechoReserva.creado_en >= start,
            HechoReserva.creado_en < end,
        )
        count, nights, value = q.with_entities(
            func.count(HechoReserva.id),
            func.coalesce(func.sum(HechoReserva.noches), 0),
            func.coalesce(func.sum(HechoReserva.valor), 0),
        ).one()
        return int(count or 0), float(nights or 0), float(value or 0)

    recientes, noches_recientes, valor_reciente = booking_count(since, now + timedelta(seconds=1))
    anteriores, _, _ = booking_count(previous, since)
    pickup = round((recientes - anteriores) / anteriores * 100, 2) if anteriores else None

    return {
        "ocupacion": ocupacion,
        "adr": adr,
        "revpar": revpar,
        "ingresos": round(ingresos, 2) if ingresos is not None else None,
        "noches_vendidas": round(noches, 2) if noches is not None else None,
        "reservas_recientes": recientes,
        "noches_recientes": round(noches_recientes, 2),
        "valor_reciente": round(valor_reciente, 2),
        "reservas_periodo_anterior": anteriores,
        "variacion_reservas_pct": pickup,
        "fecha_metrica": max((str(r.fecha) for r in selected), default=None),
        "ventana_horas": horas,
        "fuentes": [{
            "clave": "warehouse_interno",
            "nombre": "Data warehouse Norus",
            "tipo": "interno",
            "estado": "ok" if selected else "sin_datos",
            "referencias": len(selected),
            "detalle": "HechoMetricaDiaria + HechoReserva (creado_en)",
            "obtenido_en": _iso_now(),
        }],
    }


def _registered_market(
    db: Session, scope: list[int] | None, requested: int | None, moneda: str
) -> list[dict[str, Any]]:
    """Últimas referencias registradas: respaldo verificable cuando falla el directo."""
    desde = (datetime.now(UTC) - timedelta(days=180)).strftime("%Y-%m")
    query = _filter_scope(db.query(ReferenciaMercado), ReferenciaMercado, scope, requested)
    rows = query.filter(
        ReferenciaMercado.moneda == moneda,
        ReferenciaMercado.periodo >= desde,
    ).all()
    refs: list[dict[str, Any]] = []
    for row in rows:
        refs.append({
            "fuente": row.fuente,
            "url": row.url or "",
            "tipo": "registrada",
            "metrica": row.metrica,
            "periodo": row.periodo,
            "valor": float(row.valor or 0),
            "moneda": row.moneda,
            "tipo_habitacion": row.tipo_habitacion or "",
            "notas": row.notas or "Referencia registrada/importada previamente.",
            "en_vivo": False,
        })
    return refs


# ============================================================
# ANÁLISIS
# ============================================================

def _analyze(
    interno: dict[str, Any],
    refs: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    moneda: str,
    propiedad_id: int | None,
    nombres: dict[int, str],
) -> dict[str, Any]:
    def by_metric(metrica: str) -> list[float]:
        return [float(r["valor"]) for r in refs if r.get("metrica") == metrica and r.get("valor") is not None]

    adr_mercado = _mean(by_metric("adr"))
    precio_mercado = _mean(by_metric("precio"))
    ocupacion_mercado = _mean(by_metric("ocupacion"))
    revpar_mercado = _mean(by_metric("revpar"))

    brecha_adr = _pct(interno.get("adr"), adr_mercado)
    brecha_precio = _pct(interno.get("adr"), precio_mercado)
    brecha_occ = (
        round(interno["ocupacion"] - ocupacion_mercado, 2)
        if interno.get("ocupacion") is not None and ocupacion_mercado is not None
        else None
    )
    brecha_revpar = _pct(interno.get("revpar"), revpar_mercado)

    senales: list[dict[str, str]] = []
    if brecha_adr is not None:
        if brecha_adr >= 10:
            senales.append({
                "severidad": "alerta",
                "codigo": "adr_sobre_mercado",
                "mensaje": f"ADR {brecha_adr:+.1f}% sobre el mercado; revisar competitividad de tarifa.",
            })
        elif brecha_adr <= -10:
            senales.append({
                "severidad": "oportunidad",
                "codigo": "adr_bajo_mercado",
                "mensaje": f"ADR {abs(brecha_adr):.1f}% bajo el mercado; existe margen para subir tarifas.",
            })
        else:
            senales.append({
                "severidad": "ok",
                "codigo": "adr_alineado",
                "mensaje": "ADR alineado con el mercado (±10%).",
            })
    if brecha_occ is not None and brecha_occ <= -5:
        senales.append({
            "severidad": "alerta",
            "codigo": "ocupacion_baja",
            "mensaje": f"Ocupación {abs(brecha_occ):.1f} pp por debajo del mercado.",
        })
    if interno.get("variacion_reservas_pct") is not None and interno["variacion_reservas_pct"] >= 20:
        senales.append({
            "severidad": "oportunidad",
            "codigo": "pickup_alto",
            "mensaje": f"Pickup de reservas {interno['variacion_reservas_pct']:+.1f}% versus período anterior.",
        })

    live = [s for s in statuses if s.get("estado") == "ok"]
    errores = [s for s in statuses if s.get("estado") == "error"]
    if not live:
        senales.insert(0, {
            "severidad": "alerta",
            "codigo": "sin_datos_en_vivo",
            "mensaje": "No hay fuentes externas disponibles en este momento; se muestran referencias registradas.",
        })
    elif errores:
        senales.append({
            "severidad": "info",
            "codigo": "fuentes_parciales",
            "mensaje": "Fuentes sin respuesta: " + ", ".join(s["nombre"] for s in errores) + ".",
        })

    exitosos = [s for s in statuses if s.get("estado") == "ok"]
    estado = "en_vivo" if len(exitosos) >= 2 and not errores else ("parcial" if exitosos else "sin_datos")
    propiedades = (
        [{"id": propiedad_id, "nombre": nombres.get(propiedad_id, "")}]
        if propiedad_id and nombres.get(propiedad_id)
        else [
            {"id": pid, "nombre": nombre}
            for pid, nombre in sorted(nombres.items())
        ]
    )

    return {
        "generado_en": _iso_now(),
        "estado": estado,
        "moneda": moneda,
        "propiedad_id": propiedad_id,
        "propiedades": propiedades,
        "cache": "miss",
        "cache_segundos": CACHE_SECONDS,
        "antiguedad_segundos": 0,
        "interno": interno,
        "externo": {
            "adr": adr_mercado,
            "precio_largo_plazo": precio_mercado,
            "ocupacion": ocupacion_mercado,
            "revpar": revpar_mercado,
            "observaciones": len(refs),
            "origen": "en_vivo" if any(r.get("en_vivo") for r in refs) else "referencias_registradas",
            "fuentes": [s for s in statuses if s.get("tipo") != "interno"],
            "referencias": refs[:80],
        },
        "comparativa": {
            "brecha_adr_pct": brecha_adr,
            "brecha_precio_largo_plazo_pct": brecha_precio,
            "brecha_ocupacion_pp": brecha_occ,
            "brecha_revpar_pct": brecha_revpar,
            "posicionamiento": (
                "sobre_mercado" if brecha_adr is not None and brecha_adr > 5
                else "bajo_mercado" if brecha_adr is not None and brecha_adr < -5
                else "alineado" if brecha_adr is not None
                else "sin_datos"
            ),
        },
        "senales": senales,
        "nota": (
            "Datos internos del warehouse/PMS y referencias públicas o de hoteleras "
            "conectadas por API, con fuente, URL, período, moneda y hora de lectura. "
            "Caché corta bajo demanda: las fuentes públicas publican con su propia "
            "latencia. Los períodos sin dato se devuelven como null; nunca se estiman precios."
        ),
    }


# ============================================================
# ENDPOINT
# ============================================================

def analysis_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    norus_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    """Acepta sesión/cookie, Bearer de acceso o token de integración embebible."""
    if credentials:
        embed = decode_token(credentials.credentials, expected_scope="embed")
        if embed:
            propiedades = embed.get("propiedades")
            return EmbedPrincipal(
                propiedades=[int(p) for p in propiedades] if isinstance(propiedades, list) else None,
                moneda=str(embed.get("moneda") or "CLP"),
                parent=str(embed.get("parent") or ""),
            )
    return get_current_user(
        credentials=credentials, norus_session=norus_session, db=db
    )


def build_snapshot(
    db: Session,
    principal: Any,
    propiedad_id: int | None,
    moneda: str,
    ventana: str,
    refresh: bool,
) -> dict[str, Any]:
    """Construye (o reutiliza) el análisis comparativo de una sola llamada.

    Se separa del endpoint HTTP para que el programador diario de Vercel pueda
    calentar las mismas instantáneas sin montar una petición HTTP interna.
    """
    scope = _scope_ids(principal, db, propiedad_id)
    moneda = moneda.upper()
    horas = VENTANAS[ventana]
    scope_key = f"{moneda}|{propiedad_id or 'all'}|{horas}"
    snapshot = db.query(MercadoSnapshot).filter(MercadoSnapshot.scope_key == scope_key).first()
    anterior: dict[str, Any] | None = None

    def age_of(row: MercadoSnapshot) -> float:
        updated = row.actualizado_en
        if updated is None:
            return float("inf")
        if updated.tzinfo is None:
            return (datetime.now(UTC).replace(tzinfo=None) - updated).total_seconds()
        return (datetime.now(UTC) - updated).total_seconds()

    if snapshot:
        try:
            anterior = json.loads(snapshot.payload)
        except (TypeError, json.JSONDecodeError):
            anterior = None
        age = age_of(snapshot)
        if anterior and not refresh and age <= CACHE_SECONDS:
            payload = dict(anterior)
            payload.update(cache="hit", antiguedad_segundos=round(max(age, 0)))
            return payload

    nombres = {
        p.id: p.nombre
        for p in db.query(DimMarca).filter(DimMarca.id.in_(scope or [propiedad_id])).all()
    } if (scope or propiedad_id) else {}

    interno = _internal_snapshot(db, scope, propiedad_id, moneda, horas)
    statuses: list[dict[str, Any]] = list(interno["fuentes"])
    refs: list[dict[str, Any]] = []

    conectores = _load_connectors()
    internos = [c for c in conectores if c["tipo"] == "interno"]
    externos = [c for c in conectores if c["tipo"] != "interno"]

    tareas = []
    with ThreadPoolExecutor(max_workers=max(2, min(4, 1 + len(externos) + (1 if externos else 0)))) as pool:
        if mercado_publico.enabled_sources():
            tareas.append((
                "publicas",
                pool.submit(
                    mercado_publico.fetch_public_references,
                    moneda,
                    None,
                    PUBLIC_CACHE_SECONDS,
                    refresh,
                ),
            ))
        for cfg in externos:
            tareas.append((cfg["clave"], pool.submit(_connector_references, cfg, moneda, refresh)))
        for cfg in internos:
            tareas.append((cfg["clave"], pool.submit(_connector_references, cfg, moneda, refresh)))

        for nombre, future in tareas:
            try:
                resultado = future.result(timeout=CONNECTOR_TIMEOUT + 5)
            except Exception as exc:  # noqa: BLE001
                resultado = ([], {
                    "clave": nombre, "nombre": nombre, "tipo": "hotelero",
                    "estado": "error", "referencias": 0, "url": "",
                    "detalle": str(exc)[:300], "obtenido_en": _iso_now(),
                })
            items, status = resultado
            if nombre == "publicas":
                # Se leyeron en esta invocación (o con la caché corta de 60 s).
                for ref in items:
                    ref["en_vivo"] = True
            refs.extend(items)
            # Las fuentes públicas devuelven un estado por fuente; los
            # conectores devuelven un único diccionario.
            if isinstance(status, list):
                statuses.extend(status)
            else:
                statuses.append(status)

    # Conector PMS interno: sobrescribe las métricas internas del warehouse.
    for ref in refs:
        if ref.get("tipo") != "interno":
            continue
        destino = {"adr": "adr", "ocupacion": "ocupacion", "revpar": "revpar", "precio": "adr"}.get(ref["metrica"])
        if destino:
            interno[destino] = float(ref["valor"])
            interno.setdefault("origen_metricas", "conector_pms")

    externas = [r for r in refs if r.get("tipo") != "interno"]
    registradas = _registered_market(db, scope, propiedad_id, moneda)
    if not externas:
        externas = registradas
        statuses.append({
            "clave": "referencias_registradas",
            "nombre": "Referencias registradas",
            "tipo": "mercado",
            "estado": "ok" if registradas else "sin_datos",
            "referencias": len(registradas),
            "url": "",
            "detalle": "Respaldo: referencias manuales/importadas de los últimos 180 días.",
            "obtenido_en": _iso_now(),
        })

    payload = _analyze(interno, externas, statuses, moneda, propiedad_id, nombres)

    if not any(s.get("estado") == "ok" for s in statuses if s.get("tipo") != "interno") and anterior:
        degraded = dict(anterior)
        degraded.update({
            "cache": "stale",
            "estado": "historico",
            "antiguedad_segundos": round(max(age_of(snapshot), 0)),
            "generado_en": anterior.get("generado_en"),
        })
        senales = list(degraded.get("senales") or [])
        senales.insert(0, {
            "severidad": "alerta",
            "codigo": "ultimo_valido",
            "mensaje": "Las fuentes externas fallaron; se muestra el último análisis válido.",
        })
        degraded["senales"] = senales
        return degraded

    if snapshot is None:
        snapshot = MercadoSnapshot(scope_key=scope_key, origen="tiempo_real")
        db.add(snapshot)
    snapshot.payload = json.dumps(payload, ensure_ascii=False, default=str)
    snapshot.origen = "tiempo_real"
    db.commit()
    return payload


@router.get("/tiempo-real")
def tiempo_real(
    propiedad_id: int | None = Query(None, ge=1),
    moneda: str = Query("CLP", pattern=r"^[A-Z]{3}$"),
    ventana: str = Query("24h", pattern=r"^(24h|7d|30d)$"),
    refresh: bool = Query(False, description="Fuerza lectura de las fuentes externas."),
    db: Session = Depends(get_db),
    principal: Any = Depends(analysis_principal),
):
    """Compara el desempeño interno con el mercado externo en el momento."""
    return build_snapshot(db, principal, propiedad_id, moneda, ventana, refresh)
