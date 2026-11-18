# -*- coding: utf-8 -*-
"""Optional OpenID Connect (SSO) integration.

Configure ``NORUS_SSO_ISSUER``, ``NORUS_SSO_CLIENT_ID`` and
``NORUS_SSO_CLIENT_SECRET`` (and optionally ``NORUS_SSO_REDIRECT_URI``).
When no issuer is configured the feature is disabled and the UI hides it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import jwt

from .auth import JWT_AUDIENCE, JWT_ISSUER, SECRET_KEY
from .auth import IS_PRODUCTION

_DISCOVERY_CACHE: dict[str, Any] = {"url": None, "data": None, "at": 0.0}


class SSOError(Exception):
    pass


def sso_enabled() -> bool:
    return bool(
        os.environ.get("NORUS_SSO_ISSUER", "").strip()
        and os.environ.get("NORUS_SSO_CLIENT_ID", "").strip()
        and os.environ.get("NORUS_SSO_CLIENT_SECRET", "").strip()
    )


def _issuer() -> str:
    return os.environ.get("NORUS_SSO_ISSUER", "").strip().rstrip("/")


def _client_id() -> str:
    return os.environ.get("NORUS_SSO_CLIENT_ID", "").strip()


def _client_secret() -> str:
    return os.environ.get("NORUS_SSO_CLIENT_SECRET", "").strip()


def _redirect_uri() -> str:
    explicit = os.environ.get("NORUS_SSO_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("NORUS_PUBLIC_URL", "").strip().rstrip("/")
    if base:
        return base + "/api/auth/sso/callback"
    raise SSOError("Define NORUS_SSO_REDIRECT_URI o NORUS_PUBLIC_URL.")


def _http_json(url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: int = 10):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        raise SSOError(f"No fue posible contactar al proveedor SSO: {exc}") from exc


def discovery() -> dict:
    issuer = _issuer()
    if not issuer:
        raise SSOError("SSO no configurado.")
    now = time.time()
    if _DISCOVERY_CACHE["url"] == issuer and _DISCOVERY_CACHE["data"] and now - _DISCOVERY_CACHE["at"] < 3600:
        return _DISCOVERY_CACHE["data"]
    url = issuer + "/.well-known/openid-configuration"
    data = _http_json(url)
    if not data.get("authorization_endpoint") or not data.get("token_endpoint") or not data.get("jwks_uri"):
        raise SSOError("El documento de descubrimiento OIDC está incompleto.")
    _DISCOVERY_CACHE.update({"url": issuer, "data": data, "at": now})
    return data


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def create_state() -> tuple[str, str]:
    """Return (signed_state_payload, authorize_url)."""
    config = discovery()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    redirect_uri = _redirect_uri()
    payload = {
        "typ": "sso_state",
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "exp": int(time.time()) + 600,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    signed = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    params = {
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "scope": os.environ.get("NORUS_SSO_SCOPES", "openid email profile"),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return signed, config["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)


def read_state(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["exp", "state", "nonce", "verifier"]},
        )
    except jwt.PyJWTError as exc:
        raise SSOError("Estado de SSO inválido o expirado.") from exc
    if payload.get("typ") != "sso_state":
        raise SSOError("Estado de SSO inválido.")
    return payload


def _signing_key(jwks: dict, kid: str | None):
    keys = jwks.get("keys") or []
    for item in keys:
        if not kid or item.get("kid") == kid:
            try:
                if item.get("kty") == "RSA":
                    return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(item))
                if item.get("kty") == "EC":
                    return jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(item))
            except Exception:
                continue
    raise SSOError("No se encontró una clave pública válida para validar el token de identidad.")


def exchange_code(code: str, state_payload: dict) -> dict:
    config = discovery()
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": state_payload["redirect_uri"],
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "code_verifier": state_payload["verifier"],
    }).encode("utf-8")
    token_data = _http_json(
        config["token_endpoint"],
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    id_token = token_data.get("id_token")
    if not id_token:
        raise SSOError("El proveedor no devolvió un token de identidad.")

    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise SSOError("Token de identidad ilegible.") from exc
    algorithm = header.get("alg")
    if algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}:
        raise SSOError("Algoritmo de firma no permitido.")
    jwks = _http_json(config["jwks_uri"])
    key = _signing_key(jwks, header.get("kid"))
    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[algorithm],
            audience=_client_id(),
            issuer=config.get("issuer") or _issuer(),
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise SSOError("Token de identidad inválido.") from exc
    if claims.get("nonce") != state_payload.get("nonce"):
        raise SSOError("Nonce de SSO inválido.")
    if claims.get("email_verified") is False:
        raise SSOError("El proveedor no ha verificado el correo de la cuenta.")
    return claims
