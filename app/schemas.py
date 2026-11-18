# -*- coding: utf-8 -*-
"""============================================================
   NORUS & LUUM · Esquemas Pydantic (validación de API)
   ============================================================"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Literal
from pydantic import field_validator

from pydantic import BaseModel, Field, ConfigDict


# ============================================================
# AUTENTICACIÓN
# ============================================================

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    # Legacy accounts may predate the 12-character policy; rate limiting and
    # bcrypt still protect the login endpoint. New passwords are validated
    # server-side by ``validate_password_strength``.
    password: str = Field(min_length=1, max_length=72)


class LoginResponse(BaseModel):
    username: str = ""
    rol: str = ""
    mfa_required: bool = False
    mfa_token: Optional[str] = None


class UsuarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    email: Optional[str] = None
    rol: str
    activo: int
    mfa_habilitado: int = 0
    acceso_todas_propiedades: int = 0
    propiedades: list[int] = Field(default_factory=list)


class UsuarioCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=12, max_length=72)
    email: Optional[str] = Field(default=None, max_length=255)
    rol: str = "lector"
    propiedades: list[int] = Field(default_factory=list)
    acceso_todas_propiedades: bool = False


class UsuarioUpdate(BaseModel):
    email: Optional[str] = Field(default=None, max_length=255)
    rol: Optional[Literal["admin", "editor", "lector"]] = None
    activo: Optional[bool] = None
    propiedades: Optional[list[int]] = None
    acceso_todas_propiedades: Optional[bool] = None


class PasswordChange(BaseModel):
    password_actual: str = Field(min_length=1, max_length=72)
    password_nueva: str = Field(min_length=12, max_length=72)


class MFAVerifyRequest(BaseModel):
    mfa_token: str = Field(min_length=20, max_length=4096)
    code: str = Field(min_length=6, max_length=32)


class MFAEnableRequest(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class MFADisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=72)
    code: str = Field(min_length=6, max_length=32)


class MFASetupResponse(BaseModel):
    secret: str
    otpauth_uri: str
    issuer: str


class MFAEnableResponse(BaseModel):
    ok: bool = True
    backup_codes: list[str] = Field(default_factory=list)



# ============================================================
# RESERVAS (CRUD)
# ============================================================

class ReservaCreate(BaseModel):
    codigo: str = Field(default="", max_length=50)
    huesped: str = Field(default="", max_length=200)
    habitacion: str = Field(default="", max_length=30)
    estado: str = Field(default="Confirmada", max_length=50)
    valor: float = Field(default=0.0, ge=0, le=1000000000000)
    noches: float = Field(default=0.0, ge=0, le=100000)
    anticipacion_dias: float = Field(default=0.0, ge=0, le=100000)
    moneda: str = Field(default="CLP", pattern=r"^[A-Z]{3}$")
    fecha: Optional[str] = None
    canal: Optional[str] = Field(default=None, max_length=100)
    nacionalidad: Optional[str] = Field(default=None, max_length=100)
    marca: Optional[str] = Field(default=None, max_length=100)
    propiedad_id: Optional[int] = Field(default=None, ge=1)
    segmento: Optional[str] = Field(default=None, max_length=100)
    tipo_habitacion: Optional[str] = Field(default=None, max_length=100)

    @field_validator("fecha")
    @classmethod
    def validar_fecha(cls, value):
        if value is not None:
            datetime.strptime(value, "%Y-%m-%d")
        return value


class ReservaUpdate(BaseModel):
    codigo: Optional[str] = Field(default=None, max_length=50)
    huesped: Optional[str] = Field(default=None, max_length=200)
    habitacion: Optional[str] = Field(default=None, max_length=30)
    estado: Optional[str] = Field(default=None, max_length=50)
    valor: Optional[float] = Field(default=None, ge=0, le=1000000000000)
    noches: Optional[float] = Field(default=None, ge=0, le=100000)
    anticipacion_dias: Optional[float] = Field(default=None, ge=0, le=100000)
    moneda: Optional[str] = Field(default=None, pattern=r"^[A-Z]{3}$")
    fecha: Optional[str] = None
    canal: Optional[str] = Field(default=None, max_length=100)
    nacionalidad: Optional[str] = Field(default=None, max_length=100)
    marca: Optional[str] = Field(default=None, max_length=100)
    propiedad_id: Optional[int] = Field(default=None, ge=1)
    segmento: Optional[str] = Field(default=None, max_length=100)
    tipo_habitacion: Optional[str] = Field(default=None, max_length=100)

    @field_validator("fecha")
    @classmethod
    def validar_fecha(cls, value):
        if value is not None:
            datetime.strptime(value, "%Y-%m-%d")
        return value


class ReservaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    codigo: str
    huesped: str
    habitacion: str = ""
    estado: str
    valor: float
    noches: float
    anticipacion_dias: float
    moneda: str = "CLP"
    fecha: Optional[str] = None
    canal: Optional[str] = None
    nacionalidad: Optional[str] = None
    marca: Optional[str] = None
    propiedad_id: Optional[int] = None
    segmento: Optional[str] = None
    tipo_habitacion: Optional[str] = None


# ============================================================
# IMPORTACIÓN / ETL
# ============================================================

class ImportacionResponse(BaseModel):
    carga_id: int
    nombre_archivo: str
    formato: str
    filas_procesadas: int
    filas_exitosas: int
    filas_fallidas: int
    estado: str
    mensaje: str = ""


class CargaArchivoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nombre_archivo: str
    formato: str
    tamano_bytes: int
    filas_procesadas: int
    filas_exitosas: int
    filas_fallidas: int
    estado: str
    mensaje_error: str
    usuario: str
    creado_en: datetime


# ============================================================
# AUDITORÍA
# ============================================================

class EventoAuditoriaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    accion: str
    usuario: str
    detalle: str
    ip: str
    creado_en: datetime


# ============================================================
# DASHBOARD / KPIs
# ============================================================

class DashboardData(BaseModel):
    moneda: str = "CLP"
    granularidad: str = "mes"
    desde: Optional[str] = None
    hasta: Optional[str] = None
    propiedad_id: Optional[int] = None
    reservas_total: int = 0
    kpi_reservas: dict = Field(default_factory=dict)
    meses: list[str]
    ocupacion_mensual: list[Optional[float]]
    adr_mensual: list[Optional[float]]
    revpar_mensual: list[Optional[float]]
    ingresos_mensuales: list[Optional[float]]
    canales: dict
    nacionalidad: dict
    segmentos: dict
    anticipacion: dict
    incidencias: dict
    satisfaccion: dict
    habitaciones: dict
    kpi: dict
    reservas: list[ReservaResponse]
    marcas: list[str]
    ocupacion_marca: list[list[Optional[float]]]
    adr_marca: list[list[Optional[float]]]
    ingresos_marca: list[list[Optional[float]]]
    marca_colores: list[str]
    kpi_marca: list[dict]
    temporadas: dict
    revenue: dict
    nacionalidad_marca: dict
    nps: dict
    dia_semana: dict
    aportacion_canal: dict


# ============================================================
# OPERACIONES HOTELERAS
# ============================================================

class TareaCreate(BaseModel):
    titulo: str = Field(min_length=3, max_length=180)
    descripcion: str = Field(default="", max_length=4000)
    area: str = Field(default="Recepción", min_length=2, max_length=60)
    prioridad: Literal["baja", "media", "alta", "critica"] = "media"
    asignado_a: str = Field(default="", max_length=100)
    propiedad_id: int | None = Field(default=None, ge=1)
    vence_en: datetime | None = None


class EstadoUpdate(BaseModel):
    estado: str = Field(min_length=2, max_length=20)


class IncidenciaCreate(BaseModel):
    titulo: str = Field(min_length=3, max_length=180)
    detalle: str = Field(default="", max_length=4000)
    categoria: str = Field(default="Servicio", min_length=2, max_length=60)
    severidad: Literal["baja", "media", "alta", "critica"] = "media"
    area: str = Field(default="Recepción", min_length=2, max_length=60)
    habitacion: str = Field(default="", max_length=30)
    # Vínculo explícito opcional con la reserva del huésped alojado.
    reserva_id: int | None = Field(default=None, ge=1)
    responsable: str = Field(default="", max_length=100)
    propiedad_id: int | None = Field(default=None, ge=1)


class MantenimientoCreate(BaseModel):
    titulo: str = Field(min_length=3, max_length=180)
    detalle: str = Field(default="", max_length=4000)
    tipo: Literal["Preventivo", "Correctivo", "Inspección"] = "Correctivo"
    prioridad: Literal["baja", "media", "alta", "critica"] = "media"
    ubicacion: str = Field(default="", max_length=100)
    responsable: str = Field(default="", max_length=100)
    propiedad_id: int | None = Field(default=None, ge=1)


class InventarioCreate(BaseModel):
    nombre: str = Field(min_length=2, max_length=140)
    categoria: str = Field(default="General", max_length=60)
    unidad: str = Field(default="unidad", max_length=30)
    cantidad: float = Field(default=0, ge=0, le=100000000)
    stock_minimo: float = Field(default=0, ge=0, le=100000000)
    proveedor: str = Field(default="", max_length=120)
    propiedad_id: int | None = Field(default=None, ge=1)


class StockUpdate(BaseModel):
    cantidad: float = Field(ge=0, le=100000000)


class ObjetoPerdidoCreate(BaseModel):
    descripcion: str = Field(min_length=3, max_length=240)
    categoria: str = Field(default="General", max_length=60)
    ubicacion: str = Field(default="", max_length=100)
    responsable: str = Field(default="", max_length=100)
    propiedad_id: int | None = Field(default=None, ge=1)


class ChecklistCreate(BaseModel):
    nombre: str = Field(min_length=3, max_length=140)
    area: str = Field(default="Operaciones", max_length=60)
    elementos: list[str] = Field(min_length=1, max_length=40)
    propiedad_id: int | None = Field(default=None, ge=1)


class IncidenciaReservaUpdate(BaseModel):
    """Vincula o desvincula una incidencia de la reserva del huésped."""

    reserva_id: int | None = Field(default=None, ge=1)


# ============================================================
# HABITACIONES · CAPACIDAD DECLARADA
# ============================================================

class CapacidadHabitacionCreate(BaseModel):
    """Capacidad declarada por propiedad y tipo de habitación.

    La ocupación por tipo de habitación solo se calcula con esta
    configuración explícita; nunca se deduce del número total de habitaciones
    de la propiedad.
    """

    marca_id: int = Field(ge=1)
    tipo_habitacion: str = Field(min_length=1, max_length=100)
    habitaciones: int = Field(ge=0, le=100000)


# ============================================================
# MERCADO · REFERENCIAS PÚBLICAS
# ============================================================

def _validar_periodo_mercado(value: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"\d{4}(-\d{2})?", value):
        raise ValueError("El período debe tener formato YYYY-MM o YYYY.")
    if "-" in value:
        mes = int(value[5:7])
        if not 1 <= mes <= 12:
            raise ValueError("El mes del período debe estar entre 01 y 12.")
    return value


def _validar_url_mercado(value: str) -> str:
    value = str(value or "").strip()
    if value and not value.startswith(("http://", "https://")):
        raise ValueError("La URL de la fuente debe comenzar con http:// o https://.")
    return value


class MercadoReferenciaCreate(BaseModel):
    """Referencia pública del mercado registrada manualmente o importada.

    Cada valor exige fuente y período para que la comparación sea auditable.
    """

    propiedad_id: int | None = Field(default=None, ge=1)
    fuente: str = Field(min_length=2, max_length=180)
    url: str = Field(default="", max_length=500)
    tipo: Literal["mercado", "competencia", "referencia", "otro"] = "mercado"
    metrica: Literal["adr", "revpar", "ocupacion", "precio"] = "adr"
    periodo: str = Field(min_length=4, max_length=7)
    valor: float = Field(ge=0, le=100000000)
    moneda: str = Field(default="CLP", pattern=r"^[A-Z]{3}$")
    tipo_habitacion: str = Field(default="", max_length=100)
    notas: str = Field(default="", max_length=2000)

    @field_validator("periodo")
    @classmethod
    def validar_periodo(cls, value: str) -> str:
        return _validar_periodo_mercado(value)

    @field_validator("url")
    @classmethod
    def validar_url(cls, value: str) -> str:
        return _validar_url_mercado(value)


class MercadoReferenciaUpdate(BaseModel):
    propiedad_id: int | None = Field(default=None, ge=1)
    fuente: Optional[str] = Field(default=None, min_length=2, max_length=180)
    url: Optional[str] = Field(default=None, max_length=500)
    tipo: Optional[Literal["mercado", "competencia", "referencia", "otro"]] = None
    metrica: Optional[Literal["adr", "revpar", "ocupacion", "precio"]] = None
    periodo: Optional[str] = Field(default=None, min_length=4, max_length=7)
    valor: Optional[float] = Field(default=None, ge=0, le=100000000)
    moneda: Optional[str] = Field(default=None, pattern=r"^[A-Z]{3}$")
    tipo_habitacion: Optional[str] = Field(default=None, max_length=100)
    notas: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("periodo")
    @classmethod
    def validar_periodo(cls, value):
        return value if value is None else _validar_periodo_mercado(value)

    @field_validator("url")
    @classmethod
    def validar_url(cls, value):
        return value if value is None else _validar_url_mercado(value)


class MercadoReferenciaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    marca_id: Optional[int] = None
    fuente: str
    url: str = ""
    tipo: str = "mercado"
    metrica: str = "adr"
    periodo: str
    valor: float
    moneda: str = "CLP"
    tipo_habitacion: str = ""
    notas: str = ""
    creado_por: str = ""
    creado_en: Optional[datetime] = None
    actualizado_en: Optional[datetime] = None
