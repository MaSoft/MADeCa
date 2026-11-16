"""Bandeja de revisión: borradores de DeCA obtenidos de documentos de
origen o de un ERP, que una persona revisa antes de emitir.

Reglas (ver app/extraccion/__init__.py para la base legal):
- Nada se emite sin confirmación humana.
- El OCR lo hace un tercero (Azure) solo si la empresa lo ha autorizado.
- El documento original se guarda solo mientras el borrador está
  pendiente y se borra al emitirlo o descartarlo (art. 5.1.c RGPD).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.models import BorradorDeca, Expedicion, Tenant
from app.domain.schemas import DecaCreate
from app.extraccion import ExtraccionError, extraer_campos, obtener_texto
from app.extraccion.motores import MotorAzure, MotorNoDisponibleError, MotorOCR, MotorTesseract
from app.services.expedicion_service import crear_expedicion_desde_datos
from app.services.storage import get_storage

logger = logging.getLogger("borradores")


class BorradorNoPendienteError(Exception):
    pass


def motor_ocr_para(tenant: Tenant) -> MotorOCR | None:
    """Azure solo si la empresa lo autorizó y el operador lo configuró;
    si no, Tesseract en el propio servidor; si tampoco hay, ninguno (los
    PDF con texto se siguen leyendo, los escaneos darán aviso)."""
    settings = get_settings()
    if tenant.ocr_tercero_autorizado and settings.azure_di_endpoint and settings.azure_di_clave:
        try:
            return MotorAzure(settings.azure_di_endpoint, settings.azure_di_clave)
        except MotorNoDisponibleError as exc:
            logger.warning("Azure configurado pero no utilizable: %s", exc)
    try:
        return MotorTesseract(settings.tesseract_ruta, limite_segundos=settings.ocr_limite_segundos,
                              simultaneos=settings.ocr_simultaneos)
    except MotorNoDisponibleError:
        return None


def crear_borrador(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    origen: str,
    referencia_fuente: str,
    nombre_documento: str | None,
    datos: dict,
    confianza: dict | None = None,
    avisos: list | None = None,
    motor_ocr: str | None = None,
    documento: bytes | None = None,
    posiciones: dict | None = None,
) -> tuple[BorradorDeca, bool]:
    """Idempotente por (empresa, origen, referencia_fuente): si ya existe
    se devuelve el existente y False."""
    existente = db.execute(
        select(BorradorDeca).where(
            BorradorDeca.tenant_id == tenant_id,
            BorradorDeca.origen == origen,
            BorradorDeca.referencia_fuente == referencia_fuente,
        )
    ).scalar_one_or_none()
    if existente is not None:
        return existente, False

    borrador = BorradorDeca(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        estado="pendiente",
        origen=origen[:100],
        referencia_fuente=referencia_fuente[:500],
        nombre_documento=(nombre_documento or "")[:300] or None,
        datos=datos or {},
        confianza=confianza or {},
        avisos=avisos or [],
        motor_ocr=motor_ocr,
        posiciones=posiciones if documento else None,
        fecha_alta=datetime.now(timezone.utc),
    )
    ruta = None
    if documento:
        extension = PurePath(nombre_documento or "").suffix.lower()[:10] or ".bin"
        ruta = get_storage().guardar_archivo(tenant_id, f"borradores/{borrador.id}{extension}", documento)
        borrador.ruta_documento = ruta
    db.add(borrador)
    try:
        db.commit()
    except IntegrityError:
        # Otro proceso lo creó a la vez (misma referencia).
        db.rollback()
        if ruta:
            get_storage().eliminar(ruta)
        return crear_borrador(
            db, tenant_id=tenant_id, origen=origen, referencia_fuente=referencia_fuente,
            nombre_documento=nombre_documento, datos=datos,
        )
    db.refresh(borrador)
    return borrador, True


def procesar_documento(
    db: Session, *, tenant: Tenant, origen: str, referencia_fuente: str, nombre: str, contenido: bytes
) -> tuple[BorradorDeca, bool]:
    """Extrae los datos de un documento de origen y crea su borrador. Si
    no se puede leer, el borrador se crea igualmente vacío y con el motivo
    en avisos, para que nadie pierda el documento de vista."""
    settings = get_settings()
    if len(contenido) > settings.documento_origen_max_bytes:
        datos, confianza, avisos, motor = {}, {}, [f"Documento demasiado grande ({len(contenido)} bytes)"], None
        contenido_guardado, posiciones = None, None
    else:
        motor_ocr = motor_ocr_para(tenant)
        try:
            texto = obtener_texto(contenido, nombre, motor_ocr)
            resultado = extraer_campos(texto.texto)
            datos, confianza, avisos, motor = resultado.datos, resultado.confianza, resultado.avisos, texto.motor
            posiciones = {"origen": texto.origen_paginas, "paginas": texto.paginas} if texto.paginas else None
        except ExtraccionError as exc:
            datos, confianza, avisos, motor = {}, {}, [f"No se pudo leer el documento: {exc}"], None
            posiciones = None
        contenido_guardado = contenido
    return crear_borrador(
        db,
        tenant_id=tenant.id,
        origen=origen,
        referencia_fuente=referencia_fuente,
        nombre_documento=nombre,
        datos=datos,
        confianza=confianza,
        avisos=avisos,
        motor_ocr=motor,
        documento=contenido_guardado,
        posiciones=posiciones,
    )


def _borrar_documento(borrador: BorradorDeca) -> None:
    if borrador.ruta_documento:
        try:
            get_storage().eliminar(borrador.ruta_documento)
        except Exception:
            logger.exception("No se pudo borrar el documento de origen %s", borrador.ruta_documento)
        borrador.ruta_documento = None
    borrador.posiciones = None


def emitir_borrador(db: Session, *, borrador: BorradorDeca, deca: DecaCreate, usuario: str) -> Expedicion:
    """La persona ha revisado y confirmado los datos: se emite el DeCA por
    el camino de generación (MADeCa redacta un PDF nativo digital)."""
    if borrador.estado != "pendiente":
        raise BorradorNoPendienteError("Este borrador ya está resuelto")
    exp = crear_expedicion_desde_datos(
        db, tenant_id=borrador.tenant_id, deca=deca, quien=usuario,
        detalle=f"Bandeja de revisión: {borrador.nombre_documento or borrador.referencia_fuente}"[:1000],
    )
    borrador.estado = "emitido"
    borrador.expedicion_id = exp.id
    borrador.fecha_resolucion = datetime.now(timezone.utc)
    borrador.resuelto_por = usuario[:320]
    borrador.datos = deca.model_dump(mode="json")
    _borrar_documento(borrador)
    db.add(borrador)
    db.commit()
    return exp


def descartar_borrador(db: Session, *, borrador: BorradorDeca, usuario: str) -> None:
    if borrador.estado != "pendiente":
        raise BorradorNoPendienteError("Este borrador ya está resuelto")
    borrador.estado = "descartado"
    borrador.fecha_resolucion = datetime.now(timezone.utc)
    borrador.resuelto_por = usuario[:320]
    _borrar_documento(borrador)
    db.add(borrador)
    db.commit()
