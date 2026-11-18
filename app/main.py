# -*- coding: utf-8 -*-
"""============================================================
   NORUS & LUUM · Dashboard Comercial y de Gestión
   Servidor FastAPI — API comercial y operativa

   Endpoints:
   - /api/auth/*              → Autenticación, MFA y SSO
   - /api/dashboard           → Datos del dashboard
   - /api/reservas            → CRUD de reservas
   - /api/importar            → ETL de archivos
   - /api/auditoria           → Eventos de auditoría
   - /api/cargas              → Historial de cargas
   - /api/usuarios            → Administración de usuarios
   ============================================================"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from .auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    CSRF_COOKIE,
    CSRF_HEADER,
    IS_PRODUCTION,
    SECRET_KEY,
    SESSION_COOKIE,
    allowed_property_ids,
    assert_property_access,
    clear_session_cookies,
    cookie_secure,
    create_access_token,
    create_mfa_token,
    csrf_is_valid,
    decode_token,
    get_current_user,
    hash_password,
    optional_current_user,
    properties_for_user,
    require_admin,
    require_editor,
    set_csrf_cookie,
    set_session_cookie,
    validate_password_strength,
    verify_password,
)
from .crypto import (
    backup_codes_json,
    consume_backup_code,
    encryption_active,
    generate_backup_codes,
    generate_totp_secret,
    totp_uri,
    verify_totp,
)
from .dashboard import obtener_dashboard
from .database import database_kind, get_db
from .etl import ejecutar_carga
from .habitaciones import router as habitaciones_router
from .mercado import router as mercado_router
from .models import (
    CargaArchivo,
    DimMarca,
    EventoAuditoria,
    HechoReserva,
    IntentoLogin,
    Usuario,
    UsuarioPropiedad,
)
from .schemas import (
    CargaArchivoResponse,
    DashboardData,
    EventoAuditoriaResponse,
    ImportacionResponse,
    LoginRequest,
    LoginResponse,
    MFADisableRequest,
    MFAEnableRequest,
    MFAEnableResponse,
    MFASetupResponse,
    MFAVerifyRequest,
    PasswordChange,
    ReservaCreate,
    ReservaResponse,
    ReservaUpdate,
    UsuarioCreate,
    UsuarioResponse,
    UsuarioUpdate,
)
from .sso import SSOError, create_state, exchange_code, read_state, sso_enabled

log = logging.getLogger(__name__)

APP_VERSION = "2.2.0"
LOGIN_WINDOW_MINUTES = 15
LOGIN_MAX_USER_ATTEMPTS = 10
LOGIN_MAX_IP_ATTEMPTS = 60
SSO_STATE_COOKIE = "norus_sso_state"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranque/cierre. Las tablas se crean con Alembic, nunca aquí."""
    if IS_PRODUCTION and not encryption_active():
        raise RuntimeError("NORUS_ENCRYPTION_KEY es obligatoria en producción.")
    log.info("Norus Intelligence Platform %s · base de datos: %s", APP_VERSION, database_kind())
    yield


app = FastAPI(
    title="Norus Intelligence Platform API",
    description="API de analítica comercial y flujos de operación hotelera.",
    version=APP_VERSION,
    docs_url=None if IS_PRODUCTION else "/api/docs",
    redoc_url=None if IS_PRODUCTION else "/api/redoc",
    openapi_url=None if IS_PRODUCTION else "/api/openapi.json",
    lifespan=lifespan,
)

# CORS disabled by default: frontend and API are same-origin.
CORS_ORIGINS = [x.strip() for x in os.environ.get("NORUS_CORS_ORIGINS", "").split(",") if x.strip()]
if "*" in CORS_ORIGINS:
    raise RuntimeError("NORUS_CORS_ORIGINS no admite '*'; define orígenes exactos.")
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", CSRF_HEADER],
    )

from .operaciones import router as operaciones_router  # noqa: E402

app.include_router(operaciones_router)
app.include_router(habitaciones_router)
app.include_router(mercado_router)

# ============================================================
# ARCHIVOS ESTÁTICOS (frontend)
# ============================================================

RAIZ = Path(__file__).resolve().parent.parent

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
    ".ico": "image/x-icon",
}

SEGMENTOS_PERMITIDOS = {
    "index.html", "login.html", "css", "js", "data", "vendor", "docs",
    "favicon.ico", "robots.txt",
}

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

CABECERAS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": CSP,
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _resolver(ruta: str) -> Path | None:
    """Resuelve una ruta URL a un archivo real dentro de la raíz.

    Se resuelve el archivo y se valida el segmento *resuelto*: así una ruta
    como ``js/../.env`` o ``css/../data/norusluum.db`` no puede escapar del
    conjunto de archivos públicos.
    """
    rel = unquote(ruta).lstrip("/")
    if not rel:
        rel = "index.html"
    if "\x00" in rel:
        return None
    destino = (RAIZ / rel).resolve()
    try:
        relative = destino.relative_to(RAIZ)
    except ValueError:
        return None
    parts = relative.parts
    if not parts or any(part.startswith(".") for part in parts):
        return None
    if parts[0] not in SEGMENTOS_PERMITIDOS:
        return None
    # La carpeta data solo expone el dataset estático del frontend.
    if parts[0] == "data" and relative.as_posix() != "data/datos.js":
        return None
    return destino if destino.is_file() else None


# ============================================================
# MIDDLEWARE: CSRF, cabeceras y métricas
# ============================================================

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    import time

    start_time = time.time()
    blocked: JSONResponse | None = None

    # 1) Verificación de origen para todas las escrituras.
    # 2) CSRF double-submit para escrituras autenticadas por cookie.
    if request.method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site", "")
        host = request.headers.get("host", "").casefold()
        same_origin = not origin or urlsplit(origin).netloc.casefold() == host
        cookie_session = bool(request.cookies.get(SESSION_COOKIE))
        header_auth = bool(request.headers.get("authorization"))
        if fetch_site == "cross-site" or not same_origin:
            blocked = JSONResponse(status_code=403, content={"detail": "Origen no autorizado."})
        elif cookie_session and not header_auth and not csrf_is_valid(request):
            blocked = JSONResponse(status_code=403, content={"detail": "Token CSRF ausente o inválido."})

    response = blocked if blocked is not None else await call_next(request)

    for key, value in CABECERAS.items():
        response.headers.setdefault(key, value)
    if IS_PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    response.headers.setdefault("Server", "norus")

    route = request.scope.get("route")
    path = getattr(route, "path", "blocked" if blocked is not None else "unmatched")
    if path.startswith("/metrics") or path.startswith("/api/docs"):
        return response
    try:
        from .metrics import http_request_duration_seconds, http_requests_total

        http_requests_total.labels(method=request.method, path=path, status=response.status_code).inc()
        http_request_duration_seconds.labels(method=request.method, path=path).observe(time.time() - start_time)
    except Exception:  # never break a response because of metrics
        pass
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("Error no controlado en %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor. Contacte al administrador."},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    headers = getattr(exc, "headers", None) or {}
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=headers)


# ============================================================
# API: HEALTH
# ============================================================

@app.get("/api/health")
def salud(db: Session = Depends(get_db)):
    """Readiness: comprueba que la API y la base estén disponibles."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    return {"status": "ok", "app": "Norus Intelligence Platform", "version": APP_VERSION}


# ============================================================
# MÉTRICAS PROMETHEUS (solo administradores autenticados)
# ============================================================

from .metrics import (  # noqa: E402
    CONTENT_TYPE_LATEST,
    generate_latest,
)


@app.get("/metrics")
def metrics(_: Usuario = Depends(require_admin)):
    """Endpoint de métricas para Prometheus (protegido)."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ============================================================
# API: AUTENTICACIÓN
# ============================================================

def _fingerprint(value: str) -> str:
    return hmac.new(SECRET_KEY.encode(), value.casefold().encode(), hashlib.sha256).hexdigest()


def _audit_event(db: Session, accion: str, usuario: str, detalle: str = "", ip: str = "") -> None:
    db.add(EventoAuditoria(accion=accion[:50], usuario=usuario[:100], detalle=detalle[:500], ip=ip[:50]))
    db.commit()


def _user_payload(db: Session, user: Usuario) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "rol": user.rol,
        "activo": int(user.activo or 0),
        "mfa_habilitado": int(user.mfa_habilitado or 0),
        "acceso_todas_propiedades": int(user.acceso_todas_propiedades or 0),
        "propiedades": [int(p.id) for p in properties_for_user(db, user)],
    }


def _issue_session(response: Response, db: Session, user: Usuario) -> None:
    """Emite la cookie de sesión HttpOnly y el token CSRF de doble envío."""
    token = create_access_token(
        {"sub": user.username, "rol": user.rol, "ver": int(user.token_version or 0)}
    )
    set_session_cookie(response, token)
    set_csrf_cookie(response)


def _login_attempt_counts(db: Session, username_hash: str, ip_hash: str) -> tuple[int, int]:
    since = datetime.now(UTC) - timedelta(minutes=LOGIN_WINDOW_MINUTES)
    query = db.query(IntentoLogin).filter(IntentoLogin.creado_en >= since)
    return (
        query.filter(IntentoLogin.usuario_hash == username_hash).count(),
        query.filter(IntentoLogin.ip_hash == ip_hash).count(),
    )


def _record_failure(db: Session, username_hash: str, ip_hash: str) -> None:
    db.add(IntentoLogin(usuario_hash=username_hash, ip_hash=ip_hash, creado_en=datetime.now(UTC)))
    db.add(EventoAuditoria(
        accion="login_fallo", usuario="anonimo", detalle="Intento de autenticación fallido",
        ip=ip_hash[:24],
    ))
    db.commit()


@app.post("/api/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Autentica usuario y contraseña; si MFA está activo devuelve un token de segundo factor."""
    now = datetime.now(UTC)
    username_hash = _fingerprint(body.username)
    ip_value = request.client.host if request.client else "unknown"
    ip_hash = _fingerprint(ip_value)

    db.query(IntentoLogin).filter(IntentoLogin.creado_en < now - timedelta(days=1)).delete(synchronize_session=False)
    db.commit()

    user_attempts, ip_attempts = _login_attempt_counts(db, username_hash, ip_hash)
    if user_attempts >= LOGIN_MAX_USER_ATTEMPTS or ip_attempts >= LOGIN_MAX_IP_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Demasiados intentos de acceso. Intente nuevamente en 15 minutos.")

    user = db.query(Usuario).filter(Usuario.username == body.username).first()
    if not user:
        from .auth import verify_password_dummy

        verify_password_dummy(body.password)
    if not user or not verify_password(body.password, user.password_hash):
        _record_failure(db, username_hash, ip_hash)
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")

    if not int(user.activo or 0):
        raise HTTPException(status_code=403, detail="Usuario inactivo.")

    user.ultimo_acceso = now
    db.query(IntentoLogin).filter(IntentoLogin.usuario_hash == username_hash).delete(synchronize_session=False)
    db.commit()

    if int(user.mfa_habilitado or 0):
        _audit_event(db, "login_mfa_pendiente", user.username, "Contraseña correcta; segundo factor requerido", ip_hash[:24])
        return LoginResponse(
            username=user.username,
            rol=user.rol,
            mfa_required=True,
            mfa_token=create_mfa_token(user.username, int(user.token_version or 0)),
        )

    _issue_session(response, db, user)
    _audit_event(db, "login", user.username, "Inicio de sesión exitoso", ip_hash[:24])
    return LoginResponse(username=user.username, rol=user.rol)


@app.post("/api/auth/mfa/verify", response_model=LoginResponse)
def mfa_verify(
    body: MFAVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Completa el inicio de sesión con TOTP o un código de respaldo."""
    payload = decode_token(body.mfa_token, expected_scope="mfa")
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Token de verificación inválido o expirado.")
    user = db.query(Usuario).filter(Usuario.username == payload["sub"], Usuario.activo == 1).first()
    if not user or not int(user.mfa_habilitado or 0):
        raise HTTPException(status_code=401, detail="Verificación no disponible.")
    if int(payload.get("ver", 0)) != int(user.token_version or 0):
        raise HTTPException(status_code=401, detail="Sesión revocada.")

    # El segundo factor también se limita para impedir fuerza bruta de códigos.
    username_hash = _fingerprint(user.username)
    ip_hash = _fingerprint(request.client.host if request.client else "unknown")
    user_attempts, ip_attempts = _login_attempt_counts(db, username_hash, ip_hash)
    if user_attempts >= LOGIN_MAX_USER_ATTEMPTS or ip_attempts >= LOGIN_MAX_IP_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Demasiados intentos de verificación. Intente nuevamente en 15 minutos.")

    counter = verify_totp(user.mfa_secret or "", body.code)
    if counter is not None and counter > int(user.mfa_last_counter or 0):
        user.mfa_last_counter = counter
        db.commit()
    else:
        ok, new_codes = consume_backup_code(user.backup_codes, body.code)
        if not ok:
            _record_failure(db, username_hash, ip_hash)
            raise HTTPException(status_code=401, detail="Código de verificación incorrecto.")
        user.backup_codes = new_codes
        db.commit()

    db.query(IntentoLogin).filter(IntentoLogin.usuario_hash == username_hash).delete(synchronize_session=False)
    db.commit()
    _issue_session(response, db, user)
    _audit_event(db, "login_mfa", user.username, "Segundo factor validado")
    return LoginResponse(username=user.username, rol=user.rol)


@app.get("/api/auth/me", response_model=UsuarioResponse)
def me(current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Devuelve el usuario autenticado y sus propiedades permitidas."""
    return _user_payload(db, current_user)


@app.post("/api/auth/logout")
def logout(response: Response, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    clear_session_cookies(response)
    _audit_event(db, "logout", current_user.username, "Cierre de sesión")
    return {"ok": True}


@app.post("/api/auth/password")
def change_password(
    body: PasswordChange,
    response: Response,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cambio de contraseña propio; revoca sesiones anteriores."""
    if not verify_password(body.password_actual, current_user.password_hash):
        raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")
    validate_password_strength(body.password_nueva)
    if verify_password(body.password_nueva, current_user.password_hash):
        raise HTTPException(status_code=400, detail="La nueva contraseña debe ser diferente.")
    current_user.password_hash = hash_password(body.password_nueva)
    current_user.token_version = int(current_user.token_version or 0) + 1
    db.commit()
    _issue_session(response, db, current_user)
    _audit_event(db, "cambio_password", current_user.username, "Contraseña actualizada")
    return {"ok": True}


# ------------------------------------------------------------
# MFA: configuración (usuario autenticado)
# ------------------------------------------------------------

@app.post("/api/auth/mfa/setup", response_model=MFASetupResponse)
def mfa_setup(current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Genera un secreto TOTP pendiente (aún no activa el segundo factor)."""
    if int(current_user.mfa_habilitado or 0):
        raise HTTPException(status_code=400, detail="MFA ya está habilitado. Desactívelo antes de reconfigurarlo.")
    secret = generate_totp_secret()
    current_user.mfa_pending_secret = secret
    db.commit()
    return MFASetupResponse(
        secret=secret,
        otpauth_uri=totp_uri(secret, current_user.username),
        issuer="Norus Intelligence",
    )


@app.post("/api/auth/mfa/enable", response_model=MFAEnableResponse)
def mfa_enable(
    body: MFAEnableRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Activa MFA tras validar un código TOTP del secreto pendiente."""
    secret = current_user.mfa_pending_secret
    if not secret:
        raise HTTPException(status_code=400, detail="Solicite primero la configuración de MFA.")
    counter = verify_totp(secret, body.code)
    if counter is None:
        raise HTTPException(status_code=400, detail="Código incorrecto. Verifique la hora del dispositivo.")
    codes = generate_backup_codes()
    current_user.mfa_secret = secret
    current_user.mfa_pending_secret = None
    current_user.mfa_habilitado = 1
    current_user.mfa_last_counter = counter
    current_user.backup_codes = backup_codes_json(codes)
    db.commit()
    _audit_event(db, "mfa_habilitado", current_user.username, "Segundo factor activado")
    return MFAEnableResponse(ok=True, backup_codes=codes)


@app.post("/api/auth/mfa/disable")
def mfa_disable(
    body: MFADisableRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Desactiva MFA verificando contraseña y un código válido."""
    if not verify_password(body.password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="La contraseña no es correcta.")
    counter = verify_totp(current_user.mfa_secret or "", body.code)
    backup_ok = False
    new_codes = current_user.backup_codes
    if counter is None:
        backup_ok, new_codes = consume_backup_code(current_user.backup_codes, body.code)
    if counter is None and not backup_ok:
        raise HTTPException(status_code=400, detail="Código de verificación incorrecto.")
    current_user.mfa_habilitado = 0
    current_user.mfa_secret = None
    current_user.mfa_pending_secret = None
    current_user.mfa_last_counter = 0
    current_user.backup_codes = None
    db.commit()
    _audit_event(db, "mfa_deshabilitado", current_user.username, "Segundo factor desactivado")
    return {"ok": True}


# ------------------------------------------------------------
# SSO / OIDC (opcional, se activa solo con configuración)
# ------------------------------------------------------------

@app.get("/api/auth/sso/status")
def sso_status():
    return {"enabled": sso_enabled()}


@app.get("/api/auth/sso/login")
def sso_login():
    """Inicia el flujo Authorization Code + PKCE contra el proveedor OIDC."""
    if not sso_enabled():
        raise HTTPException(status_code=404, detail="SSO no está configurado.")
    try:
        signed_state, authorize_url = create_state()
    except SSOError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    response = RedirectResponse(authorize_url, status_code=302)
    response.set_cookie(
        key=SSO_STATE_COOKIE, value=signed_state, httponly=True,
        secure=cookie_secure(), samesite="lax", max_age=600, path="/",
    )
    return response


def _sso_error_redirect(code: int) -> RedirectResponse:
    """Redirige al login limpiando el estado SSO de un solo uso."""
    response = RedirectResponse(f"/login.html?sso_error={code}", status_code=302)
    response.delete_cookie(SSO_STATE_COOKIE, path="/", samesite="lax", secure=cookie_secure())
    return response


@app.get("/api/auth/sso/callback")
def sso_callback(request: Request, db: Session = Depends(get_db)):
    """Valida el código OIDC, vincula la cuenta y emite la sesión local."""
    if not sso_enabled():
        raise HTTPException(status_code=404, detail="SSO no está configurado.")
    state_cookie = request.cookies.get(SSO_STATE_COOKIE, "")
    state_param = request.query_params.get("state", "")
    code = request.query_params.get("code", "")
    if not state_cookie or not state_param or not code:
        return _sso_error_redirect(1)
    try:
        state_payload = read_state(state_cookie)
        if not hmac.compare_digest(str(state_payload.get("state", "")), state_param):
            raise SSOError("Estado de SSO no coincide.")
        claims = exchange_code(code, state_payload)
    except SSOError as exc:
        log.warning("SSO fallido: %s", exc)
        return _sso_error_redirect(1)

    subject = str(claims.get("sub") or "")
    email = str(claims.get("email") or "").strip().lower()
    user = db.query(Usuario).filter(Usuario.sso_subject == subject).first() if subject else None
    if user is None and email:
        user = db.query(Usuario).filter(func.lower(Usuario.email) == email).first()
        if user is not None:
            user.sso_subject = subject
            user.sso_provider = claims.get("iss")
    if user is None or not int(user.activo or 0):
        _audit_event(db, "sso_rechazado", email or "desconocido", "Cuenta no autorizada en Norus")
        return _sso_error_redirect(2)

    user.sso_subject = subject
    user.sso_provider = claims.get("iss")
    user.ultimo_acceso = datetime.now(UTC)
    db.commit()

    response = RedirectResponse("/index.html", status_code=302)
    response.delete_cookie(SSO_STATE_COOKIE, path="/", samesite="lax", secure=cookie_secure())
    _issue_session(response, db, user)
    _audit_event(db, "login_sso", user.username, "Inicio de sesión mediante SSO")
    return response


# ============================================================
# API: DASHBOARD
# ============================================================

def _validate_period(granularidad: str, desde: str | None, hasta: str | None) -> tuple[str | None, str | None, str]:
    if granularidad not in {"dia", "semana", "mes", "anio"}:
        raise HTTPException(status_code=422, detail="Granularidad inválida.")
    clean = []
    for value, name in ((desde, "desde"), (hasta, "hasta")):
        if value in (None, ""):
            clean.append(None)
            continue
        try:
            clean.append(datetime.strptime(value, "%Y-%m-%d").date().isoformat())
        except ValueError:
            raise HTTPException(status_code=422, detail=f"El parámetro {name} debe tener formato YYYY-MM-DD.")
    if clean[0] and clean[1] and clean[0] > clean[1]:
        raise HTTPException(status_code=422, detail="La fecha 'desde' no puede ser posterior a 'hasta'.")
    return clean[0], clean[1], granularidad


@app.get("/api/dashboard/propiedades")
def dashboard_properties(
    current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)
):
    return [
        {"id": p.id, "nombre": p.nombre, "habitaciones": int(p.habitaciones or 0)}
        for p in properties_for_user(db, current_user)
    ]


@app.get("/api/dashboard/monedas")
def monedas_dashboard(
    db: Session = Depends(get_db), current_user: Usuario = Depends(get_current_user)
):
    """Monedas visibles para el usuario, respetando su alcance por propiedad."""
    from .models import HechoMetricaDiaria, HechoMetricaMensual

    allowed = allowed_property_ids(db, current_user)

    def scoped(query, model):
        if allowed is None:
            return query
        if not allowed:
            return query.filter(model.marca_id == -1)
        return query.filter(model.marca_id.in_(allowed))

    values = {
        x[0]
        for x in scoped(db.query(HechoMetricaMensual.moneda).distinct(), HechoMetricaMensual).all()
        if x[0]
    }
    values.update(
        x[0]
        for x in scoped(db.query(HechoMetricaDiaria.moneda).distinct(), HechoMetricaDiaria).all()
        if x[0]
    )
    values.update(
        x[0]
        for x in scoped(db.query(HechoReserva.moneda).distinct(), HechoReserva).all()
        if x[0]
    )
    if not values:
        values.add(os.environ.get("NORUS_DEFAULT_CURRENCY", "CLP").upper())
    return {"monedas": sorted(values)}


@app.get("/api/dashboard", response_model=DashboardData)
def dashboard(
    moneda: str = Query("CLP", pattern=r"^[A-Z]{3}$"),
    granularidad: str = Query("mes"),
    desde: str | None = Query(None, max_length=10),
    hasta: str | None = Query(None, max_length=10),
    propiedad_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Datos del dashboard agregados por día, semana, mes o año."""
    desde, hasta, granularidad = _validate_period(granularidad, desde, hasta)
    allowed = allowed_property_ids(db, current_user)
    if propiedad_id is not None:
        assert_property_access(db, current_user, propiedad_id)
        if not db.get(DimMarca, propiedad_id):
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
    data = obtener_dashboard(
        db, moneda=moneda, desde=desde, hasta=hasta, granularidad=granularidad,
        propiedad_id=propiedad_id, allowed_property_ids=allowed,
    )
    if current_user.rol == "lector":
        for reservation in data["reservas"]:
            reservation["huesped"] = ""
            reservation["codigo"] = ""
    return data


# ============================================================
# API: RESERVAS (CRUD)
# ============================================================

def _reserva_to_response(r: HechoReserva) -> dict:
    return {
        "id": r.id,
        "codigo": r.codigo or "",
        "huesped": r.huesped or "",
        "habitacion": r.habitacion or "",
        "estado": r.estado or "Confirmada",
        "valor": float(r.valor or 0.0),
        "noches": float(r.noches or 0.0),
        "anticipacion_dias": float(r.anticipacion_dias or 0.0),
        "moneda": r.moneda or "CLP",
        "fecha": r.fecha.fecha if r.fecha else None,
        "canal": r.canal.nombre if r.canal else None,
        "nacionalidad": r.nacionalidad.nombre if r.nacionalidad else None,
        "marca": r.marca.nombre if r.marca else None,
        "propiedad_id": r.marca_id,
        "segmento": r.segmento.nombre if r.segmento else None,
        "tipo_habitacion": r.tipo_habitacion.nombre if r.tipo_habitacion else None,
    }


def _reserve_scope(query, db: Session, user: Usuario):
    allowed = allowed_property_ids(db, user)
    if allowed is None:
        return query
    if not allowed:
        return query.filter(HechoReserva.marca_id == -1)
    return query.filter(HechoReserva.marca_id.in_(allowed))


def _resolve_brand(db: Session, user: Usuario, body) -> DimMarca | None:
    """Resuelve/crea la propiedad validando el alcance del usuario."""
    allowed = allowed_property_ids(db, user)
    if body.propiedad_id is not None:
        brand = db.get(DimMarca, body.propiedad_id)
        if not brand:
            raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
        assert_property_access(db, user, brand.id)
        return brand
    name = str(getattr(body, "marca", "") or "").strip()[:100]
    if not name:
        if allowed is None:
            return None
        if len(allowed) == 1:
            return db.get(DimMarca, allowed[0])
        raise HTTPException(status_code=422, detail="Indique la propiedad de la reserva.")
    brand = db.query(DimMarca).filter_by(nombre=name).first()
    if brand is None:
        if allowed is not None:
            raise HTTPException(status_code=403, detail="No tiene permisos para crear registros en esa propiedad.")
        brand = DimMarca(nombre=name, habitaciones=0)
        db.add(brand)
        db.flush()
    else:
        assert_property_access(db, user, brand.id)
    return brand


def _get_or_create_date(db: Session, date_text: str):
    from .models import DimFecha

    try:
        value = datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="Fecha inválida.")
    if not 1900 <= value.year <= 2100:
        raise HTTPException(status_code=422, detail="Año fuera de rango.")
    fecha = db.query(DimFecha).filter_by(fecha=date_text).first()
    if fecha is None:
        months = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        days = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        fecha = DimFecha(
            fecha=date_text, anio=value.year, mes=value.month, mes_nombre=months[value.month - 1],
            trimestre=(value.month - 1) // 3 + 1, dia_semana=value.weekday(),
            nombre_dia=days[value.weekday()], es_fin_semana=int(value.weekday() >= 5),
        )
        db.add(fecha)
        db.flush()
    return fecha


def _get_or_create_dim(db: Session, model, name: str, **defaults):
    name = str(name).strip()[:100]
    if not name:
        return None
    instance = db.query(model).filter_by(nombre=name).first()
    if instance is None:
        instance = model(nombre=name, **defaults)
        db.add(instance)
        db.flush()
    return instance


@app.get("/api/reservas")
def listar_reservas(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    busqueda: str = Query("", max_length=100),
    canal: str = Query("", max_length=100),
    propiedad_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_editor),
):
    """Lista reservas con paginación, filtros y alcance por propiedad."""
    from .models import DimCanal, DimFecha, DimMarca, DimNacionalidad, DimSegmento, DimTipoHabitacion

    query = db.query(HechoReserva).outerjoin(DimFecha).outerjoin(DimCanal).outerjoin(
        DimNacionalidad, HechoReserva.nacionalidad_id == DimNacionalidad.id
    ).outerjoin(DimMarca, HechoReserva.marca_id == DimMarca.id).outerjoin(
        DimSegmento, HechoReserva.segmento_id == DimSegmento.id
    ).outerjoin(DimTipoHabitacion, HechoReserva.tipo_habitacion_id == DimTipoHabitacion.id)
    query = _reserve_scope(query, db, current_user)
    if propiedad_id is not None:
        assert_property_access(db, current_user, propiedad_id)
        query = query.filter(HechoReserva.marca_id == propiedad_id)
    if canal:
        query = query.filter(DimCanal.nombre == canal)
    query = query.order_by(HechoReserva.id.desc())
    if not busqueda:
        return [_reserva_to_response(r) for r in query.offset(skip).limit(limit).all()]

    # El nombre del huésped está cifrado en reposo: se filtra en memoria.
    needle = busqueda.casefold()
    matches = [
        r for r in query.limit(max(limit * 20, 1000)).all()
        if needle in (r.codigo or "").casefold() or needle in (r.huesped or "").casefold()
    ]
    return [_reserva_to_response(r) for r in matches[skip:skip + limit]]


@app.post("/api/reservas", response_model=ReservaResponse)
def crear_reserva(
    body: ReservaCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_editor),
):
    """Crea una nueva reserva dentro de las propiedades permitidas."""
    from .models import DimCanal, DimNacionalidad, DimSegmento, DimTipoHabitacion

    brand = _resolve_brand(db, current_user, body)
    dim_date = _get_or_create_date(db, body.fecha) if body.fecha else None
    dim_canal = _get_or_create_dim(db, DimCanal, body.canal, tipo="") if body.canal else None
    dim_nac = _get_or_create_dim(db, DimNacionalidad, body.nacionalidad, region="") if body.nacionalidad else None
    dim_seg = _get_or_create_dim(db, DimSegmento, body.segmento) if body.segmento else None
    dim_th = _get_or_create_dim(db, DimTipoHabitacion, body.tipo_habitacion) if body.tipo_habitacion else None

    reserva = HechoReserva(
        codigo=str(body.codigo or "").strip()[:50],
        huesped=str(body.huesped or "").strip()[:200],
        habitacion=str(body.habitacion or "").strip()[:30],
        estado=str(body.estado or "Confirmada").strip()[:50],
        moneda=body.moneda,
        valor=max(0, float(body.valor or 0)),
        noches=max(0, float(body.noches or 0)),
        anticipacion_dias=max(0, float(body.anticipacion_dias or 0)),
        fecha_id=dim_date.id if dim_date else None,
        canal_id=dim_canal.id if dim_canal else None,
        nacionalidad_id=dim_nac.id if dim_nac else None,
        marca_id=brand.id if brand else None,
        segmento_id=dim_seg.id if dim_seg else None,
        tipo_habitacion_id=dim_th.id if dim_th else None,
    )
    db.add(reserva)
    db.commit()
    db.refresh(reserva)
    _audit_event(db, "crear_reserva", current_user.username, f"Reserva {reserva.codigo}")
    return _reserva_to_response(reserva)


@app.put("/api/reservas/{reserva_id}", response_model=ReservaResponse)
def actualizar_reserva(
    reserva_id: int,
    body: ReservaUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_editor),
):
    """Actualiza una reserva existente dentro del alcance del usuario."""
    from .models import DimCanal, DimNacionalidad, DimSegmento, DimTipoHabitacion

    reserva = db.get(HechoReserva, reserva_id)
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")
    assert_property_access(db, current_user, reserva.marca_id)

    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    for field in ("codigo", "huesped", "habitacion", "estado", "valor", "noches", "anticipacion_dias", "moneda"):
        if field in updates:
            value = updates[field]
            if field == "codigo":
                value = str(value)[:50]
            elif field == "huesped":
                value = str(value)[:200]
            elif field == "habitacion":
                value = str(value).strip()[:30]
            elif field == "estado":
                value = str(value)[:50]
            setattr(reserva, field, value)

    if "fecha" in updates:
        reserva.fecha_id = _get_or_create_date(db, updates["fecha"]).id

    if "propiedad_id" in updates or "marca" in updates:
        brand = _resolve_brand(db, current_user, body)
        if brand is not None:
            reserva.marca_id = brand.id

    dimensions = {
        "canal": (DimCanal, "canal_id", {"tipo": ""}),
        "nacionalidad": (DimNacionalidad, "nacionalidad_id", {"region": ""}),
        "segmento": (DimSegmento, "segmento_id", {}),
        "tipo_habitacion": (DimTipoHabitacion, "tipo_habitacion_id", {}),
    }
    for field, (model, fk, defaults) in dimensions.items():
        if field not in updates:
            continue
        name = str(updates[field]).strip()
        if not name:
            setattr(reserva, fk, None)
            continue
        dimension = _get_or_create_dim(db, model, name, **defaults)
        setattr(reserva, fk, dimension.id if dimension else None)

    db.commit()
    db.refresh(reserva)
    _audit_event(db, "actualizar_reserva", current_user.username, f"Reserva {reserva.codigo}")
    return _reserva_to_response(reserva)


@app.delete("/api/reservas/{reserva_id}")
def eliminar_reserva(
    reserva_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_admin),
):
    """Elimina una reserva (solo admin) dentro de su alcance."""
    reserva = db.get(HechoReserva, reserva_id)
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")
    assert_property_access(db, current_user, reserva.marca_id)
    codigo = reserva.codigo
    db.delete(reserva)
    db.commit()
    _audit_event(db, "eliminar_reserva", current_user.username, f"Reserva {codigo}")
    return {"ok": True}


# ============================================================
# API: IMPORTACIÓN ETL
# ============================================================

EXTENSIONES_PERMITIDAS = {".csv", ".tsv", ".xlsx", ".json"}


@app.post("/api/importar", response_model=ImportacionResponse)
async def importar_archivo(
    file: UploadFile = File(...),
    mapeo: str = Form(default="{}"),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_editor),
):
    """Sube un archivo y ejecuta el pipeline ETL autenticado."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in EXTENSIONES_PERMITIDAS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato no soportado: {ext}. Permitidos: {', '.join(sorted(EXTENSIONES_PERMITIDAS))}",
        )

    max_bytes = int(os.environ.get("NORUS_UPLOAD_MAX_BYTES", str(512 * 1024 * 1024)))
    contenido = await file.read(max_bytes + 1)
    if len(contenido) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Archivo demasiado grande (máx {max_bytes // (1024 * 1024)} MB).")
    if not contenido:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")

    try:
        mapeo_manual = json.loads(mapeo)
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="El mapeo de columnas no es JSON válido.")
    if not isinstance(mapeo_manual, dict) or len(mapeo_manual) > 40 or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in mapeo_manual.items()
    ):
        raise HTTPException(status_code=400, detail="El mapeo de columnas no tiene el formato esperado.")

    allowed = allowed_property_ids(db, current_user)
    carga = ejecutar_carga(
        db, file.filename or "sin_nombre", contenido, current_user.username, mapeo_manual,
        allowed_property_ids=allowed,
    )
    return ImportacionResponse(
        carga_id=carga.id,
        nombre_archivo=carga.nombre_archivo,
        formato=carga.formato,
        filas_procesadas=carga.filas_procesadas,
        filas_exitosas=carga.filas_exitosas,
        filas_fallidas=carga.filas_fallidas,
        estado=carga.estado,
        mensaje=carga.mensaje_error or f"{carga.filas_exitosas} registros cargados exitosamente.",
    )


@app.get("/api/cargas", response_model=list[CargaArchivoResponse])
def listar_cargas(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_editor),
):
    """Historial de importaciones; solo para perfiles con permiso de escritura."""
    cargas = db.query(CargaArchivo).order_by(CargaArchivo.id.desc()).offset(skip).limit(limit).all()
    return cargas


# ============================================================
# API: AUDITORÍA
# ============================================================

@app.get("/api/auditoria", response_model=list[EventoAuditoriaResponse])
def listar_auditoria(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: Usuario = Depends(require_admin),
):
    return db.query(EventoAuditoria).order_by(EventoAuditoria.id.desc()).offset(skip).limit(limit).all()


@app.post("/api/auditoria/registrar")
def registrar_evento(
    accion: str = Query(..., min_length=1, max_length=50),
    detalle: str = Query("", max_length=500),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    _audit_event(db, accion, current_user.username, detalle)
    return {"ok": True}


# ============================================================
# API: USUARIOS (solo admin)
# ============================================================

def _set_properties(db: Session, user: Usuario, property_ids: list[int]) -> None:
    unique = sorted({int(x) for x in property_ids})
    if unique:
        found = {row[0] for row in db.query(DimMarca.id).filter(DimMarca.id.in_(unique)).all()}
        missing = [x for x in unique if x not in found]
        if missing:
            raise HTTPException(status_code=404, detail=f"Propiedad inexistente: {missing[0]}.")
    db.query(UsuarioPropiedad).filter(UsuarioPropiedad.usuario_id == user.id).delete(synchronize_session=False)
    for property_id in unique:
        db.add(UsuarioPropiedad(usuario_id=user.id, marca_id=property_id))


@app.post("/api/usuarios", response_model=UsuarioResponse)
def crear_usuario(
    body: UsuarioCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_admin),
):
    """Crea un usuario con rol y propiedades asignadas."""
    if db.query(Usuario).filter_by(username=body.username).first():
        raise HTTPException(status_code=409, detail="El usuario ya existe.")
    validate_password_strength(body.password)
    if body.email and db.query(Usuario).filter(func.lower(Usuario.email) == body.email.lower()).first():
        raise HTTPException(status_code=409, detail="El correo ya está registrado.")

    user = Usuario(
        username=body.username,
        password_hash=hash_password(body.password),
        email=body.email.lower() if body.email else None,
        rol=body.rol,
        acceso_todas_propiedades=1 if body.acceso_todas_propiedades else 0,
    )
    db.add(user)
    db.flush()
    _set_properties(db, user, body.propiedades)
    db.commit()
    db.refresh(user)
    _audit_event(db, "crear_usuario", current_user.username, f"Usuario {user.username} rol {user.rol}")
    return _user_payload(db, user)


@app.get("/api/usuarios", response_model=list[UsuarioResponse])
def listar_usuarios(db: Session = Depends(get_db), _: Usuario = Depends(require_admin)):
    return [_user_payload(db, user) for user in db.query(Usuario).order_by(Usuario.username).all()]


@app.patch("/api/usuarios/{user_id}", response_model=UsuarioResponse)
def actualizar_usuario(
    user_id: int,
    body: UsuarioUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_admin),
):
    """Actualiza datos, rol, estado y propiedades de un usuario."""
    user = db.get(Usuario, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    changes = body.model_dump(exclude_unset=True)
    if user.id == current_user.id and ("rol" in changes or changes.get("activo") is False):
        raise HTTPException(status_code=400, detail="No puede cambiar su propio rol ni desactivar su cuenta.")

    if "email" in changes:
        email = (changes["email"] or "").strip().lower() or None
        if email and db.query(Usuario).filter(func.lower(Usuario.email) == email, Usuario.id != user.id).first():
            raise HTTPException(status_code=409, detail="El correo ya está registrado.")
        user.email = email
    if "rol" in changes and changes["rol"] is not None:
        user.rol = changes["rol"]
    if "activo" in changes and changes["activo"] is not None:
        user.activo = 1 if changes["activo"] else 0
        if not user.activo:
            user.token_version = int(user.token_version or 0) + 1
    if "acceso_todas_propiedades" in changes and changes["acceso_todas_propiedades"] is not None:
        user.acceso_todas_propiedades = 1 if changes["acceso_todas_propiedades"] else 0
    if changes.get("propiedades") is not None:
        _set_properties(db, user, changes["propiedades"])
    db.commit()
    db.refresh(user)
    _audit_event(db, "actualizar_usuario", current_user.username, f"Usuario {user.username}")
    return _user_payload(db, user)


@app.post("/api/usuarios/{user_id}/reset-password")
def reset_password(
    user_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_admin),
):
    """Restablece la contraseña de otro usuario y revoca sus sesiones."""
    user = db.get(Usuario, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    password = str(body.get("password") or "")
    validate_password_strength(password)
    user.password_hash = hash_password(password)
    user.token_version = int(user.token_version or 0) + 1
    db.commit()
    _audit_event(db, "reset_password", current_user.username, f"Usuario {user.username}")
    return {"ok": True}


# ============================================================
# CATCH-ALL: archivos estáticos (DEBE SER LA ÚLTIMA RUTA)
# ============================================================

@app.api_route("/{ruta:path}", methods=["GET", "HEAD"])
def servir(ruta: str, request: Request, db: Session = Depends(get_db)):
    """Sirve el front estático; el dashboard exige una sesión válida."""
    if ruta.startswith("api/"):
        raise HTTPException(status_code=404, detail="No encontrado.")

    objetivo = _resolver(ruta)
    if objetivo is None:
        raise HTTPException(status_code=404, detail="No encontrado.")

    relative = objetivo.relative_to(RAIZ).as_posix()
    user = optional_current_user(
        credentials=None,
        norus_session=request.cookies.get(SESSION_COOKIE),
        db=db,
    )

    # Nunca entregar el dashboard antes de iniciar sesión.
    if relative == "index.html" and user is None:
        return RedirectResponse("/login.html", status_code=302)
    if relative == "login.html" and user is not None:
        return RedirectResponse("/index.html", status_code=302)

    return FileResponse(
        objetivo,
        headers=CABECERAS,
        media_type=MIME.get(objetivo.suffix.lower(), "application/octet-stream"),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8765)
