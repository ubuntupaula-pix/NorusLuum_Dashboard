# -*- coding: utf-8 -*-
"""Conectores públicos sin clave para referencias de mercado hotelero.

Este módulo solo obtiene y normaliza datos de fuentes públicas verificables.
Nunca inventa precios: cada referencia devuelta conserva fuente, URL, período,
moneda y notas de atribución. La sincronización la inicia explícitamente un
usuario con permisos de edición desde la vista de Mercado.

Controles de seguridad:
  * HTTPS obligatorio y lista blanca de hosts (también aplicada a redirectos).
  * Timeout, límite de tamaño de respuesta y caché en memoria.
  * Sin credenciales ni parámetros controlados por el usuario en las URLs.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from typing import Any

USER_AGENT = "NorusIntelligence/1.0 (public market benchmarks; +https://eahospitalitypulse.com)"
MAX_RESPONSE_BYTES = int(
    os.environ.get("NORUS_MARKET_RESPONSE_MAX_BYTES", str(8 * 1024 * 1024))
)
TIMEOUT_SECONDS = float(os.environ.get("NORUS_MARKET_TIMEOUT_SECONDS", "10"))
CACHE_TTL_SECONDS = int(os.environ.get("NORUS_MARKET_CACHE_SECONDS", "3600"))
MAX_REFERENCES_PER_SOURCE = int(
    os.environ.get("NORUS_MARKET_PUBLIC_MAX_REFERENCES", "500")
)

SOURCES: dict[str, dict[str, str]] = {
    "ea_hospitality_pulse": {
        "nombre": "EA Hospitality Pulse",
        "url": "https://eahospitalitypulse.com/api/v1/rate-observations.json",
        "atribucion": "Datos de EA Hospitality Pulse (https://eahospitalitypulse.com).",
        "licencia": "Uso razonable con atribución y enlace; no reventa ni producto competidor.",
        "metrica": "adr",
        "moneda_origen": "USD",
    },
    "apartments_zuerich": {
        "nombre": "Apartments Zürich",
        "url": "https://www.apartments.zuerich/api/prices",
        "atribucion": "Precios públicos de Apartments Zürich (https://www.apartments.zuerich).",
        "licencia": "API pública de solo lectura, sin clave ni registro.",
        "metrica": "precio",
        "moneda_origen": "CHF",
    },
}

FX_SOURCE = {
    "nombre": "Open Exchange Rate API",
    "url": "https://open.er-api.com/v6/latest/{base}",
    "atribucion": "Tipos de cambio de https://open.er-api.com.",
    "url_publica": "https://open.er-api.com/v6/latest/USD",
}

ALLOWED_HOSTS = {
    "eahospitalitypulse.com",
    "www.eahospitalitypulse.com",
    "apartments.zuerich",
    "www.apartments.zuerich",
    "open.er-api.com",
}

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _validate_url(url: str) -> str:
    """Permite solo HTTPS y hosts de la lista blanca."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Solo se permiten URLs HTTPS de fuentes públicas.")
    host = parsed.hostname.lower().rstrip(".")
    if host not in ALLOWED_HOSTS:
        raise ValueError("El host de la API pública no está autorizado.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("La URL de la API pública no es válida.")
    return url


class _ValidatingRedirect(urllib.request.HTTPRedirectHandler):
    """Impide que un redirect salga de la lista blanca."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        _validate_url(absolute)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def fetch_json(url: str) -> Any:
    """Descarga JSON público con caché, timeout, límite y validación de host."""
    _validate_url(url)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
        if cached and cached[0] > now:
            return cached[1]

    opener = urllib.request.build_opener(_ValidatingRedirect())
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("La respuesta de la API pública supera el tamaño permitido.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("La API pública devolvió JSON inválido.") from exc

    with _CACHE_LOCK:
        _CACHE[url] = (now + max(0, CACHE_TTL_SECONDS), data)
    return data


def clear_cache() -> None:
    """Útil para pruebas y para forzar una recarga manual."""
    with _CACHE_LOCK:
        _CACHE.clear()


def enabled_sources() -> list[str]:
    if os.environ.get("NORUS_MARKET_PUBLIC_ENABLED", "true").strip().lower() in {
        "0", "false", "no", "off",
    }:
        return []
    raw = os.environ.get("NORUS_MARKET_PUBLIC_SOURCES", ",".join(SOURCES))
    return [name.strip() for name in raw.split(",") if name.strip() in SOURCES]


def describe_sources() -> list[dict]:
    enabled = set(enabled_sources())
    items = [
        {
            "clave": key,
            "nombre": cfg["nombre"],
            "url": cfg["url"],
            "metrica": cfg["metrica"],
            "moneda_origen": cfg["moneda_origen"],
            "atribucion": cfg["atribucion"],
            "licencia": cfg["licencia"],
            "activa": key in enabled,
        }
        for key, cfg in SOURCES.items()
    ]
    items.append({
        "clave": "open_er_api",
        "nombre": FX_SOURCE["nombre"],
        "url": FX_SOURCE["url_publica"],
        "metrica": "conversion",
        "moneda_origen": "USD/CHF",
        "atribucion": FX_SOURCE["atribucion"],
        "licencia": "API pública sin clave.",
        "activa": True,
    })
    return items


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _period(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) >= 7 and text[4:5] == "-" and text[:4].isdigit() and text[5:7].isdigit():
        return text[:7]
    if len(text) >= 4 and text[:4].isdigit():
        return text[:4]
    return None


def fx_rate(base: str, quote: str) -> float:
    """Tipo de cambio público hacia la moneda del dashboard."""
    base = base.upper()
    quote = quote.upper()
    if base == quote:
        return 1.0
    url = FX_SOURCE["url"].format(base=urllib.parse.quote(base))
    data = fetch_json(url)
    if not isinstance(data, dict) or data.get("result") != "success":
        raise ValueError("El servicio de tipos de cambio no devolvió un resultado válido.")
    rate = _positive_number((data.get("rates") or {}).get(quote))
    if rate is None:
        raise ValueError(f"No hay tipo de cambio público para {base}/{quote}.")
    return rate


def _ea_references(moneda: str) -> list[dict]:
    cfg = SOURCES["ea_hospitality_pulse"]
    url = cfg["url"]
    data = fetch_json(url)
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("El formato de observaciones de EA Hospitality Pulse cambió.")

    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        rate = _positive_number(row.get("rate_usd"))
        period = _period(row.get("stay_date") or row.get("observed_date"))
        market = str(row.get("market") or "mercado").strip().lower()[:60] or "mercado"
        if rate is not None and period:
            buckets[(period, market)].append(rate)
    if not buckets:
        raise ValueError("La API pública no entregó observaciones de tarifa utilizables.")

    conversion = fx_rate(cfg["moneda_origen"], moneda)
    notes = (
        "Tarifas directas públicas observadas en USD; convención 2 noches, "
        "30 días de anticipación y 2 adultos. Convertido a {moneda} con "
        "Open Exchange Rate API. Atribución: EA Hospitality Pulse "
        "(https://eahospitalitypulse.com)."
    ).format(moneda=moneda)
    refs: list[dict] = []
    ordered = sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1]), reverse=True)
    for (period, market), values in ordered[:MAX_REFERENCES_PER_SOURCE]:
        refs.append({
            "fuente": cfg["nombre"],
            "url": url,
            "tipo": "mercado",
            "metrica": cfg["metrica"],
            "periodo": period,
            "valor": round(sum(values) / len(values) * conversion, 2),
            "moneda": moneda,
            "tipo_habitacion": f"mercado:{market}"[:100],
            "notas": f"{notes} Observaciones: {len(values)}. Destino: {market}.",
        })
    return refs


def _zuerich_references(moneda: str) -> list[dict]:
    cfg = SOURCES["apartments_zuerich"]
    url = cfg["url"]
    data = fetch_json(url)
    prices = data.get("prices") if isinstance(data, dict) else None
    if not isinstance(prices, list):
        raise ValueError("El formato de precios de Apartments Zürich cambió.")
    metadata = data.get("priceMetadata") if isinstance(data, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    origin_currency = str(metadata.get("currency") or cfg["moneda_origen"]).upper()
    period = _period(metadata.get("asOf") or date.today().isoformat()) or date.today().strftime("%Y-%m")
    conversion = fx_rate(origin_currency, moneda)
    notes = (
        "Precio público de larga estancia publicado por Apartments Zürich "
        "({origen}/mes); convertido a {moneda} con Open Exchange Rate API. "
        "API pública de solo lectura, sin clave. Atribución: "
        "https://www.apartments.zuerich."
    ).format(origen=origin_currency, moneda=moneda)

    refs: list[dict] = []
    for item in prices:
        if not isinstance(item, dict):
            continue
        value = _positive_number(item.get("priceLongTerm"))
        if value is None:
            continue
        apartment = str(item.get("apartment") or "unidad").strip()[:60] or "unidad"
        refs.append({
            "fuente": cfg["nombre"],
            "url": url,
            "tipo": "mercado",
            "metrica": cfg["metrica"],
            "periodo": period,
            "valor": round(value * conversion, 2),
            "moneda": moneda,
            "tipo_habitacion": f"largaestancia:{apartment}"[:100],
            "notas": f"{notes} Unidad: {apartment}.",
        })
        if len(refs) >= MAX_REFERENCES_PER_SOURCE:
            break
    if not refs:
        raise ValueError("La API pública no entregó precios de larga estancia utilizables.")
    return refs


def fetch_public_references(
    moneda: str, fuentes: list[str] | None = None
) -> tuple[list[dict], list[dict]]:
    """Obtiene referencias públicas normalizadas y el estado de cada fuente."""
    moneda = str(moneda or "").strip().upper()
    if len(moneda) != 3 or not moneda.isalpha():
        raise ValueError("La moneda debe ser un código ISO de 3 letras.")

    selected = list(fuentes) if fuentes is not None else enabled_sources()
    if not selected:
        raise ValueError("No hay fuentes públicas habilitadas.")
    unknown = [name for name in selected if name not in SOURCES]
    if unknown:
        raise ValueError("Fuente pública desconocida: " + ", ".join(unknown))

    references: list[dict] = []
    statuses: list[dict] = []
    builders = {
        "ea_hospitality_pulse": _ea_references,
        "apartments_zuerich": _zuerich_references,
    }
    for key in selected:
        cfg = SOURCES[key]
        try:
            items = builders[key](moneda)
            references.extend(items)
            statuses.append({
                "clave": key,
                "nombre": cfg["nombre"],
                "estado": "ok",
                "referencias": len(items),
                "detalle": cfg["atribucion"],
            })
        except Exception as exc:  # noqa: BLE001 - una fuente no bloquea a las demás
            statuses.append({
                "clave": key,
                "nombre": cfg["nombre"],
                "estado": "error",
                "referencias": 0,
                "detalle": str(exc)[:300],
            })
    return references, statuses
