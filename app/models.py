# -*- coding: utf-8 -*-
"""============================================================
   NORUS & LUUM · Modelos SQLAlchemy (esquema estrella)
   Tablas de hechos y dimensiones para el data warehouse.
   ============================================================"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Column, Integer, String, Float, DateTime, Text, ForeignKey, Index,
    UniqueConstraint, func,
)
from sqlalchemy.orm import relationship

from .crypto import EncryptedText
from .database import Base


# ============================================================
# DIMENSIONES
# ============================================================

class DimFecha(Base):
    """Dimensión de tiempo: una fila por día."""
    __tablename__ = "dim_fecha"

    id = Column(Integer, primary_key=True)
    fecha = Column(String(10), unique=True, nullable=False, index=True)  # YYYY-MM-DD
    anio = Column(Integer, nullable=False)
    mes = Column(Integer, nullable=False)
    mes_nombre = Column(String(10), nullable=False)
    trimestre = Column(Integer, nullable=False)
    dia_semana = Column(Integer, nullable=False)
    nombre_dia = Column(String(10), nullable=False)
    es_fin_semana = Column(Integer, nullable=False, default=0)


class DimCanal(Base):
    """Dimensión de canales de venta."""
    __tablename__ = "dim_canal"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(100), unique=True, nullable=False, index=True)
    tipo = Column(String(50), default="")  # directo, ota, gds, etc.


class DimNacionalidad(Base):
    """Dimensión de nacionalidades / mercados emisores."""
    __tablename__ = "dim_nacionalidad"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(100), unique=True, nullable=False, index=True)
    region = Column(String(100), default="")


class DimMarca(Base):
    """Dimension de marcas (propiedades hoteleras)."""
    __tablename__ = "dim_marca"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(100), unique=True, nullable=False, index=True)
    habitaciones = Column(Integer, default=0)


class DimSegmento(Base):
    """Dimensión de segmentos de mercado."""
    __tablename__ = "dim_segmento"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(100), unique=True, nullable=False, index=True)


class DimTipoHabitacion(Base):
    """Dimensión de tipos de habitación."""
    __tablename__ = "dim_tipo_habitacion"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(100), unique=True, nullable=False, index=True)


# ============================================================
# HECHOS
# ============================================================

class HechoReserva(Base):
    """Tabla de hechos: reservas individuales."""
    __tablename__ = "hecho_reserva"
    __table_args__ = (
        Index("idx_reserva_fecha", "fecha_id"),
        Index("idx_reserva_canal", "canal_id"),
        Index("idx_reserva_marca", "marca_id"),
        Index("idx_reserva_carga", "carga_id"),
        Index("idx_reserva_estado", "estado"),
        Index("idx_reserva_habitacion", "marca_id", "habitacion"),
    )

    id = Column(Integer, primary_key=True)
    codigo = Column(String(50), nullable=False, index=True)
    moneda = Column(String(3), nullable=False, default="CLP")
    # PII: transparently encrypted at rest by EncryptedText (Fernet).
    huesped = Column(EncryptedText, nullable=True)
    # Número de habitación (dato operativo, no PII). Permite vincular una
    # incidencia con la estancia activa del huésped.
    habitacion = Column(String(30), nullable=False, default="", server_default="")
    estado = Column(String(50), default="Confirmada")
    valor = Column(Float, default=0.0)
    noches = Column(Float, default=0.0)
    anticipacion_dias = Column(Float, default=0.0)

    # FKs a dimensiones
    fecha_id = Column(Integer, ForeignKey("dim_fecha.id"), nullable=True)
    canal_id = Column(Integer, ForeignKey("dim_canal.id"), nullable=True)
    nacionalidad_id = Column(Integer, ForeignKey("dim_nacionalidad.id"), nullable=True)
    marca_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True)
    segmento_id = Column(Integer, ForeignKey("dim_segmento.id"), nullable=True)
    tipo_habitacion_id = Column(Integer, ForeignKey("dim_tipo_habitacion.id"), nullable=True)

    # Relaciones
    fecha = relationship("DimFecha")
    canal = relationship("DimCanal")
    nacionalidad = relationship("DimNacionalidad")
    marca = relationship("DimMarca")
    segmento = relationship("DimSegmento")
    tipo_habitacion = relationship("DimTipoHabitacion")

    # Metadatos de carga
    carga_id = Column(Integer, ForeignKey("carga_archivo.id"), nullable=True)
    creado_en = Column(DateTime, default=func.now())


class HechoMetricaMensual(Base):
    """Tabla de hechos: métricas agregadas por mes."""
    __tablename__ = "hecho_metrica_mensual"
    __table_args__ = (
        Index("idx_metrica_mes_marca", "anio", "mes", "marca_id"),
        Index("idx_metrica_anio_mes", "anio", "mes"),
        UniqueConstraint("anio", "mes", "marca_id", "moneda", name="uq_metrica_mes_propiedad_moneda"),
    )

    id = Column(Integer, primary_key=True)
    anio = Column(Integer, nullable=False)
    mes = Column(Integer, nullable=False)
    marca_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True)
    moneda = Column(String(3), nullable=False, default="CLP")

    ocupacion = Column(Float, nullable=True)
    adr = Column(Float, nullable=True)
    revpar = Column(Float, nullable=True)
    ingresos = Column(Float, nullable=True)
    noches_vendidas = Column(Float, nullable=True)
    habitaciones_disponibles = Column(Float, nullable=True)

    carga_id = Column(Integer, ForeignKey("carga_archivo.id"), nullable=True)


class HechoMetricaDiaria(Base):
    """Daily aggregation used for day/week granularity dashboards."""
    __tablename__ = "hecho_metrica_diaria"
    __table_args__ = (
        Index("idx_metrica_dia_marca", "fecha", "marca_id"),
        UniqueConstraint("fecha", "marca_id", "moneda", name="uq_metrica_dia_propiedad_moneda"),
    )

    id = Column(Integer, primary_key=True)
    fecha = Column(String(10), nullable=False, index=True)
    anio = Column(Integer, nullable=False)
    mes = Column(Integer, nullable=False)
    marca_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True)
    moneda = Column(String(3), nullable=False, default="CLP")

    ocupacion = Column(Float, nullable=True)
    adr = Column(Float, nullable=True)
    revpar = Column(Float, nullable=True)
    ingresos = Column(Float, nullable=True)
    noches_vendidas = Column(Float, nullable=True)
    habitaciones_disponibles = Column(Float, nullable=True)

    carga_id = Column(Integer, ForeignKey("carga_archivo.id"), nullable=True)


class HechoIncidencia(Base):
    """Tabla de hechos: incidencias y satisfacción."""
    __tablename__ = "hecho_incidencia"
    __table_args__ = (
        Index("idx_incidencia_fecha", "fecha_id"),
        Index("idx_incidencia_tipo", "tipo"),
        Index("idx_incidencia_area", "area"),
        Index("idx_incidencia_marca", "marca_id"),
        UniqueConstraint("origen_hash", name="uq_incidencia_origen_hash"),
    )

    id = Column(Integer, primary_key=True)
    origen_hash = Column(String(64), nullable=True)
    tipo = Column(String(100), default="General")
    area = Column(String(100), default="General")
    cantidad = Column(Float, default=1.0)
    tiempo_resolucion_h = Column(Float, nullable=True)
    satisfaccion = Column(Float, nullable=True)
    nps = Column(Float, nullable=True)

    fecha_id = Column(Integer, ForeignKey("dim_fecha.id"), nullable=True)
    marca_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True)
    carga_id = Column(Integer, ForeignKey("carga_archivo.id"), nullable=True)

    fecha = relationship("DimFecha")


# ============================================================
# AUDITORÍA Y METADATOS DE CARGA
# ============================================================

class CargaArchivo(Base):
    """Registro de cada archivo importado (auditoría de ETL)."""
    __tablename__ = "carga_archivo"

    id = Column(Integer, primary_key=True)
    nombre_archivo = Column(String(255), nullable=False)
    formato = Column(String(20), nullable=False)
    tamano_bytes = Column(Integer, default=0)
    filas_procesadas = Column(Integer, default=0)
    filas_exitosas = Column(Integer, default=0)
    filas_fallidas = Column(Integer, default=0)
    estado = Column(String(20), default="completada")  # completada, parcial, fallida
    mensaje_error = Column(Text, default="")
    usuario = Column(String(100), default="")
    creado_en = Column(DateTime, default=func.now())


class EventoAuditoria(Base):
    """Registro inmutable de eventos de acceso y actividad."""
    __tablename__ = "evento_auditoria"

    id = Column(Integer, primary_key=True)
    accion = Column(String(50), nullable=False, index=True)
    usuario = Column(String(100), default="")
    detalle = Column(Text, default="")
    ip = Column(String(50), default="")
    creado_en = Column(DateTime, default=func.now())


# ============================================================
# USUARIOS
# ============================================================

class UsuarioPropiedad(Base):
    """Asignación explícita de una propiedad (marca) a un usuario no administrador."""
    __tablename__ = "usuario_propiedad"
    __table_args__ = (UniqueConstraint("usuario_id", "marca_id", name="uq_usuario_propiedad"),)

    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False, index=True)
    marca_id = Column(Integer, ForeignKey("dim_marca.id", ondelete="CASCADE"), nullable=False, index=True)


class Usuario(Base):
    """Usuarios del sistema con roles y alcance por propiedad."""
    __tablename__ = "usuario"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=True, index=True)
    rol = Column(String(20), default="lector")  # admin, editor, lector
    activo = Column(Integer, default=1)
    creado_en = Column(DateTime, default=func.now())
    ultimo_acceso = Column(DateTime, nullable=True)
    # Invalida tokens JWT emitidos antes de un cierre de sesión o cambio sensible.
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    # Permite a un rol no administrador ver todas las propiedades.
    acceso_todas_propiedades = Column(Integer, nullable=False, default=0, server_default="0")

    # MFA (TOTP). Los secretos y códigos de respaldo se cifran en reposo.
    mfa_habilitado = Column(Integer, nullable=False, default=0, server_default="0")
    mfa_secret = Column(EncryptedText, nullable=True)
    mfa_pending_secret = Column(EncryptedText, nullable=True)
    mfa_last_counter = Column(BigInteger, nullable=False, default=0, server_default="0")
    backup_codes = Column(EncryptedText, nullable=True)

    # SSO/OIDC.
    sso_subject = Column(String(255), unique=True, nullable=True, index=True)
    sso_provider = Column(String(120), nullable=True)

    propiedades = relationship(
        "DimMarca", secondary="usuario_propiedad", lazy="selectin", order_by="DimMarca.nombre"
    )


# ============================================================
# OPERACIÓN HOTELERA (procedimientos diarios)
# ============================================================

class TareaOperativa(Base):
    __tablename__ = "ops_tarea"
    __table_args__ = (Index("ix_ops_tarea_estado_fecha", "estado", "vence_en"),)

    id = Column(Integer, primary_key=True)
    titulo = Column(String(180), nullable=False)
    descripcion = Column(Text, nullable=False, default="")
    area = Column(String(60), nullable=False, default="Recepción")
    prioridad = Column(String(20), nullable=False, default="media")
    estado = Column(String(20), nullable=False, default="pendiente")
    asignado_a = Column(String(100), nullable=False, default="")
    propiedad_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True, index=True)
    vence_en = Column(DateTime, nullable=True)
    creado_por = Column(String(100), nullable=False)
    creado_en = Column(DateTime, nullable=False, default=func.now())
    actualizado_en = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class IncidenciaOperativa(Base):
    __tablename__ = "ops_incidencia"
    __table_args__ = (
        Index("ix_ops_incidente_estado_fecha", "estado", "creado_en"),
        Index("ix_ops_incidente_reserva", "reserva_id"),
    )

    id = Column(Integer, primary_key=True)
    titulo = Column(String(180), nullable=False)
    detalle = Column(Text, nullable=False, default="")
    categoria = Column(String(60), nullable=False, default="Servicio")
    severidad = Column(String(20), nullable=False, default="media")
    estado = Column(String(20), nullable=False, default="abierta")
    area = Column(String(60), nullable=False, default="Recepción")
    habitacion = Column(String(30), nullable=False, default="")
    # Vínculo explícito opcional con la reserva del huésped. Cuando está vacío,
    # el contexto se resuelve por habitación y fecha de la estancia.
    reserva_id = Column(Integer, ForeignKey("hecho_reserva.id", ondelete="SET NULL"), nullable=True)
    responsable = Column(String(100), nullable=False, default="")
    propiedad_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True, index=True)
    creado_por = Column(String(100), nullable=False)
    creado_en = Column(DateTime, nullable=False, default=func.now())
    resuelto_en = Column(DateTime, nullable=True)


class OrdenMantenimiento(Base):
    __tablename__ = "ops_mantenimiento"
    __table_args__ = (Index("ix_ops_mant_estado_fecha", "estado", "creado_en"),)

    id = Column(Integer, primary_key=True)
    titulo = Column(String(180), nullable=False)
    detalle = Column(Text, nullable=False, default="")
    tipo = Column(String(40), nullable=False, default="Correctivo")
    prioridad = Column(String(20), nullable=False, default="media")
    estado = Column(String(20), nullable=False, default="pendiente")
    ubicacion = Column(String(100), nullable=False, default="")
    responsable = Column(String(100), nullable=False, default="")
    propiedad_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True, index=True)
    creado_por = Column(String(100), nullable=False)
    creado_en = Column(DateTime, nullable=False, default=func.now())
    cerrado_en = Column(DateTime, nullable=True)


class ItemInventario(Base):
    __tablename__ = "ops_inventario"
    __table_args__ = (Index("ix_ops_inv_propiedad_nombre", "propiedad_id", "nombre"),)

    id = Column(Integer, primary_key=True)
    nombre = Column(String(140), nullable=False)
    categoria = Column(String(60), nullable=False, default="General")
    unidad = Column(String(30), nullable=False, default="unidad")
    cantidad = Column(Float, nullable=False, default=0)
    stock_minimo = Column(Float, nullable=False, default=0)
    proveedor = Column(String(120), nullable=False, default="")
    propiedad_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True, index=True)
    actualizado_por = Column(String(100), nullable=False)
    actualizado_en = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class ObjetoPerdido(Base):
    __tablename__ = "ops_objeto_perdido"

    id = Column(Integer, primary_key=True)
    descripcion = Column(EncryptedText, nullable=False)
    categoria = Column(String(60), nullable=False, default="General")
    ubicacion = Column(String(100), nullable=False, default="")
    fecha_encontrado = Column(DateTime, nullable=False, default=func.now())
    estado = Column(String(20), nullable=False, default="resguardado")
    responsable = Column(String(100), nullable=False, default="")
    propiedad_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True, index=True)
    registrado_por = Column(String(100), nullable=False)
    creado_en = Column(DateTime, nullable=False, default=func.now())


class EjecucionChecklist(Base):
    __tablename__ = "ops_checklist"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(140), nullable=False)
    area = Column(String(60), nullable=False, default="Operaciones")
    elementos = Column(Text, nullable=False, default="[]")
    estado = Column(String(20), nullable=False, default="en_progreso")
    propiedad_id = Column(Integer, ForeignKey("dim_marca.id"), nullable=True, index=True)
    ejecutado_por = Column(String(100), nullable=False)
    creado_en = Column(DateTime, nullable=False, default=func.now())
    completado_en = Column(DateTime, nullable=True)


class CapacidadHabitacion(Base):
    """Capacidad declarada por propiedad y tipo de habitación.

    La ocupación por tipo de habitación solo se calcula cuando existe esta
    configuración; de lo contrario se devuelve ``null`` en vez de inventar
    habitaciones disponibles.
    """
    __tablename__ = "ops_capacidad_habitacion"
    __table_args__ = (
        UniqueConstraint("marca_id", "tipo_habitacion_id", name="uq_capacidad_marca_tipo"),
        Index("ix_capacidad_marca_tipo", "marca_id", "tipo_habitacion_id"),
    )

    id = Column(Integer, primary_key=True)
    marca_id = Column(Integer, ForeignKey("dim_marca.id", ondelete="CASCADE"), nullable=False)
    tipo_habitacion_id = Column(Integer, ForeignKey("dim_tipo_habitacion.id", ondelete="CASCADE"), nullable=False)
    habitaciones = Column(Integer, nullable=False, default=0)
    actualizado_por = Column(String(100), nullable=False, default="")
    actualizado_en = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class ReferenciaMercado(Base):
    """Precio o indicador público del mercado, con fuente y período.

    Se usa para comparar la tarifa del hotel con referencias públicas de
    largo plazo. La plataforma no inventa estos valores: se registran
    manualmente, se importan desde un archivo autorizado o se sincronizan
    desde una API pública sin clave con fuente, URL y atribución.
    """
    __tablename__ = "mercado_referencia"
    __table_args__ = (
        UniqueConstraint(
            "marca_id", "fuente", "metrica", "periodo", "moneda", "tipo_habitacion",
            name="uq_mercado_referencia",
        ),
        Index("ix_mercado_metrica_periodo", "metrica", "periodo"),
        Index("ix_mercado_marca_periodo", "marca_id", "periodo"),
    )

    id = Column(Integer, primary_key=True)
    marca_id = Column(Integer, ForeignKey("dim_marca.id", ondelete="CASCADE"), nullable=True)
    fuente = Column(String(180), nullable=False)
    url = Column(String(500), nullable=False, default="")
    tipo = Column(String(40), nullable=False, default="mercado")
    metrica = Column(String(20), nullable=False, default="adr")
    periodo = Column(String(7), nullable=False)  # YYYY-MM o YYYY
    valor = Column(Float, nullable=False, default=0.0)
    moneda = Column(String(3), nullable=False, default="CLP")
    tipo_habitacion = Column(String(100), nullable=False, default="")
    notas = Column(Text, nullable=False, default="")
    creado_por = Column(String(100), nullable=False, default="")
    creado_en = Column(DateTime, nullable=False, default=func.now())
    actualizado_en = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    marca = relationship("DimMarca", lazy="selectin")


class IntentoLogin(Base):
    __tablename__ = "auth_login_attempt"
    __table_args__ = (Index("ix_auth_login_fingerprint_time", "usuario_hash", "creado_en"),)

    id = Column(Integer, primary_key=True)
    usuario_hash = Column(String(64), nullable=False)
    ip_hash = Column(String(64), nullable=False)
    creado_en = Column(DateTime, nullable=False, default=func.now())
