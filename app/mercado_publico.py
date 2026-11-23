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

import io
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
    "ine_emat": {
        "nombre": "INE · EMAT (Chile)",
        "url": (
            "https://www.ine.gob.cl/docs/default-source/actividad-del-turismo/"
            "cuadros-estadisticos-dos/serie-hist%C3%B3rica-metodolog%C3%ADa-2017/"
            "series-mensuales-de-julio-2016-a-la-fecha.xlsx?sfvrsn=a6e68f7_97"
        ),
        "atribucion": (
            "Instituto Nacional de Estadísticas (INE), Encuesta Mensual de "
            "Alojamiento Turístico (EMAT). https://www.ine.gob.cl"
        ),
        "licencia": "Datos abiertos del Estado de Chile; citar fuente INE-EMAT y fecha de descarga.",
        "metrica": "adr",
        "moneda_origen": "CLP",
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
    "ine.gob.cl",
    "www.ine.gob.cl",
}

_CACHE: dict[str, tuple[float, Any]] = {}
_BYTE_CACHE: dict[str, tuple[float, bytes]] = {}
_REFERENCES_CACHE: dict[str, tuple[float, list[dict]]] = {}
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


def fetch_bytes(url: str, cache_ttl: float | None = None, force: bool = False) -> bytes:
    """Descarga un recurso público (JSON o XLSX) con validaciones de seguridad.

    ``cache_ttl`` permite al modo de tiempo real reducir la ventana de caché
    de 1 hora a unos segundos. ``force`` la omite por completo. El valor
    predeterminado se mantiene para no alterar la sincronización manual.
    """
    _validate_url(url)
    ttl = CACHE_TTL_SECONDS if cache_ttl is None else max(0.0, float(cache_ttl))
    now = time.monotonic()
    clave = "bytes:" + url
    with _CACHE_LOCK:
        cached = _BYTE_CACHE.get(clave)
        if cached and not force and cached[0] > now:
            return cached[1]

    opener = urllib.request.build_opener(_ValidatingRedirect())
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*"},
        method="GET",
    )
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("La respuesta de la fuente pública supera el tamaño permitido.")

    with _CACHE_LOCK:
        _BYTE_CACHE[clave] = (now + ttl, raw)
    return raw


def fetch_json(url: str, cache_ttl: float | None = None, force: bool = False) -> Any:
    """Descarga JSON público reutilizando las validaciones de ``fetch_bytes``."""
    raw = fetch_bytes(url, cache_ttl, force)
    ttl = CACHE_TTL_SECONDS if cache_ttl is None else max(0.0, float(cache_ttl))
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
        if cached and not force and cached[0] > now:
            return cached[1]
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("La API pública devolvió JSON inválido.") from exc
    with _CACHE_LOCK:
        _CACHE[url] = (now + ttl, data)
    return data


def _fetch(url: str, cache_ttl: float | None = None, force: bool = False) -> Any:
    """Mantiene la firma simple de ``fetch_json`` para la sincronización manual.

    Los parámetros nuevos solo se envían cuando el llamador (modo tiempo real)
    los necesita, de modo que integraciones y pruebas que sustituyen
    ``fetch_json(url)`` siguen funcionando.
    """
    if cache_ttl is None and not force:
        return fetch_json(url)
    return fetch_json(url, cache_ttl=cache_ttl, force=force)


def clear_cache() -> None:
    """Útil para pruebas y para forzar una recarga manual."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _BYTE_CACHE.clear()
        _REFERENCES_CACHE.clear()


def enabled_sources() -> list[str]:
    if os.environ.get("NORUS_MARKET_PUBLIC_ENABLED", "true").strip().lower() in {
        "0", "false", "no", "off",
    }:
        return []
    # Por defecto solo la fuente oficial chilena: las demás son referencias
    # de otras geografías y deben habilitarse explícitamente si se desean.
    raw = os.environ.get("NORUS_MARKET_PUBLIC_SOURCES", "ine_emat")
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


def fx_rate(
    base: str, quote: str, cache_ttl: float | None = None, force: bool = False
) -> float:
    """Tipo de cambio público hacia la moneda del dashboard."""
    base = base.upper()
    quote = quote.upper()
    if base == quote:
        return 1.0
    url = FX_SOURCE["url"].format(base=urllib.parse.quote(base))
    data = _fetch(url, cache_ttl, force)
    if not isinstance(data, dict) or data.get("result") != "success":
        raise ValueError("El servicio de tipos de cambio no devolvió un resultado válido.")
    rate = _positive_number((data.get("rates") or {}).get(quote))
    if rate is None:
        raise ValueError(f"No hay tipo de cambio público para {base}/{quote}.")
    return rate


def _ea_references(
    moneda: str, cache_ttl: float | None = None, force: bool = False
) -> list[dict]:
    cfg = SOURCES["ea_hospitality_pulse"]
    url = cfg["url"]
    data = _fetch(url, cache_ttl, force)
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

    conversion = fx_rate(cfg["moneda_origen"], moneda, cache_ttl=cache_ttl, force=force)
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


def _zuerich_references(
    moneda: str, cache_ttl: float | None = None, force: bool = False
) -> list[dict]:
    cfg = SOURCES["apartments_zuerich"]
    url = cfg["url"]
    data = _fetch(url, cache_ttl, force)
    prices = data.get("prices") if isinstance(data, dict) else None
    if not isinstance(prices, list):
        raise ValueError("El formato de precios de Apartments Zürich cambió.")
    metadata = data.get("priceMetadata") if isinstance(data, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    origin_currency = str(metadata.get("currency") or cfg["moneda_origen"]).upper()
    period = _period(metadata.get("asOf") or date.today().isoformat()) or date.today().strftime("%Y-%m")
    conversion = fx_rate(origin_currency, moneda, cache_ttl=cache_ttl, force=force)
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


_MESESEMAT = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12,
}
_REGIONESEMAT = {
    "Total nacional",
    "Arica y Parinacota",
    "Coquimbo",
    "Valparaíso",
    "Metropolitana de Santiago",
    "Biobío",
    "La Araucanía",
    "Los Lagos",
}
_HOJASEMAT = {"ocupacion": "16", "revpar": "22", "adr": "25"}


def _periodo_emat(texto: Any) -> str | None:
    """Convierte 'Ago-26/R' o 'Sept-16' en '2026-08' / '2016-09'."""
    limpio = str(texto or "").strip().split("/")[0]
    if "-" not in limpio:
        return None
    mes, anio = limpio.split("-", 1)
    numero = _MESESEMAT.get(mes.strip().lower().rstrip("."))
    anio = anio.strip()
    if numero is None or len(anio) != 2 or not anio.isdigit():
        return None
    return f"{2000 + int(anio):04d}-{numero:02d}"


def _ine_emat_references(
    moneda: str, cache_ttl: float | None = None, force: bool = False
) -> list[dict]:
    """Indicadores reales de ocupación, ADR y RevPAR de Chile (INE-EMAT).

    El libro oficial se descarga tal cual (sin estimaciones) y se extraen las
    filas de regiones seleccionadas. Ocupación es porcentaje; ADR y RevPAR
    vienen en CLP y se convierten con FX real a la moneda pedida.

    El resultado parseado se reutiliza durante la ventana de caché para que
    el cron y varias instantáneas no vuelvan a recorrer el XLSX (34 hojas).
    """
    # La caché de bytes evita descargas; esta evita además el parseo, que es
    # la parte costosa cuando el cron calienta varias monedas/propiedades.
    clave = f"ine_emat|{moneda}"
    ttl = CACHE_TTL_SECONDS if cache_ttl is None else max(0.0, float(cache_ttl))
    ahora = time.monotonic()
    with _CACHE_LOCK:
        guardado = _REFERENCES_CACHE.get(clave)
        if guardado and not force and guardado[0] > ahora:
            return [dict(ref) for ref in guardado[1]]

    from openpyxl import load_workbook  # importado aquí para no gravar el arranque

    cfg = SOURCES["ine_emat"]
    url = cfg["url"]
    conversion = fx_rate("CLP", moneda, cache_ttl=cache_ttl, force=force) if moneda != "CLP" else 1.0
    libro = load_workbook(io.BytesIO(fetch_bytes(url, cache_ttl, force)), read_only=True, data_only=True)
    refs: list[dict] = []
    nota = (
        "INE · Encuesta Mensual de Alojamiento Turístico (EMAT), Chile. "
        "Datos oficiales descargados del libro publicado por el INE; sin estimaciones. "
        "Cifras provisionales/rectificadas según footnote del propio cuadro."
    )
    try:
        for metrica, hoja in _HOJASEMAT.items():
            if hoja not in libro.sheetnames:
                raise ValueError(f"El libro EMAT no contiene el cuadro {hoja} ({metrica}).")
            ws = libro[hoja]
            # iter_rows(values_only=True) lee cada celda una sola vez. El
            # acceso ws.cell(fila, columna) en modo read_only re-parsea la
            # hoja desde el inicio y era ~50 s por invocación.
            fila_encabezado = None
            cabecera: tuple = ()
            periodos: dict[int, str] = {}
            for numero_fila, fila in enumerate(ws.iter_rows(values_only=True), start=1):
                if numero_fila > 20:
                    break
                if fila and "Región" in str(fila[0] or ""):
                    fila_encabezado = numero_fila
                    cabecera = fila
                    periodos = {
                        indice: _periodo_emat(valor)
                        for indice, valor in enumerate(cabecera)
                        if indice >= 1 and _periodo_emat(valor)
                    }
                    break
            if fila_encabezado is None or not periodos:
                raise ValueError(f"No se encontraron períodos en el cuadro EMAT {hoja}.")

            for fila in ws.iter_rows(min_row=fila_encabezado + 1, values_only=True):
                region = str(fila[0] or "").strip() if fila else ""
                if not region:
                    break
                if region not in _REGIONESEMAT:
                    continue
                valores = []
                for indice, periodo in periodos.items():
                    valor = _positive_number(fila[indice] if indice < len(fila) else None)
                    if valor is not None:
                        valores.append((periodo, valor))
                # Solo los últimos 18 períodos publicados, por región y métrica.
                for periodo, valor in sorted(valores)[-18:]:
                    final = round(valor if metrica == "ocupacion" else valor * conversion, 2)
                    refs.append({
                        "fuente": cfg["nombre"],
                        "url": url,
                        "tipo": "mercado",
                        "metrica": metrica,
                        "periodo": periodo,
                        "valor": final,
                        "moneda": moneda,
                        "tipo_habitacion": f"emat:{region}"[:100],
                        "notas": f"{nota} Región: {region}. Cuadro EMAT {hoja}.",
                    })
    finally:
        libro.close()
    if not refs:
        raise ValueError("El libro EMAT no entregó observaciones utilizables.")
    refs = refs[:MAX_REFERENCES_PER_SOURCE]
    with _CACHE_LOCK:
        _REFERENCES_CACHE[clave] = (ahora + ttl, refs)
    return [dict(ref) for ref in refs]


def fetch_public_references(
    moneda: str,
    fuentes: list[str] | None = None,
    cache_ttl: float | None = None,
    force: bool = False,
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
        "ine_emat": _ine_emat_references,
    }
    for key in selected:
        cfg = SOURCES[key]
        try:
            items = builders[key](moneda, cache_ttl=cache_ttl, force=force)
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
