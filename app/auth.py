# -*- coding: utf-8 -*-
"""============================================================
   NORUS & LUUM · Autenticación JWT + bcrypt + MFA + alcance
   Login seguro con tokens, roles, CSRF y permisos por propiedad.
   ============================================================"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, UTC

from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import bcrypt
from sqlalchemy.orm import Session

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

from .database import get_db
from .models import Usuario, UsuarioPropiedad, DimMarca

# ============================================================
# CONFIGURACIÓN (variables de entorno en producción)
# ============================================================

ENVIRONMENT = os.environ.get("NORUS_ENV", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"
SECRET_KEY = os.environ.get("NORUS_JWT_SECRET")
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise RuntimeError("NORUS_JWT_SECRET debe configurarse antes de iniciar en producción.")
    # A development key is unique to this process and is never valid for deployment.
    SECRET_KEY = secrets.token_urlsafe(48)
if IS_PRODUCTION and len(SECRET_KEY.encode("utf-8")) < 32:
    raise RuntimeError("NORUS_JWT_SECRET debe tener al menos 32 bytes en producción.")

ALGORITHM = "HS256"
JWT_ISSUER = os.environ.get("NORUS_JWT_ISSUER", "norus-intelligence")
JWT_AUDIENCE = os.environ.get("NORUS_JWT_AUDIENCE", "norus-dashboard")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("NORUS_TOKEN_EXPIRY", "480"))
MFA_TOKEN_EXPIRE_MINUTES = 5
# Tokens de solo lectura para incrustar el panel en otra aplicación web.
EMBED_TOKEN_EXPIRE_MINUTES = max(
    1, min(int(os.environ.get("NORUS_EMBED_TOKEN_EXPIRY", "1440")), 10080)
)
SESSION_COOKIE = "norus_session"
CSRF_COOKIE = "norus_csrf"
CSRF_HEADER = "X-CSRF-Token"
MIN_PASSWORD_LENGTH = int(os.environ.get("NORUS_PASSWORD_MIN_LENGTH", "12"))

# Esquema de seguridad HTTP Bearer (compatibilidad con clientes API)
security = HTTPBearer(auto_error=False)


def cookie_secure() -> bool:
    explicit = os.environ.get("NORUS_COOKIE_SECURE")
    if explicit is not None:
        return explicit.lower() == "true"
    return IS_PRODUCTION


# ============================================================
# HASH DE CONTRASEÑAS (bcrypt directo)
# ============================================================

def hash_password(password: str) -> str:
    """Genera hash bcrypt de una contraseña."""
    if len(password.encode("utf-8")) > 72:
        raise ValueError("La contraseña no puede superar 72 bytes UTF-8.")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verifica una contraseña contra su hash (tiempo constante)."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# Hash de relleno para evitar diferencias de tiempo cuando el usuario no existe.
# Usa las mismas rondas que las contraseñas reales para no filtrar la existencia
# de la cuenta mediante el tiempo de respuesta.
_DUMMY_HASH = bcrypt.hashpw(b"norus-dummy-password", bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password_dummy(password: str) -> None:
    """Consume el mismo tiempo que una verificación real y descarta el resultado."""
    try:
        bcrypt.checkpw(password.encode("utf-8")[:72], _DUMMY_HASH.encode("utf-8"))
    except Exception:
        pass


def validate_password_strength(password: str) -> None:
    """Política mínima para contraseñas nuevas: longitud, variedad y sin triviales."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.",
        )
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="La contraseña no puede superar 72 bytes UTF-8.")
    categories = sum(bool(re.search(pattern, password)) for pattern in (
        r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]",
    ))
    if categories < 3:
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe combinar mayúsculas, minúsculas, números o símbolos (al menos 3 tipos).",
        )
    normalized = password.casefold()
    weak = ("password", "contraseña", "123456", "qwerty", "admin", "norus", "luum")
    if any(item in normalized for item in weak):
        raise HTTPException(status_code=400, detail="La contraseña contiene una palabra demasiado común.")


# ============================================================
# JWT
# ============================================================

def _base_claims(scope: str) -> dict:
    now = datetime.now(UTC)
    return {
        "iat": now,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "jti": secrets.token_urlsafe(16),
        "scope": scope,
    }


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Crea un token JWT de acceso con datos del usuario y versión de sesión."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update(_base_claims("access"))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_mfa_token(username: str, token_version: int) -> str:
    """Token efímero que solo autoriza completar el segundo factor."""
    payload = {
        "sub": username,
        "ver": int(token_version or 0),
        "exp": datetime.now(UTC) + timedelta(minutes=MFA_TOKEN_EXPIRE_MINUTES),
    }
    payload.update(_base_claims("mfa"))
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def embed_allowed_origins() -> list[str]:
    """Orígenes autorizados a incrustar el panel (lista blanca exacta)."""
    return [
        value.strip().rstrip("/")
        for value in os.environ.get("NORUS_EMBED_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    ]


def create_embed_token(
    propiedades: list[int] | None,
    moneda: str,
    parent_origin: str,
    minutos: int | None = None,
) -> tuple[str, int]:
    """Emite un JWT de solo lectura para el widget embebible.

    El token viaja en el fragmento de la URL (``#token=...``), nunca en la
    consulta: así no aparece en logs del servidor, proxies ni historial.
    """
    ttl = min(max(int(minutos or EMBED_TOKEN_EXPIRE_MINUTES), 1), EMBED_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": "embed",
        "ver": 0,
        "propiedades": sorted({int(p) for p in propiedades}) if propiedades is not None else None,
        "moneda": moneda,
        "parent": parent_origin.rstrip("/"),
        "exp": datetime.now(UTC) + timedelta(minutes=ttl),
    }
    payload.update(_base_claims("embed"))
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM), ttl


def decode_token(token: str, expected_scope: str | None = "access") -> dict | None:
    """Decodifica y valida un token JWT."""
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if expected_scope is not None and payload.get("scope", "access") != expected_scope:
        return None
    return payload


# ============================================================
# CSRF (double-submit cookie) y cookies de sesión
# ============================================================

def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str | None = None) -> str:
    value = token or new_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE,
        value=value,
        httponly=False,  # JavaScript debe leerlo para enviarlo en la cabecera
        secure=cookie_secure(),
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return value


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=cookie_secure()
    )
    response.delete_cookie(CSRF_COOKIE, path="/", samesite="lax", secure=cookie_secure())


def csrf_is_valid(request: Request) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    return bool(cookie and header and hmac.compare_digest(cookie, header))


# ============================================================
# DEPENDENCIAS DE FASTAPI
# ============================================================

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    norus_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Usuario:
    """Obtiene el usuario autenticado desde el token JWT (header o cookie)."""
    token = credentials.credentials if credentials else norus_session
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionó token de autenticación.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str | None = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token sin usuario válido.")

    user = db.query(Usuario).filter(Usuario.username == username, Usuario.activo == 1).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado o inactivo.")
    # Invalida tokens emitidos antes de logout/cambio de contraseña.
    if int(payload.get("ver", 0)) != int(user.token_version or 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión revocada.")

    return user


def optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    norus_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Usuario | None:
    """Variante que no lanza 401; usada para decidir si servir el dashboard."""
    token = credentials.credentials if credentials else norus_session
    if not token:
        return None
    payload = decode_token(token)
    if payload is None:
        return None
    username = payload.get("sub")
    if not username:
        return None
    user = db.query(Usuario).filter(Usuario.username == username, Usuario.activo == 1).first()
    if user is None or int(payload.get("ver", 0)) != int(user.token_version or 0):
        return None
    return user


def require_admin(current_user: Usuario = Depends(get_current_user)) -> Usuario:
    if current_user.rol != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Se requiere rol de administrador.")
    return current_user


def require_editor(current_user: Usuario = Depends(get_current_user)) -> Usuario:
    if current_user.rol not in ("admin", "editor"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere rol de editor o administrador.",
        )
    return current_user


# ============================================================
# PERMISOS POR PROPIEDAD
# ============================================================

def allowed_property_ids(db: Session, user: Usuario) -> list[int] | None:
    """None significa acceso global; una lista (posiblemente vacía) limita el alcance."""
    if user.rol == "admin" or int(user.acceso_todas_propiedades or 0) == 1:
        return None
    rows = (
        db.query(UsuarioPropiedad.marca_id)
        .filter(UsuarioPropiedad.usuario_id == user.id)
        .all()
    )
    return sorted({int(row[0]) for row in rows})


def scope_property_ids(db: Session, user: Usuario, requested: int | None = None) -> list[int] | None:
    """Combina el alcance del usuario con un filtro explícito de propiedad."""
    allowed = allowed_property_ids(db, user)
    if requested is not None:
        if allowed is not None and requested not in allowed:
            raise HTTPException(status_code=403, detail="No tiene permisos para esta propiedad.")
        return [requested]
    return allowed


def can_access_property(db: Session, user: Usuario, property_id: int | None) -> bool:
    allowed = allowed_property_ids(db, user)
    if allowed is None:
        return True
    if property_id is None:
        return False
    return int(property_id) in allowed


def assert_property_access(db: Session, user: Usuario, property_id: int | None) -> None:
    if not can_access_property(db, user, property_id):
        raise HTTPException(status_code=403, detail="No tiene permisos para esta propiedad.")


def default_property_id(db: Session, user: Usuario) -> int | None:
    allowed = allowed_property_ids(db, user)
    if allowed is None:
        return None
    return allowed[0] if allowed else None


def properties_for_user(db: Session, user: Usuario) -> list[DimMarca]:
    allowed = allowed_property_ids(db, user)
    query = db.query(DimMarca)
    if allowed is not None:
        query = query.filter(DimMarca.id.in_(allowed)) if allowed else query.filter(DimMarca.id == -1)
    return query.order_by(DimMarca.nombre).all()
