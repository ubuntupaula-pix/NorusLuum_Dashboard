# -*- coding: utf-8 -*-
"""============================================================
   NORUS & LUUM · Tests de la API
   Autenticación, CSRF, dashboard multi-granularidad, MFA,
   permisos por propiedad y administración de usuarios.
   ============================================================"""

import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app, get_db
from app.database import Base
from app.auth import hash_password
from app.crypto import TOTP_INTERVAL, _totp_at
from app.models import (
    CapacidadHabitacion,
    DimMarca,
    DimTipoHabitacion,
    HechoMetricaMensual,
    HechoReserva,
    IncidenciaOperativa,
    ReferenciaMercado,
    Usuario,
)

# Base de datos de prueba en memoria
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base.metadata.create_all(bind=engine)


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

ADMIN_USER = "testadmin"
ADMIN_PASS = "testpass-testpass"

# Crear usuario admin de prueba
_db = TestingSessionLocal()
_db.add(Usuario(username=ADMIN_USER, password_hash=hash_password(ADMIN_PASS), rol="admin"))
_db.commit()
_db.close()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
        test_client.cookies.clear()


# ---------- Utilidades ----------

def login(client, username=ADMIN_USER, password=ADMIN_PASS):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def csrf_headers(client):
    token = client.cookies.get("norus_csrf")
    assert token, "El login debe emitir la cookie CSRF"
    return {"X-CSRF-Token": token}


def post(client, url, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    headers.update(csrf_headers(client))
    return client.post(url, headers=headers, **kwargs)


def put(client, url, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    headers.update(csrf_headers(client))
    return client.put(url, headers=headers, **kwargs)


def patch(client, url, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    headers.update(csrf_headers(client))
    return client.patch(url, headers=headers, **kwargs)


def delete(client, url, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    headers.update(csrf_headers(client))
    return client.delete(url, headers=headers, **kwargs)


def current_totp(secret: str) -> str:
    return _totp_at(secret, int(time.time()) // TOTP_INTERVAL)


# ---------- Salud y sesión ----------

def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_exitoso(client):
    response = login(client)
    data = response.json()
    assert "access_token" not in data
    assert "norus_session" in client.cookies
    assert "norus_csrf" in client.cookies
    assert data["username"] == ADMIN_USER
    assert data["rol"] == "admin"


def test_login_fallido(client):
    response = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": "wrong-password"})
    assert response.status_code == 401


def test_dashboard_requiere_auth(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 401


def test_index_redirige_sin_sesion(client):
    response = client.get("/index.html", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login.html"


def test_index_disponible_con_sesion(client):
    login(client)
    response = client.get("/index.html", follow_redirects=False)
    assert response.status_code == 200


def test_no_expone_base_de_datos_ni_archivos_privados(client):
    assert client.get("/data/norusluum.db").status_code == 404
    assert client.get("/data/datos.js").status_code == 200
    assert client.get("/js/../README.md").status_code == 404
    assert client.get("/css/../data/norusluum.db").status_code == 404
    assert client.get("/.env").status_code == 404


# ---------- CSRF ----------

def test_escritura_sin_csrf_es_rechazada(client):
    login(client)
    response = client.post("/api/auditoria/registrar", params={"accion": "test"})
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]


def test_escritura_con_csrf_funciona(client):
    login(client)
    response = post(client, "/api/auditoria/registrar", params={"accion": "test_csrf"})
    assert response.status_code == 200


def test_origen_cruzado_bloqueado_en_login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_origen_cruzado_bloqueado_con_sesion(client):
    login(client)
    headers = csrf_headers(client)
    headers.update({"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    response = client.post("/api/auditoria/registrar", params={"accion": "x"}, headers=headers)
    assert response.status_code == 403
    assert "Origen" in response.json()["detail"]


def test_origen_propio_permitido(client):
    login(client)
    headers = csrf_headers(client)
    headers["Origin"] = "http://testserver"
    response = client.post("/api/auditoria/registrar", params={"accion": "same_origin"}, headers=headers)
    assert response.status_code == 200


# ---------- Dashboard ----------

@pytest.mark.parametrize("granularidad", ["dia", "semana", "mes", "anio"])
def test_dashboard_granularidades(client, granularidad):
    login(client)
    response = client.get(
        "/api/dashboard",
        params={"granularidad": granularidad, "desde": "2025-01-01", "hasta": "2025-12-31"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["granularidad"] == granularidad
    assert "kpi" in data
    assert "anio" in data["kpi"]
    assert "meses" in data


def test_dashboard_preview_incluye_numero_de_habitacion(client):
    login(client)
    codigo = "RES-HAB-DASH"
    creada = post(client, "/api/reservas", json={
        "codigo": codigo,
        "huesped": "Huésped Habitación",
        "habitacion": "612",
        "estado": "Confirmada",
        "valor": 100.0,
        "noches": 1,
        "fecha": date.today().isoformat(),
        "canal": "Directo",
        "nacionalidad": "Chile",
        "tipo_habitacion": "Suite",
    })
    assert creada.status_code == 200, creada.text

    data = client.get("/api/dashboard", params={"granularidad": "mes"}).json()
    fila = next(r for r in data["reservas"] if r["codigo"] == codigo)
    assert fila["habitacion"] == "612"


def test_dashboard_granularidad_invalida(client):
    login(client)
    response = client.get("/api/dashboard", params={"granularidad": "trimestre"})
    assert response.status_code == 422


def test_dashboard_fechas_invertidas(client):
    login(client)
    response = client.get("/api/dashboard", params={"desde": "2025-12-31", "hasta": "2025-01-01"})
    assert response.status_code == 422


def test_dashboard_series_por_marca_admiten_periodos_sin_datos(client):
    """Los períodos sin métricas deben serializarse como null, no romper la respuesta."""
    db = TestingSessionLocal()
    enero = DimMarca(nombre="Propiedad Solo Enero", habitaciones=10)
    febrero = DimMarca(nombre="Propiedad Solo Febrero", habitaciones=10)
    db.add_all([enero, febrero])
    db.flush()
    for brand, mes in ((enero, 1), (febrero, 2)):
        db.add(HechoMetricaMensual(
            anio=2025, mes=mes, marca_id=brand.id, moneda="CLP",
            ocupacion=50, adr=100, revpar=50, ingresos=1000,
            noches_vendidas=10, habitaciones_disponibles=100,
        ))
    db.commit()
    db.close()

    login(client)
    response = client.get(
        "/api/dashboard",
        params={"granularidad": "mes", "desde": "2025-01-01", "hasta": "2025-02-28"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["ingresos_marca"]) == len(data["marcas"])
    filas_con_huecos = [
        fila for fila in data["ingresos_marca"]
        if any(value is None for value in fila) and any(value is not None for value in fila)
    ]
    assert filas_con_huecos, data["ingresos_marca"]


def test_dashboard_monedas_respeta_alcance(client):
    db = TestingSessionLocal()
    norte = DimMarca(nombre="Moneda Norte", habitaciones=5)
    sur = DimMarca(nombre="Moneda Sur", habitaciones=5)
    db.add_all([norte, sur])
    db.flush()
    db.add(HechoMetricaMensual(
        anio=2025, mes=1, marca_id=norte.id, moneda="MXN",
        ocupacion=10, adr=1, revpar=1, ingresos=1, noches_vendidas=1, habitaciones_disponibles=1,
    ))
    db.add(HechoMetricaMensual(
        anio=2025, mes=1, marca_id=sur.id, moneda="PEN",
        ocupacion=10, adr=1, revpar=1, ingresos=1, noches_vendidas=1, habitaciones_disponibles=1,
    ))
    db.commit()
    norte_id, sur_id = norte.id, sur.id
    db.close()

    login(client)
    created = post(client, "/api/usuarios", json={
        "username": "monedas_scoped",
        "password": "Monedas-Scoped-2026!x",
        "rol": "lector",
        "propiedades": [norte_id],
        "acceso_todas_propiedades": False,
    })
    assert created.status_code == 200, created.text

    todas = client.get("/api/dashboard/monedas")
    assert todas.status_code == 200
    assert {"MXN", "PEN"} <= set(todas.json()["monedas"])

    client.cookies.clear()
    login(client, "monedas_scoped", "Monedas-Scoped-2026!x")
    propias = client.get("/api/dashboard/monedas")
    assert propias.status_code == 200
    assert propias.json()["monedas"] == ["MXN"]


def test_cargas_solo_para_editores(client):
    login(client)
    created = post(client, "/api/usuarios", json={
        "username": "cargas_lector",
        "password": "Cargas-Lector-2026!x",
        "rol": "lector",
    })
    assert created.status_code == 200, created.text
    assert client.get("/api/cargas").status_code == 200  # admin

    client.cookies.clear()
    login(client, "cargas_lector", "Cargas-Lector-2026!x")
    assert client.get("/api/cargas").status_code == 403


# ---------- CRUD y ETL ----------

def test_crear_reserva(client):
    login(client)
    response = post(client, "/api/reservas", json={
        "codigo": "RES-TEST-001",
        "huesped": "Huésped de Prueba",
        "estado": "Confirmada",
        "valor": 1500.0,
        "noches": 3,
        "fecha": "2025-06-15",
        "canal": "Directa Web",
        "nacionalidad": "México",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["codigo"] == "RES-TEST-001"
    assert data["huesped"] == "Huésped de Prueba"


def test_importar_csv(client):
    login(client)
    csv_content = (
        b"codigo,huesped,fecha,valor,canal,nacionalidad,estado\n"
        b"RES-001,Ana,2025-01-15,1000,Directa,Mexico,Confirmada\n"
        b"RES-002,Luis,2025-01-16,1200,OTA,Canada,Confirmada\n"
    )
    response = post(client, "/api/importar", files={"file": ("test.csv", csv_content, "text/csv")})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["filas_exitosas"] == 2
    assert data["estado"] == "completada"


# ---------- Permisos por propiedad ----------

def _seed_properties():
    db = TestingSessionLocal()
    existing = {row[0] for row in db.query(DimMarca.nombre).all()}
    created = {}
    for name, rooms in (("Propiedad Norte", 20), ("Propiedad Sur", 35)):
        if name not in existing:
            brand = DimMarca(nombre=name, habitaciones=rooms)
            db.add(brand)
            db.flush()
            created[name] = brand.id
        else:
            created[name] = db.query(DimMarca).filter_by(nombre=name).one().id
    db.commit()
    db.close()
    return created


def test_permisos_por_propiedad(client):
    props = _seed_properties()
    login(client)

    response = post(client, "/api/usuarios", json={
        "username": "scoped",
        "password": "Scoped-User-2026!x",
        "rol": "lector",
        "propiedades": [props["Propiedad Norte"]],
        "acceso_todas_propiedades": False,
    })
    assert response.status_code == 200, response.text
    user = response.json()
    assert user["propiedades"] == [props["Propiedad Norte"]]

    client.cookies.clear()
    login(client, "scoped", "Scoped-User-2026!x")

    listado = client.get("/api/dashboard/propiedades")
    assert listado.status_code == 200
    assert [p["id"] for p in listado.json()] == [props["Propiedad Norte"]]

    # Puede consultar su propiedad…
    ok = client.get("/api/dashboard", params={"propiedad_id": props["Propiedad Norte"]})
    assert ok.status_code == 200

    # …pero no otra.
    denied = client.get("/api/dashboard", params={"propiedad_id": props["Propiedad Sur"]})
    assert denied.status_code == 403

    denied_create = post(client, "/api/reservas", json={
        "codigo": "RES-SCOPED-001",
        "huesped": "Intruso",
        "valor": 10,
        "propiedad_id": props["Propiedad Sur"],
    })
    assert denied_create.status_code == 403

    # Un lector no administra usuarios.
    assert client.get("/api/usuarios").status_code == 403


def test_lector_ve_base_en_solo_lectura(client):
    """El lector consulta la Base, sin PII y sin poder escribir."""
    pid = _nuevo_brand("Base Lector", 26)
    login(client)
    creada = post(client, "/api/reservas", json={
        "codigo": "BASE-LECTOR-001",
        "huesped": "Huésped Confidencial",
        "habitacion": "707",
        "valor": 900,
        "moneda": "CLP",
        "propiedad_id": pid,
    })
    assert creada.status_code == 200, creada.text
    assert post(client, "/api/usuarios", json={
        "username": "base_lector",
        "password": "Base-Lector-2026!x",
        "rol": "lector",
        "propiedades": [pid],
        "acceso_todas_propiedades": False,
    }).status_code == 200

    client.cookies.clear()
    login(client, "base_lector", "Base-Lector-2026!x")

    page = client.get("/api/reservas", params={
        "habitacion": "707", "skip": 0, "limit": 5, "con_total": "true",
    })
    assert page.status_code == 200, page.text
    data = page.json()
    assert data["total"] == 1
    assert data["items"][0]["habitacion"] == "707"
    assert data["items"][0]["huesped"] == ""

    # La búsqueda general no permite inferir el nombre del huésped.
    sin_pii = client.get("/api/reservas", params={"busqueda": "Confidencial"})
    assert sin_pii.status_code == 200
    assert sin_pii.json() == []

    denegada = post(client, "/api/reservas", json={
        "codigo": "BASE-LECTOR-002", "huesped": "Intruso", "propiedad_id": pid,
    })
    assert denegada.status_code == 403
    assert put(client, f"/api/reservas/{creada.json()['id']}", json={"valor": 1}).status_code == 403
    assert delete(client, f"/api/reservas/{creada.json()['id']}").status_code == 403


def test_usuario_sin_propiedades_no_ve_datos(client):
    login(client)
    response = post(client, "/api/usuarios", json={
        "username": "sinacceso",
        "password": "Sin-Acceso-2026!x",
        "rol": "editor",
        "propiedades": [],
        "acceso_todas_propiedades": False,
    })
    assert response.status_code == 200, response.text

    client.cookies.clear()
    login(client, "sinacceso", "Sin-Acceso-2026!x")
    assert client.get("/api/dashboard/propiedades").json() == []
    reservas = client.get("/api/reservas")
    assert reservas.status_code == 200
    assert reservas.json() == []


# ---------- Administración de usuarios ----------

def test_crear_usuario_valida_password(client):
    login(client)
    response = post(client, "/api/usuarios", json={
        "username": "debil",
        "password": "corta",
        "rol": "lector",
    })
    assert response.status_code in (400, 422)


def test_actualizar_usuario(client):
    login(client)
    created = post(client, "/api/usuarios", json={
        "username": "editable",
        "password": "Editable-User-2026!x",
        "rol": "lector",
    })
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]

    updated = patch(client, f"/api/usuarios/{user_id}", json={
        "rol": "editor",
        "acceso_todas_propiedades": True,
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["rol"] == "editor"
    assert updated.json()["acceso_todas_propiedades"] == 1


def test_reset_password(client):
    login(client)
    created = post(client, "/api/usuarios", json={
        "username": "resetme",
        "password": "Reset-Me-2026!xyz",
        "rol": "lector",
    })
    user_id = created.json()["id"]
    response = post(client, f"/api/usuarios/{user_id}/reset-password", json={
        "password": "Nueva-Clave-2026!abc",
    })
    assert response.status_code == 200

    client.cookies.clear()
    login(client, "resetme", "Nueva-Clave-2026!abc")


# ---------- MFA y SSO ----------

def test_sso_deshabilitado_sin_configuracion(client):
    response = client.get("/api/auth/sso/status")
    assert response.status_code == 200
    assert response.json() == {"enabled": False}
    assert client.get("/api/auth/sso/login", follow_redirects=False).status_code == 404


def test_flujo_mfa_completo(client):
    login(client)
    created = post(client, "/api/usuarios", json={
        "username": "mfa_user",
        "password": "Mfa-User-2026!strong",
        "rol": "editor",
        "acceso_todas_propiedades": True,
    })
    assert created.status_code == 200, created.text

    client.cookies.clear()
    login(client, "mfa_user", "Mfa-User-2026!strong")

    setup = post(client, "/api/auth/mfa/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")

    enabled = post(client, "/api/auth/mfa/enable", json={"code": current_totp(secret)})
    assert enabled.status_code == 200, enabled.text
    backup_codes = enabled.json()["backup_codes"]
    assert len(backup_codes) == 10

    post(client, "/api/auth/logout")
    client.cookies.clear()

    second = client.post("/api/auth/login", json={
        "username": "mfa_user",
        "password": "Mfa-User-2026!strong",
    })
    assert second.status_code == 200
    payload = second.json()
    assert payload["mfa_required"] is True
    assert payload["mfa_token"]
    assert "norus_session" not in client.cookies

    wrong = client.post("/api/auth/mfa/verify", json={
        "mfa_token": payload["mfa_token"],
        "code": "000000",
    })
    assert wrong.status_code == 401

    # El código TOTP ya se usó al activar MFA (protección anti-replay);
    # se valida con un código de respaldo.
    verified = client.post("/api/auth/mfa/verify", json={
        "mfa_token": payload["mfa_token"],
        "code": backup_codes[0],
    })
    assert verified.status_code == 200, verified.text
    assert verified.json()["username"] == "mfa_user"
    assert "norus_session" in client.cookies

    # El código de respaldo es de un solo uso.
    client.cookies.clear()
    replay = client.post("/api/auth/mfa/verify", json={
        "mfa_token": payload["mfa_token"],
        "code": backup_codes[0],
    })
    assert replay.status_code == 401


def test_mfa_verificacion_limitada_por_intentos(client):
    """El segundo factor no permite fuerza bruta ilimitada de códigos."""
    login(client)
    created = post(client, "/api/usuarios", json={
        "username": "mfa_bruteforce",
        "password": "Mfa-Bruteforce-2026!x",
        "rol": "lector",
    })
    assert created.status_code == 200, created.text

    client.cookies.clear()
    login(client, "mfa_bruteforce", "Mfa-Bruteforce-2026!x")
    setup = post(client, "/api/auth/mfa/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    enabled = post(client, "/api/auth/mfa/enable", json={"code": current_totp(secret)})
    assert enabled.status_code == 200, enabled.text
    post(client, "/api/auth/logout")
    client.cookies.clear()

    token = client.post("/api/auth/login", json={
        "username": "mfa_bruteforce",
        "password": "Mfa-Bruteforce-2026!x",
    }).json()["mfa_token"]

    for _ in range(10):
        wrong = client.post("/api/auth/mfa/verify", json={"mfa_token": token, "code": "99999999"})
        assert wrong.status_code == 401, wrong.text

    blocked = client.post("/api/auth/mfa/verify", json={"mfa_token": token, "code": "99999999"})
    assert blocked.status_code == 429, blocked.text


# ---------- Operación hotelera ----------

def test_operacion_hotelera_crud_completo(client):
    """Cada módulo operativo debe crear, listar, resumir y cambiar de estado."""
    props = _seed_properties()
    pid = props["Propiedad Norte"]
    login(client)

    tarea = post(client, "/api/operaciones/tareas", json={
        "titulo": "Revisar habitaciones bloqueadas",
        "area": "Housekeeping",
        "prioridad": "alta",
        "asignado_a": "Equipo A",
        "propiedad_id": pid,
    })
    assert tarea.status_code == 201, tarea.text
    tarea_id = tarea.json()["id"]

    incidencia = post(client, "/api/operaciones/incidencias", json={
        "titulo": "Aire acondicionado sin funcionar",
        "categoria": "Climatización",
        "severidad": "critica",
        "area": "Habitaciones",
        "habitacion": "302",
        "propiedad_id": pid,
    })
    assert incidencia.status_code == 201, incidencia.text

    mantenimiento = post(client, "/api/operaciones/mantenimiento", json={
        "titulo": "Cambio de filtros",
        "tipo": "Preventivo",
        "prioridad": "media",
        "ubicacion": "Piso 3",
        "propiedad_id": pid,
    })
    assert mantenimiento.status_code == 201, mantenimiento.text

    inventario = post(client, "/api/operaciones/inventario", json={
        "nombre": "Toallas de baño",
        "categoria": "Lencería",
        "cantidad": 4,
        "stock_minimo": 10,
        "propiedad_id": pid,
    })
    assert inventario.status_code == 201, inventario.text
    inventario_id = inventario.json()["id"]

    objeto = post(client, "/api/operaciones/objetos-perdidos", json={
        "descripcion": "Cargador de teléfono negro",
        "categoria": "Electrónica",
        "ubicacion": "Lobby",
        "propiedad_id": pid,
    })
    assert objeto.status_code == 201, objeto.text

    checklist = post(client, "/api/operaciones/checklists", json={
        "nombre": "Apertura de recepción",
        "area": "Recepción",
        "elementos": ["Revisar caja", "Confirmar llegadas", "Actualizar pizarrón"],
        "propiedad_id": pid,
    })
    assert checklist.status_code == 201, checklist.text
    assert checklist.json()["elementos"] == ["Revisar caja", "Confirmar llegadas", "Actualizar pizarrón"]

    for module in ("tareas", "incidencias", "mantenimiento", "inventario", "objetos-perdidos", "checklists"):
        listing = client.get("/api/operaciones/" + module, params={"propiedad_id": pid})
        assert listing.status_code == 200, (module, listing.text)
        assert len(listing.json()) >= 1, module

    resumen = client.get("/api/operaciones/resumen", params={"propiedad_id": pid})
    assert resumen.status_code == 200, resumen.text
    data = resumen.json()
    assert data["tareas_pendientes"] >= 1
    assert data["incidencias_abiertas"] >= 1
    assert data["incidencias_criticas"] >= 1
    assert data["mantenimientos_pendientes"] >= 1
    assert data["inventario_bajo"] >= 1
    assert data["objetos_resguardados"] >= 1
    assert data["checklists_en_progreso"] >= 1

    estado = patch(client, f"/api/operaciones/tareas/{tarea_id}", json={"estado": "completada"})
    assert estado.status_code == 200, estado.text
    assert estado.json()["estado"] == "completada"

    stock = patch(client, f"/api/operaciones/inventario/{inventario_id}", json={"cantidad": 25})
    assert stock.status_code == 200, stock.text
    assert stock.json()["cantidad"] == 25


def test_operacion_hotelera_respeta_alcance(client):
    props = _seed_properties()
    login(client)
    created = post(client, "/api/operaciones/tareas", json={
        "titulo": "Tarea privada norte",
        "area": "Recepción",
        "propiedad_id": props["Propiedad Norte"],
    })
    assert created.status_code == 201, created.text

    assert post(client, "/api/usuarios", json={
        "username": "ops_scoped",
        "password": "Ops-Scoped-2026!x",
        "rol": "lector",
        "propiedades": [props["Propiedad Sur"]],
        "acceso_todas_propiedades": False,
    }).status_code == 200

    client.cookies.clear()
    login(client, "ops_scoped", "Ops-Scoped-2026!x")
    listing = client.get("/api/operaciones/tareas", params={"propiedad_id": props["Propiedad Sur"]})
    assert listing.status_code == 200, listing.text
    assert listing.json() == []
    denied = client.get("/api/operaciones/tareas", params={"propiedad_id": props["Propiedad Norte"]})
    assert denied.status_code == 403


# ---------- Habitaciones ----------

def _nuevo_brand(nombre: str, habitaciones: int = 0) -> int:
    db = TestingSessionLocal()
    brand = DimMarca(nombre=nombre, habitaciones=habitaciones)
    db.add(brand)
    db.commit()
    db.refresh(brand)
    brand_id = brand.id
    db.close()
    return brand_id


def test_resumen_habitaciones_sin_capacidad_no_inventa(client):
    """Sin capacidad declarada la ocupación por tipo debe ser null, no 0."""
    pid = _nuevo_brand("Habitaciones Sin Capacidad", 40)
    login(client)
    created = post(client, "/api/reservas", json={
        "codigo": "HAB-SIN-CAP-001",
        "huesped": "Huésped Sin Capacidad",
        "habitacion": "201",
        "tipo_habitacion": "Doble",
        "noches": 2,
        "valor": 400,
        "fecha": "2025-05-10",
        "moneda": "CLP",
        "propiedad_id": pid,
    })
    assert created.status_code == 200, created.text
    assert created.json()["habitacion"] == "201"

    response = client.get("/api/habitaciones/resumen", params={
        "propiedad_id": pid, "moneda": "CLP", "desde": "2025-05-01", "hasta": "2025-05-31",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    tipo = next(t for t in data["tipos"] if t["tipo_habitacion"] == "Doble")
    assert tipo["habitaciones"] is None
    assert tipo["ocupacion"] is None
    assert tipo["revpar"] is None
    assert tipo["noches_vendidas"] == 2
    assert tipo["adr"] == 200.0
    assert data["resumen"]["ocupacion"] is None
    detalle = data["detalle"]
    assert any(d["habitacion"] == "201" for d in detalle)


def test_capacidad_declarada_calcula_ocupacion_por_tipo(client):
    pid = _nuevo_brand("Habitaciones Con Capacidad", 40)
    login(client)
    created = post(client, "/api/reservas", json={
        "codigo": "HAB-CON-CAP-001",
        "huesped": "Huésped Con Capacidad",
        "habitacion": "305",
        "tipo_habitacion": "Suite",
        "noches": 2,
        "valor": 300,
        "fecha": "2025-05-10",
        "moneda": "CLP",
        "propiedad_id": pid,
    })
    assert created.status_code == 200, created.text

    capacity = post(client, "/api/habitaciones/capacidades", json={
        "marca_id": pid, "tipo_habitacion": "Suite", "habitaciones": 10,
    })
    assert capacity.status_code == 201, capacity.text
    assert capacity.json()["habitaciones"] == 10

    response = client.get("/api/habitaciones/resumen", params={
        "propiedad_id": pid, "moneda": "CLP", "desde": "2025-05-01", "hasta": "2025-05-31",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    tipo = next(t for t in data["tipos"] if t["tipo_habitacion"] == "Suite")
    assert tipo["habitaciones"] == 10
    assert tipo["ocupacion"] == round(2 / (10 * 31) * 100, 2)
    assert tipo["adr"] == 150.0
    assert tipo["revpar"] == round(300 / (10 * 31), 2)

    listing = client.get("/api/habitaciones/capacidades", params={"propiedad_id": pid})
    assert listing.status_code == 200, listing.text
    fila = next(r for r in listing.json() if r["tipo_habitacion"] == "Suite")
    deleted = delete(client, f"/api/habitaciones/capacidades/{fila['id']}")
    assert deleted.status_code == 200, deleted.text


def test_habitaciones_respeta_rol_y_propiedad(client):
    pid_norte = _nuevo_brand("Habitaciones Norte", 20)
    pid_sur = _nuevo_brand("Habitaciones Sur", 20)
    login(client)
    assert post(client, "/api/usuarios", json={
        "username": "rooms_lector",
        "password": "Rooms-Lector-2026!x",
        "rol": "lector",
        "propiedades": [pid_norte],
        "acceso_todas_propiedades": False,
    }).status_code == 200

    created = post(client, "/api/reservas", json={
        "codigo": "HAB-NORTE-001",
        "huesped": "Huésped Norte",
        "habitacion": "101",
        "tipo_habitacion": "Doble",
        "noches": 1,
        "valor": 100,
        "fecha": "2025-05-10",
        "moneda": "CLP",
        "propiedad_id": pid_norte,
    })
    assert created.status_code == 200, created.text

    client.cookies.clear()
    login(client, "rooms_lector", "Rooms-Lector-2026!x")

    resumen = client.get("/api/habitaciones/resumen", params={
        "propiedad_id": pid_norte, "moneda": "CLP", "desde": "2025-05-01", "hasta": "2025-05-31",
    })
    assert resumen.status_code == 200, resumen.text
    assert resumen.json()["detalle"][0]["huesped"] == ""
    assert resumen.json()["detalle"][0]["codigo"] == ""

    denied = client.get("/api/habitaciones/resumen", params={"propiedad_id": pid_sur, "moneda": "CLP"})
    assert denied.status_code == 403

    capacity = post(client, "/api/habitaciones/capacidades", json={
        "marca_id": pid_norte, "tipo_habitacion": "Doble", "habitaciones": 5,
    })
    assert capacity.status_code == 403


# ---------- Mercado ----------

def test_mercado_comparacion_largo_plazo(client):
    pid = _nuevo_brand("Mercado Comparacion", 30)
    db = TestingSessionLocal()
    db.add(HechoMetricaMensual(
        anio=2025, mes=4, marca_id=pid, moneda="CLP",
        ocupacion=10, adr=300, revpar=30, ingresos=3000,
        noches_vendidas=10, habitaciones_disponibles=100,
    ))
    db.add(ReferenciaMercado(
        marca_id=pid, fuente="Informe de prueba 2025-04", metrica="adr",
        periodo="2025-04", valor=250, moneda="CLP",
    ))
    db.commit()
    db.close()

    login(client)
    response = client.get("/api/mercado/comparacion", params={
        "propiedad_id": pid, "metrica": "adr", "moneda": "CLP",
        "desde": "2025-04", "hasta": "2025-04", "granularidad": "mes",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["periodos"] == ["2025-04"]
    assert data["hotel"] == [300.0]
    assert data["mercado"] == [250.0]
    assert data["brecha_pct"] == [20.0]


def test_mercado_valida_periodo_y_fuente(client):
    login(client)
    bad_period = post(client, "/api/mercado/referencias", json={
        "fuente": "Fuente inválida", "metrica": "adr", "periodo": "2025-13", "valor": 100,
    })
    assert bad_period.status_code == 422

    bad_url = post(client, "/api/mercado/referencias", json={
        "fuente": "Fuente URL", "metrica": "adr", "periodo": "2025-08", "valor": 100,
        "url": "ftp://example.com/precio",
    })
    assert bad_url.status_code == 422

    bad_occupancy = post(client, "/api/mercado/referencias", json={
        "fuente": "Fuente ocupación", "metrica": "ocupacion", "periodo": "2025-08", "valor": 150,
    })
    assert bad_occupancy.status_code == 422


def test_mercado_alcance_global_no_mezcla_propiedades(client):
    pid = _nuevo_brand("Mercado Alcance", 30)
    db = TestingSessionLocal()
    db.add(ReferenciaMercado(
        marca_id=None, fuente="Referencia global única", metrica="adr",
        periodo="2025-06", valor=500, moneda="CLP",
    ))
    db.add(ReferenciaMercado(
        marca_id=pid, fuente="Referencia propiedad única", metrica="adr",
        periodo="2025-06", valor=900, moneda="CLP",
    ))
    db.commit()
    db.close()

    login(client)
    global_only = client.get("/api/mercado/referencias", params={"metrica": "adr"})
    assert global_only.status_code == 200, global_only.text
    fuentes = {r["fuente"] for r in global_only.json()}
    assert "Referencia global única" in fuentes
    assert "Referencia propiedad única" not in fuentes

    scoped = client.get("/api/mercado/referencias", params={"metrica": "adr", "propiedad_id": pid})
    assert scoped.status_code == 200, scoped.text
    fuentes_scoped = {r["fuente"] for r in scoped.json()}
    assert {"Referencia global única", "Referencia propiedad única"} <= fuentes_scoped


def test_mercado_importar_csv_con_fuente_y_validacion(client):
    login(client)
    csv_content = (
        b"fuente,metrica,periodo,valor,moneda,url\n"
        b"Importacion Mercado Unica,adr,2025-07,320,CLP,https://example.com/informe\n"
        b"Importacion Mercado Unica,adr,2025-14,320,CLP,\n"
    )
    response = post(client, "/api/mercado/importar", files={
        "file": ("mercado.csv", csv_content, "text/csv"),
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["exitosas"] == 1
    assert data["fallidas"] == 1
    assert data["estado"] == "parcial"


# ---------- Incidencias vinculadas a huéspedes ----------

def test_contexto_incidencia_por_habitacion_y_vinculo_explicito(client):
    pid = _nuevo_brand("Incidencias Contexto", 25)
    login(client)
    hoy = date.today().isoformat()
    reserva = post(client, "/api/reservas", json={
        "codigo": "CTX-RES-001",
        "huesped": "Huésped Contexto",
        "habitacion": "507",
        "tipo_habitacion": "Suite",
        "noches": 3,
        "valor": 500,
        "fecha": hoy,
        "moneda": "CLP",
        "propiedad_id": pid,
    })
    assert reserva.status_code == 200, reserva.text
    reserva_id = reserva.json()["id"]

    incidencia = post(client, "/api/operaciones/incidencias", json={
        "titulo": "Fuga de agua en el baño",
        "categoria": "Plomería",
        "habitacion": "507",
        "propiedad_id": pid,
    })
    assert incidencia.status_code == 201, incidencia.text
    incidencia_id = incidencia.json()["id"]

    contexto = client.get(f"/api/operaciones/incidencias/{incidencia_id}/contexto")
    assert contexto.status_code == 200, contexto.text
    data = contexto.json()
    assert data["vinculo"] == "habitacion_fecha"
    assert data["reserva"]["id"] == reserva_id
    assert data["reserva"]["huesped"] == "Huésped Contexto"
    assert data["pii_visible"] is True

    linked = patch(
        client, f"/api/operaciones/incidencias/{incidencia_id}/reserva",
        json={"reserva_id": reserva_id},
    )
    assert linked.status_code == 200, linked.text

    contexto_explicito = client.get(f"/api/operaciones/incidencias/{incidencia_id}/contexto")
    assert contexto_explicito.json()["vinculo"] == "explicito"


def test_contexto_incidencia_oculta_pii_para_lector(client):
    pid = _nuevo_brand("Incidencias Lector", 25)
    login(client)
    reserva = post(client, "/api/reservas", json={
        "codigo": "CTX-LECTOR-001",
        "huesped": "Huésped Privado",
        "habitacion": "808",
        "noches": 2,
        "valor": 800,
        "fecha": date.today().isoformat(),
        "moneda": "CLP",
        "propiedad_id": pid,
    })
    assert reserva.status_code == 200, reserva.text
    incidencia = post(client, "/api/operaciones/incidencias", json={
        "titulo": "Ruido en pasillo",
        "categoria": "Servicio",
        "habitacion": "808",
        "propiedad_id": pid,
    })
    assert incidencia.status_code == 201, incidencia.text
    incidencia_id = incidencia.json()["id"]

    assert post(client, "/api/usuarios", json={
        "username": "ctx_lector",
        "password": "Ctx-Lector-2026!x",
        "rol": "lector",
        "propiedades": [pid],
        "acceso_todas_propiedades": False,
    }).status_code == 200

    client.cookies.clear()
    login(client, "ctx_lector", "Ctx-Lector-2026!x")
    contexto = client.get(f"/api/operaciones/incidencias/{incidencia_id}/contexto")
    assert contexto.status_code == 200, contexto.text
    data = contexto.json()
    assert data["pii_visible"] is False
    assert data["reserva"]["huesped"] == ""
    assert data["reserva"]["codigo"] == ""
    assert data["reserva"]["valor"] is None

    denied = patch(
        client, f"/api/operaciones/incidencias/{incidencia_id}/reserva",
        json={"reserva_id": reserva.json()["id"]},
    )
    assert denied.status_code == 403


# ---------- ETL: número de habitación ----------

def test_etl_mapea_numero_de_habitacion_y_tipo(client):
    login(client)
    csv_content = (
        "codigo,huesped,habitacion,tipo_habitacion,fecha,valor,moneda,marca\n"
        "ETL-HAB-001,Ana,812,Suite,2025-08-01,100,CLP,ETL Propiedad Habitaciones\n"
        "ETL-HAB-002,Bea,Deluxe,,2025-08-02,100,CLP,ETL Propiedad Habitaciones\n"
    ).encode("utf-8")
    response = post(client, "/api/importar", files={
        "file": ("habitaciones.csv", csv_content, "text/csv"),
    })
    assert response.status_code == 200, response.text
    assert response.json()["filas_exitosas"] == 2

    listing = client.get("/api/reservas", params={"busqueda": "ETL-HAB"})
    assert listing.status_code == 200, listing.text
    por_codigo = {r["codigo"]: r for r in listing.json()}
    assert por_codigo["ETL-HAB-001"]["habitacion"] == "812"
    assert por_codigo["ETL-HAB-001"]["tipo_habitacion"] == "Suite"
    assert por_codigo["ETL-HAB-002"]["habitacion"] == ""
    assert por_codigo["ETL-HAB-002"]["tipo_habitacion"] == "Deluxe"


# ---------- Importación multi-formato con datos demo ----------

def _demo_rows(limit=25):
    import csv
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "data" / "demo" / "reservas_demo.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        rows = []
        for index, row in enumerate(reader):
            if index >= limit:
                break
            rows.append({key: row[key] for key in header})
    return header, rows


@pytest.mark.parametrize("formato", ["csv", "tsv", "json", "xlsx"])
def test_importa_demo_en_todos_los_formatos(client, formato):
    import csv
    import io
    import json

    login(client)
    header, rows = _demo_rows()
    if formato in {"csv", "tsv"}:
        delimiter = "," if formato == "csv" else "\t"
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=header, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)
        content = out.getvalue().encode("utf-8")
        mime = "text/csv"
    elif formato == "json":
        content = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        mime = "application/json"
    else:
        from openpyxl import Workbook

        wb = Workbook(write_only=True)
        ws = wb.create_sheet("demo")
        ws.append(header)
        for row in rows:
            ws.append([row[key] for key in header])
        buffer = io.BytesIO()
        wb.save(buffer)
        content = buffer.getvalue()
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    response = post(
        client,
        "/api/importar",
        files={"file": (f"demo_subset.{formato}", content, mime)},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["filas_procesadas"] == len(rows)
    assert data["filas_exitosas"] == len(rows), data.get("mensaje_error")
    assert data["estado"] == "completada"


# ---------- APIs públicas de mercado ----------

def test_fuentes_publicas_sin_clave(client):
    login(client)
    response = client.get("/api/mercado/publico/fuentes")
    assert response.status_code == 200, response.text
    keys = {item["clave"] for item in response.json()}
    assert {"ea_hospitality_pulse", "apartments_zuerich", "open_er_api"} <= keys


def test_url_publica_valida_lista_blanca():
    from app import mercado_publico

    assert mercado_publico._validate_url(
        "https://eahospitalitypulse.com/api/v1/rate-observations.json"
    )
    with pytest.raises(ValueError):
        mercado_publico._validate_url("https://evil.example/precios.json")
    with pytest.raises(ValueError):
        mercado_publico._validate_url("http://eahospitalitypulse.com/api/v1/index.json")


def test_sincroniza_mercado_publico_y_compara(client, monkeypatch):
    from app import mercado_publico

    def fake_fetch_json(url):
        if "rate-observations" in url:
            return {"data": [
                {"market": "santiago", "stay_date": "2026-11-12", "rate_usd": 100},
                {"market": "santiago", "stay_date": "2026-11-19", "rate_usd": 120},
            ]}
        if "apartments.zuerich" in url:
            return {
                "prices": [
                    {"apartment": "4101", "priceLongTerm": 2000},
                    {"apartment": "4102", "priceLongTerm": None},
                    {"apartment": "4105", "priceLongTerm": 1800},
                ],
                "priceMetadata": {"asOf": "2026-11-18", "currency": "CHF"},
            }
        if "open.er-api.com" in url:
            return {"result": "success", "rates": {"CLP": 900.0, "CHF": 0.8}}
        raise AssertionError(f"URL no esperada: {url}")

    monkeypatch.setattr(mercado_publico, "fetch_json", fake_fetch_json)
    # Esta prueba usa fuentes simuladas: la fuente real de Chile (INE-EMAT)
    # se prueba por separado y no debe consultarse en este escenario.
    monkeypatch.setattr(
        mercado_publico, "enabled_sources",
        lambda: ["ea_hospitality_pulse", "apartments_zuerich"],
    )
    mercado_publico.clear_cache()
    login(client)
    response = post(client, "/api/mercado/publico/actualizar", params={"moneda": "CLP"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["estado"] == "completada"
    assert result["creadas"] == 3
    assert result["referencias_recibidas"] == 3
    assert all(item["estado"] == "ok" for item in result["fuentes"])

    comparison = client.get(
        "/api/mercado/comparacion",
        params={"metrica": "adr", "moneda": "CLP", "desde": "2026-11"},
    )
    assert comparison.status_code == 200, comparison.text
    assert "EA Hospitality Pulse" in {
        ref["fuente"] for ref in comparison.json()["referencias"]
    }


# ---------- Paginación real y mercado en casi tiempo real ----------

def test_listar_reservas_con_total_para_paginacion(client):
    login(client)
    creada = post(client, "/api/reservas", json={
        "codigo": "RES-PAG-001",
        "huesped": "Paginación Real",
        "estado": "Confirmada",
        "valor": 1000.0,
        "noches": 2,
        "fecha": date.today().isoformat(),
        "canal": "Canal Paginación",
        "nacionalidad": "Chile",
    })
    assert creada.status_code == 200, creada.text

    page = client.get("/api/reservas", params={"skip": 0, "limit": 2, "con_total": "true"})
    assert page.status_code == 200, page.text
    data = page.json()
    assert set(data) == {"items", "total", "canales"}
    assert len(data["items"]) <= 2
    assert data["total"] >= len(data["items"])
    assert "Canal Paginación" in data["canales"]

    page2 = client.get("/api/reservas", params={"skip": 2, "limit": 2, "con_total": "true"})
    ids = {r["id"] for r in data["items"]}
    assert not ids.intersection({r["id"] for r in page2.json()["items"]})


def test_mercado_tiempo_real_con_cache_y_refresco(client, monkeypatch):
    from app import mercado_publico, mercado_tiempo_real

    monkeypatch.setattr(mercado_publico, "enabled_sources", lambda: [])
    monkeypatch.setattr(mercado_tiempo_real, "_load_connectors", lambda: [])
    monkeypatch.setenv("NORUS_MARKET_CONNECTORS", "")

    db = TestingSessionLocal()
    try:
        db.add(ReferenciaMercado(
            fuente="Referencia de prueba",
            metrica="adr",
            periodo=date.today().strftime("%Y-%m"),
            valor=100.0,
            moneda="CLP",
        ))
        db.commit()
    finally:
        db.close()

    login(client)
    r = client.get("/api/mercado/tiempo-real", params={"moneda": "CLP", "ventana": "24h"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["moneda"] == "CLP"
    assert data["cache"] == "miss"
    assert {"interno", "externo", "comparativa", "senales"} <= set(data)

    hit = client.get("/api/mercado/tiempo-real", params={"moneda": "CLP", "ventana": "24h"})
    assert hit.json()["cache"] == "hit"

    forzado = client.get(
        "/api/mercado/tiempo-real",
        params={"moneda": "CLP", "ventana": "24h", "refresh": "1"},
    )
    assert forzado.json()["cache"] == "miss"


def test_mercado_tiempo_real_normaliza_estados_de_fuentes_publicas(client, monkeypatch):
    from app import mercado_publico, mercado_tiempo_real

    def fake_fetch_json(url, cache_ttl=None, force=False):
        if "rate-observations" in url:
            return {"data": [
                {"market": "santiago", "stay_date": date.today().isoformat(), "rate_usd": 100},
            ]}
        if "apartments.zuerich" in url:
            return {"prices": [{"apartment": "4101", "priceLongTerm": 2000}],
                    "priceMetadata": {"asOf": date.today().isoformat(), "currency": "CHF"}}
        if "open.er-api.com" in url:
            return {"result": "success", "rates": {"CLP": 900.0, "CHF": 0.8}}
        raise AssertionError(f"URL no esperada: {url}")

    monkeypatch.setattr(mercado_publico, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(
        mercado_publico, "enabled_sources",
        lambda: ["ea_hospitality_pulse", "apartments_zuerich"],
    )
    monkeypatch.setattr(mercado_tiempo_real, "_load_connectors", lambda: [])
    mercado_publico.clear_cache()
    login(client)

    respuesta = client.get(
        "/api/mercado/tiempo-real", params={"moneda": "CLP", "ventana": "7d"}
    )
    assert respuesta.status_code == 200, respuesta.text
    data = respuesta.json()
    fuentes = data["externo"]["fuentes"]
    assert fuentes and all(isinstance(f, dict) for f in fuentes)
    assert any(f["estado"] == "ok" for f in fuentes)
    assert data["externo"]["observaciones"] > 0
    assert data["externo"]["origen"] == "en_vivo"


def test_token_embed_solo_autoriza_tiempo_real(client, monkeypatch):
    monkeypatch.setenv("NORUS_EMBED_ALLOWED_ORIGINS", "https://padre.example.com")
    login(client)

    config = client.get("/api/embed/config")
    assert config.status_code == 200
    assert config.json()["habilitado"] is True

    rechazado = post(client, "/api/embed/token", json={
        "parent_origin": "https://otro.example.com",
        "moneda": "CLP",
    })
    assert rechazado.status_code == 403

    emitido = post(client, "/api/embed/token", json={
        "parent_origin": "https://padre.example.com",
        "moneda": "CLP",
        "minutos": 60,
    })
    assert emitido.status_code == 200, emitido.text
    payload = emitido.json()
    assert payload["expires_en_minutos"] == 60
    assert "<iframe" in payload["nota"]
    assert "postMessage" in payload["nota"]

    headers = {"Authorization": "Bearer " + payload["token"]}
    permitido = client.get("/api/mercado/tiempo-real", headers=headers, params={"moneda": "CLP"})
    assert permitido.status_code == 200, permitido.text

    denegado = client.get("/api/dashboard", headers=headers)
    assert denegado.status_code == 401


def test_embed_html_sin_login_y_csp(client, monkeypatch):
    monkeypatch.setenv("NORUS_EMBED_ALLOWED_ORIGINS", "https://padre.example.com")
    client.cookies.clear()
    widget = client.get("/embed.html")
    assert widget.status_code == 200, widget.text
    assert "norus:token" in widget.text or "js/embed.js" in widget.text
    csp = widget.headers.get("content-security-policy", "")
    assert "frame-ancestors https://padre.example.com" in csp
    assert "x-frame-options" not in {k.lower() for k in widget.headers}


# ---------- Datos de demostración: visibles y borrables ----------

def test_demo_cargar_estado_y_borrar(client, monkeypatch):
    """La demo usa datos reales; se simula la red para que la prueba sea rápida."""
    from pathlib import Path

    from app import mercado_publico
    from scripts import seed_demo_real

    encabezado = (
        "hotel,is_canceled,lead_time,arrival_date_year,arrival_date_month,"
        "arrival_date_week_number,arrival_date_day_of_month,stays_in_weekend_nights,"
        "stays_in_week_nights,adults,children,babies,meal,country,market_segment,"
        "distribution_channel,is_repeated_guest,previous_cancellations,"
        "previous_bookings_not_canceled,reserved_room_type,assigned_room_type,"
        "booking_changes,deposit_type,agent,company,days_in_waiting_list,"
        "customer_type,adr,required_car_parking_spaces,total_of_special_requests,"
        "reservation_status,reservation_status_date\n"
    )
    meses_ingles = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    filas_csv = []
    for i in range(130):
        mes = i % 12 + 1
        nombre_mes = meses_ingles[mes - 1]
        filas_csv.append(
            f"City Hotel,0,10,2025,{nombre_mes},1,{i % 28 + 1},1,2,2,0,0,BB,CHL,"
            f"Direct,Direct,0,0,0,A,A,0,No Deposit,NULL,NULL,0,Transient,100,0,0,"
            f"Check-Out,2025-{mes:02d}-{i % 28 + 1:02d}\n"
        )

    def fake_descargar(destino):
        Path(destino).write_text(encabezado + "".join(filas_csv), encoding="utf-8")

    monkeypatch.setattr(seed_demo_real, "_descargar_csv", fake_descargar)
    monkeypatch.setattr(mercado_publico, "fx_rate", lambda *a, **k: 900.0)
    monkeypatch.setattr(
        mercado_publico, "fetch_public_references",
        lambda moneda, fuentes=None, cache_ttl=None, force=False: ([], []),
    )

    login(client)

    borrado = delete(client, "/api/demo")
    assert borrado.status_code == 200, borrado.text
    assert borrado.json()["activo"] is False

    carga = post(client, "/api/demo/cargar", json={"filas": 120})
    assert carga.status_code == 200, carga.text
    datos = carga.json()
    assert datos["activo"] is True
    assert datos["filas"] == 120
    assert datos["reservas"] == 120
    assert datos["cargas"]

    estado = client.get("/api/demo/estado")
    assert estado.status_code == 200
    assert estado.json()["activo"] is True

    # Las propiedades del dataset real aparecen en el catálogo y en los filtros.
    propiedades = client.get("/api/operaciones/propiedades")
    assert propiedades.status_code == 200
    nombres = [p["nombre"] for p in propiedades.json()]
    assert any("Demo real · City Hotel" in n for n in nombres)

    # El borrado no elimina usuarios ni auditoría.
    borrado = delete(client, "/api/demo")
    assert borrado.status_code == 200, borrado.text
    assert borrado.json()["activo"] is False
    assert borrado.json()["reservas"] == 0

    usuarios = client.get("/api/usuarios")
    assert usuarios.status_code == 200
    assert any(u["username"] == ADMIN_USER for u in usuarios.json())


def test_demo_borrado_requiere_admin(client):
    db = TestingSessionLocal()
    try:
        db.add(Usuario(
            username="lector_demo", password_hash=hash_password("lectorpass-lectorpass"), rol="lector"
        ))
        db.commit()
    finally:
        db.close()
    login(client, "lector_demo", "lectorpass-lectorpass")
    respuesta = delete(client, "/api/demo")
    assert respuesta.status_code == 403


# ---------- Importación avanzada: límites, codificaciones y fragmentos ----------

def test_config_importacion_permite_mas_de_100000_filas(client):
    response = client.get("/api/importar/config")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["max_rows"] >= 100_000
    assert data["max_bytes"] >= 100 * 1024 * 1024
    assert 0 < data["chunk_bytes"] <= 4 * 1024 * 1024
    assert {".csv", ".tsv", ".xlsx", ".json"} <= set(data["extensiones"])


@pytest.mark.parametrize("encoding,delimiter", [("utf-8-sig", ";"), ("utf-16", ",")])
def test_importa_csv_con_bom_y_codificaciones(client, encoding, delimiter):
    import io

    login(client)
    header = ["codigo", "huesped", "fecha", "marca", "moneda", "ingresos", "noches"]
    lineas = [delimiter.join(header)]
    for i in range(1, 4):
        lineas.append(delimiter.join([
            f"BOM-{encoding}-{i}", f"Huesped {i}", "2026-03-05", "Marca BOM", "CLP", "120000", "2",
        ]))
    contenido = ("\n".join(lineas) + "\n").encode(encoding)
    response = post(client, "/api/importar", files={
        "file": (f"bom_{encoding}.csv", contenido, "text/csv"),
    })
    assert response.status_code == 200, response.text
    assert response.json()["filas_exitosas"] == 3, response.json()


def test_importa_json_ndjson_y_detecta_formato_por_contenido(client):
    import json as json_lib

    from app.etl import detectar_formato, leer_tabla

    login(client)
    registros = [
        {"codigo": "NDJSON-1", "fecha": "2026-04-01", "marca": "Marca ND", "moneda": "CLP", "ingresos": 90000, "noches": 1},
        {"codigo": "NDJSON-2", "fecha": "2026-04-02", "marca": "Marca ND", "moneda": "CLP", "ingresos": 95000, "noches": 1},
    ]
    contenido = ("\n".join(json_lib.dumps(r) for r in registros)).encode("utf-8")
    assert detectar_formato("sin_extension", contenido) == "json"
    filas, columnas = leer_tabla("sin_extension", contenido)
    assert len(filas) == 2 and "codigo" in columnas

    response = post(client, "/api/importar", files={
        "file": ("registros.json", contenido, "application/x-ndjson"),
    })
    assert response.status_code == 200, response.text
    assert response.json()["filas_exitosas"] == 2, response.json()


def test_subida_fragmentada_extremo_a_extremo(client, monkeypatch):
    import hashlib

    monkeypatch.setattr("app.main.UPLOAD_CHUNK_BYTES", 64)
    login(client)
    contenido = (
        "codigo,huesped,fecha,marca,moneda,ingresos,noches\n"
        "CHUNK-1,Ana,2026-05-01,Marca Chunk,CLP,100000,1\n"
        "CHUNK-2,Bea,2026-05-02,Marca Chunk,CLP,110000,1\n"
    ).encode("utf-8")
    total = (len(contenido) + 63) // 64
    assert total >= 2
    creacion = post(client, "/api/importar/sesiones", json={
        "nombre_archivo": "fragmentado.csv",
        "tamano_bytes": len(contenido),
        "total_chunks": total,
        "sha256": hashlib.sha256(contenido).hexdigest(),
        "mapeo": {},
    })
    assert creacion.status_code == 200, creacion.text
    upload_id = creacion.json()["upload_id"]

    for indice in range(total):
        parte = contenido[indice * 64:(indice + 1) * 64]
        respuesta = post(client, f"/api/importar/sesiones/{upload_id}/chunks/{indice}", files={
            "file": (f"parte{indice}", parte, "application/octet-stream"),
        })
        assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["recibidos"] == total

    final = post(client, f"/api/importar/sesiones/{upload_id}/finalizar")
    assert final.status_code == 200, final.text
    assert final.json()["filas_exitosas"] == 2, final.json()
    assert final.json()["nombre_archivo"] == "fragmentado.csv"


def test_subida_fragmentada_rechaza_hash_incorrecto(client, monkeypatch):
    import hashlib

    monkeypatch.setattr("app.main.UPLOAD_CHUNK_BYTES", 64)
    login(client)
    contenido = b"codigo,fecha,marca\nabc,2026-01-01,Marca Hash\n"
    total = (len(contenido) + 63) // 64
    creacion = post(client, "/api/importar/sesiones", json={
        "nombre_archivo": "hash.csv",
        "tamano_bytes": len(contenido),
        "total_chunks": total,
        "sha256": "0" * 64,
        "mapeo": {},
    })
    assert creacion.status_code == 200, creacion.text
    upload_id = creacion.json()["upload_id"]
    for indice in range(total):
        respuesta = post(client, f"/api/importar/sesiones/{upload_id}/chunks/{indice}", files={
            "file": (f"parte{indice}", contenido[indice * 64:(indice + 1) * 64], "application/octet-stream"),
        })
        assert respuesta.status_code == 200, respuesta.text
    final = post(client, f"/api/importar/sesiones/{upload_id}/finalizar")
    assert final.status_code == 409
    assert "SHA-256" in final.json()["detail"]


def test_etl_procesa_mas_de_100000_filas():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.etl import ejecutar_carga

    filas = 100_001
    contenido = (
        "fecha,marca,moneda,ocupacion,habitaciones_disp,adr\n"
        + "2026-06-15,Marca Masiva,CLP,70,100,50000\n" * filas
    ).encode("utf-8")
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    sesion = sessionmaker(bind=engine)()
    try:
        carga = ejecutar_carga(sesion, "masivo.csv", contenido, "test", {})
        assert carga.estado == "completada", carga.mensaje_error
        assert carga.filas_procesadas == filas
        assert carga.filas_exitosas == filas, carga.mensaje_error
        assert carga.filas_fallidas == 0
    finally:
        sesion.close()
        engine.dispose()


def test_parser_ine_emat_indicadores_reales(monkeypatch):
    """El XLSX oficial se convierte en ocupación/ADR/RevPAR verificables."""
    from io import BytesIO

    from openpyxl import Workbook

    from app import mercado_publico

    libro = Workbook()
    libro.remove(libro.active)
    for hoja, valor in (("16", 40.5), ("22", 30000.0), ("25", 80000.0)):
        ws = libro.create_sheet(hoja)
        ws.append(["Cuadro de prueba"])
        ws.append([None])
        ws.append(["Región / Destino turístico", "Jul-26", "Ago-26/R"])
        ws.append(["Total nacional", valor, valor * 1.1])
    destino = BytesIO()
    libro.save(destino)

    monkeypatch.setattr(mercado_publico, "fetch_bytes", lambda *a, **k: destino.getvalue())
    mercado_publico.clear_cache()
    refs = mercado_publico._ine_emat_references("CLP", cache_ttl=0, force=True)

    assert {ref["metrica"] for ref in refs} == {"ocupacion", "adr", "revpar"}
    assert {ref["periodo"] for ref in refs} == {"2026-07", "2026-08"}
    assert all(
        ref["moneda"] == "CLP" and ref["url"].startswith("https://www.ine.gob.cl")
        for ref in refs
    )
    assert any(ref["metrica"] == "ocupacion" and ref["valor"] == 40.5 for ref in refs)


def test_cron_mercado_diario_requiere_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "secreto-de-prueba")
    assert client.post("/api/cron/mercado-diario").status_code == 401
    respuesta = client.post(
        "/api/cron/mercado-diario",
        headers={"Authorization": "Bearer secreto-incorrecto"},
    )
    assert respuesta.status_code == 401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
