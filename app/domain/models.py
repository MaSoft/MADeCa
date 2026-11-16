"""Modelos ORM. Multi-tenant: toda tabla operativa lleva tenant_id y todas
las consultas de negocio deben filtrar por él (la sesión de cada petición
fija la empresa: ver app/security/dependencies.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid4() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SuperAdmin(Base):
    """Operador de la plataforma MADeCa (no de una empresa cliente).

    Deliberadamente NO tiene tenant_id ni ninguna relación con Tenant/
    Usuario: es un modelo de autenticación totalmente aparte para que un
    admin de una empresa cliente nunca pueda, ni por error de código,
    autenticarse como superadmin. Ver app/security/auth.py y
    app/security/dependencies.py (cookie y claim JWT distintos).
    """

    __tablename__ = "superadmins"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_alta: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    nombre: Mapped[str] = mapped_column(String(300), nullable=False)
    nif_cif: Mapped[str] = mapped_column(String(15), nullable=False, unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_alta: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Autorización expresa de la empresa para que el OCR de sus documentos
    # lo haga un tercero (Azure): es un subencargado del tratamiento
    # (art. 28.2 RGPD). Se guarda quién y cuándo lo autorizó.
    ocr_tercero_autorizado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ocr_tercero_autorizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ocr_tercero_autorizado_por: Mapped[str | None] = mapped_column(String(320), nullable=True)

    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="tenant")
    expediciones: Mapped[list["Expedicion"]] = relationship(back_populates="tenant")


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_usuario_tenant_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    rol: Mapped[str] = mapped_column(String(20), nullable=False, default="operador")
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_alta: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Los tokens de sesión emitidos antes de este momento se rechazan
    # (se fija al restablecer la contraseña). NULL = sin corte.
    sesiones_validas_desde: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="usuarios")


class ErpApiKey(Base):
    """API key de ingesta ERP. Solo se guarda el hash; la clave en claro se
    muestra una única vez al crearla."""

    __tablename__ = "erp_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    prefijo: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    erp_origen: Mapped[str] = mapped_column(String(30), nullable=False, default="generico")
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    fecha_ultimo_uso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Certificado(Base):
    """Certificado digital (.p12/.pfx) de la empresa para firma AdES.

    El fichero se guarda cifrado en disco (storage backend); aquí solo se
    referencia la ruta y metadatos. La contraseña del .p12 se cifra con la
    clave de aplicación (Fernet) — nunca en claro.
    """

    __tablename__ = "certificados"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    ruta_archivo: Mapped[str] = mapped_column(String(500), nullable=False)
    password_cifrado: Mapped[str] = mapped_column(Text, nullable=False)
    titular_cn: Mapped[str | None] = mapped_column(String(300), nullable=True)
    fecha_caducidad: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fecha_subida: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Expedicion(Base):
    __tablename__ = "expediciones"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "erp_origen", "referencia_externa", name="uq_expedicion_idempotencia"
        ),
        Index("ix_expedicion_tenant_fecha", "tenant_id", "fecha_transporte"),
    )

    # Este id es el identificador opaco usado en la URL pública del QR:
    # https://.../inspect/{id}. UUIDv4 aleatorio, no correlativo -> no enumerable.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)

    referencia_externa: Mapped[str] = mapped_column(String(200), nullable=False)
    erp_origen: Mapped[str] = mapped_column(String(30), nullable=False, default="generico")
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="en_transito")

    # Metadatos operativos validados (ExpedicionMetadata.model_dump(mode="json")).
    # El contenido del documento en sí vive solo en el PDF (storage_path);
    # aquí no se guarda nada del contenido de negocio del ERP.
    datos_deca: Mapped[dict] = mapped_column(JSONB, nullable=False)

    fecha_transporte: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_emision: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Se rellena cuando el estado pasa a ENTREGADO/ANULADO; dispara la
    # cuenta atrás de desactivación de URL (Apartado Tercero.5, 7 días).
    fecha_finalizacion_servicio: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    matricula_vehiculo: Mapped[str] = mapped_column(String(15), nullable=False)
    matricula_remolque: Mapped[str | None] = mapped_column(String(15), nullable=True)

    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    tamano_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    firmado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    finalidad_contractual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    certificado_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("certificados.id"), nullable=True
    )

    url_activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Enlace privado de descarga para la otra parte (cargador o
    # transportista que no emite el DeCA): Apartado Segundo.4 — "al otro le
    # basta con poder descargarlos durante un año". Independiente de la URL
    # pública del QR, que puede desactivarse a los 7 días (Tercero.5).
    token_contraparte: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="expediciones")
    accesos: Mapped[list["AccesoLog"]] = relationship(back_populates="expedicion")


class ModificacionDeca(Base):
    """Modificación de un DeCA durante el servicio (Apartado Quinto,
    método 1, BOE-A-2026-12784): se añaden al mismo PDF los datos nuevos y
    el motivo, y los anteriores quedan indicados como no válidos. La URL y
    el QR no cambian. Se conserva el PDF anterior (ruta_pdf_anterior) para
    trazabilidad."""

    __tablename__ = "modificaciones_deca"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    expedicion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("expediciones.id"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    # [{"campo": ..., "anterior": ..., "nuevo": ...}]
    cambios: Mapped[list] = mapped_column(JSONB, nullable=False)
    ruta_pdf_anterior: Mapped[str] = mapped_column(String(500), nullable=False)
    origen: Mapped[str] = mapped_column(String(200), nullable=False)


class EventoExpedicion(Base):
    """Historial de una expedición: quién la creó, la modificó, la dio por
    entregada… y cuándo. `quien` es el correo del usuario o el canal
    («API», «Carpeta compartida»). Las consultas del QR siguen en
    accesos_log."""

    __tablename__ = "eventos_expedicion"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    expedicion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("expediciones.id"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    accion: Mapped[str] = mapped_column(String(40), nullable=False)
    quien: Mapped[str] = mapped_column(String(320), nullable=False)
    detalle: Mapped[str | None] = mapped_column(Text, nullable=True)


class BorradorDeca(Base):
    """Datos obtenidos de un documento de origen (albarán, carta de
    porte...) o de un ERP, pendientes de revisión humana antes de emitir
    el DeCA. Nunca se emite nada sin que una persona lo confirme: el
    cargador y el transportista responden de los datos (Art. 7 Orden
    FOM/2861/2012)."""

    __tablename__ = "borradores_deca"
    __table_args__ = (
        UniqueConstraint("tenant_id", "origen", "referencia_fuente", name="uq_borrador_fuente"),
        Index("ix_borrador_tenant_estado", "tenant_id", "estado"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    # pendiente | emitido | descartado
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="pendiente")
    # Quién lo trajo: "panel", "api", "conector_local", "fuente:<id>"...
    origen: Mapped[str] = mapped_column(String(100), nullable=False)
    # Identificador del documento en su origen (ruta, id de SharePoint,
    # nº de albarán del ERP...): evita duplicados al sincronizar.
    referencia_fuente: Mapped[str] = mapped_column(String(500), nullable=False)
    nombre_documento: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Documento original, solo mientras esté pendiente (se borra al
    # emitir o descartar: minimización, art. 5.1.c RGPD).
    ruta_documento: Mapped[str | None] = mapped_column(String(500), nullable=True)
    datos: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confianza: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    avisos: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    motor_ocr: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Dónde está cada palabra del documento original, para señalar en la
    # revisión de dónde se leyó cada dato: {"origen": "pdf"|"ocr",
    # "paginas": [...]} (app/extraccion/posiciones.py). Se borra con el
    # documento al emitir o descartar.
    posiciones: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fecha_alta: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    fecha_resolucion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resuelto_por: Mapped[str | None] = mapped_column(String(320), nullable=True)
    expedicion_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("expediciones.id"), nullable=True)


class FuenteDatos(Base):
    """Fuente automática de documentos o de un ERP (app/conectores). La
    configuración (con credenciales) va cifrada y nunca se devuelve al
    navegador."""

    __tablename__ = "fuentes_datos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    config_cifrada: Mapped[str] = mapped_column(Text, nullable=False)
    estado_sync: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    intervalo_minutos: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    ultima_ejecucion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ultimo_resultado: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ultimo_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fecha_alta: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AccesoLog(Base):
    """Registro append-only de cada escaneo/consulta del visor público."""

    __tablename__ = "accesos_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid4)
    expedicion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expediciones.id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resultado: Mapped[str] = mapped_column(String(30), nullable=False)

    expedicion: Mapped["Expedicion"] = relationship(back_populates="accesos")
