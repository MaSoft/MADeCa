"""Orquestación de la creación de una expedición DeCA. Dos caminos,
misma lógica compartida de idempotencia/firma/almacenamiento:

- `crear_expedicion_desde_pdf`: el ERP ya aporta el PDF (albarán/
  factura/declaración de contenido) — se le estampa el QR sin tocar el
  resto del contenido.
- `crear_expedicion_desde_datos`: el ERP no genera PDF propio, solo
  manda datos estructurados — MADeCa redacta el documento.

Punto de entrada único usado tanto por el endpoint REST de ingesta como
por el watcher de hot-folder, para que ambos caminos apliquen
exactamente las mismas reglas.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.models import Certificado, EventoExpedicion, Expedicion, ModificacionDeca
from app.domain.schemas import CAMPOS_INDEXADOS, DecaCreate, ExpedicionMetadata, ModificacionCreate
from app.domain.validation import validar_matricula
from app.zonas import con_zona
from app.services.pdf_generator import (
    anadir_pagina_modificacion,
    estampar_qr_en_pdf,
    generar_pdf_desde_datos,
)
from app.services.signing import firmar_pdf
from app.services.storage import get_storage


class ExpedicionYaExisteError(Exception):
    """Idempotencia: ya existe una expedición con esa referencia_externa
    + erp_origen para este tenant (uq_expedicion_idempotencia)."""


class CertificadoNoDisponibleError(Exception):
    pass


class ModificacionNoPermitidaError(Exception):
    pass


def _comprobar_idempotencia(db: Session, *, tenant_id: uuid.UUID, erp_origen: str, referencia_externa: str) -> None:
    existente = db.execute(
        select(Expedicion).where(
            Expedicion.tenant_id == tenant_id,
            Expedicion.erp_origen == erp_origen,
            Expedicion.referencia_externa == referencia_externa,
        )
    ).scalar_one_or_none()
    if existente is not None:
        raise ExpedicionYaExisteError(
            f"Ya existe una expedición para referencia_externa="
            f"'{referencia_externa}' (erp={erp_origen})"
        )


def _firmar_si_procede(
    db: Session, *, tenant_id: uuid.UUID, finalidad_contractual: bool, pdf_bytes: bytes
) -> tuple[bytes, bool, Certificado | None]:
    if not finalidad_contractual:
        return pdf_bytes, False, None

    # Antes se tomaba el primer certificado activo sin mirar su caducidad:
    # una firma hecha con un certificado caducado no es una firma
    # electrónica avanzada válida (Apartado Cuarto + Reglamento eIDAS).
    # Se usa el vigente que caduque más tarde.
    ahora = datetime.now(timezone.utc)
    certificado = db.execute(
        select(Certificado)
        .where(
            Certificado.tenant_id == tenant_id,
            Certificado.activo.is_(True),
            (Certificado.fecha_caducidad.is_(None)) | (Certificado.fecha_caducidad > ahora),
        )
        .order_by(Certificado.fecha_caducidad.desc().nulls_last())
    ).scalars().first()
    if certificado is None:
        raise CertificadoNoDisponibleError(
            "La expedición tiene finalidad_contractual=True pero el tenant "
            "no tiene ningún certificado de firma activo y vigente "
            "(Apartado Cuarto BOE-A-2026-12784)"
        )
    pdf_firmado = firmar_pdf(
        pdf_bytes=pdf_bytes,
        ruta_certificado_p12=certificado.ruta_archivo,
        password_certificado_cifrada=certificado.password_cifrado,
    )
    return pdf_firmado, True, certificado


QUIEN_SISTEMA = "Sistema"


def registrar_evento(db: Session, expedicion: Expedicion, accion: str, quien: str | None, detalle: str | None = None) -> None:
    """Añade un paso a la actividad de la expedición. No confirma: va en la
    misma transacción que el cambio, para que no haya uno sin el otro."""
    db.add(EventoExpedicion(expedicion_id=expedicion.id, tenant_id=expedicion.tenant_id, accion=accion,
                            quien=(quien or QUIEN_SISTEMA)[:320], detalle=detalle))


def _persistir(
    db: Session,
    *,
    expedicion_id: uuid.UUID,
    tenant_id: uuid.UUID,
    referencia_externa: str,
    erp_origen: str,
    matricula_vehiculo: str,
    matricula_remolque: str | None,
    fecha_transporte: datetime,
    finalidad_contractual: bool,
    datos_auditoria: dict,
    pdf_bytes: bytes,
    firmado: bool,
    certificado: Certificado | None,
    quien: str | None = None,
    detalle: str | None = None,
) -> Expedicion:
    storage = get_storage()
    ruta = storage.guardar(tenant_id, expedicion_id, pdf_bytes)

    expedicion = Expedicion(
        id=expedicion_id,
        tenant_id=tenant_id,
        referencia_externa=referencia_externa,
        erp_origen=erp_origen,
        estado="en_transito",
        datos_deca=datos_auditoria,
        fecha_transporte=fecha_transporte,
        fecha_emision=datetime.now(timezone.utc),
        matricula_vehiculo=matricula_vehiculo,
        matricula_remolque=matricula_remolque,
        storage_path=ruta,
        tamano_bytes=len(pdf_bytes),
        firmado=firmado,
        finalidad_contractual=finalidad_contractual,
        certificado_id=certificado.id if certificado else None,
        url_activa=True,
        token_contraparte=secrets.token_hex(32),
    )
    db.add(expedicion)
    db.flush()
    registrar_evento(db, expedicion, "emitida", quien, detalle)
    db.commit()
    db.refresh(expedicion)
    return expedicion


def crear_expedicion_desde_pdf(
    db: Session, *, tenant_id: uuid.UUID, metadata: ExpedicionMetadata, pdf_original_bytes: bytes,
    quien: str | None = None, detalle: str | None = None,
) -> Expedicion:
    """El PDF lo aporta el ERP tal cual (albarán/factura/declaración de
    contenido); solo se le estampa el QR. `estampar_qr_en_pdf` valida
    internamente que el PDF sea legible y no parezca un escaneo
    (PdfInvalidoError / PdfPosibleEscaneoError se propagan al llamador).
    """
    _comprobar_idempotencia(
        db, tenant_id=tenant_id, erp_origen=metadata.erp_origen.value, referencia_externa=metadata.referencia_externa
    )

    settings = get_settings()
    expedicion_id = uuid.uuid4()

    pdf_bytes, _url = estampar_qr_en_pdf(
        pdf_original_bytes=pdf_original_bytes, expedicion_id=expedicion_id, base_url=settings.app_base_url
    )
    pdf_bytes, firmado, certificado = _firmar_si_procede(
        db, tenant_id=tenant_id, finalidad_contractual=metadata.finalidad_contractual, pdf_bytes=pdf_bytes
    )

    return _persistir(
        db,
        expedicion_id=expedicion_id,
        tenant_id=tenant_id,
        referencia_externa=metadata.referencia_externa,
        erp_origen=metadata.erp_origen.value,
        matricula_vehiculo=metadata.matricula_vehiculo,
        matricula_remolque=metadata.matricula_remolque,
        fecha_transporte=metadata.fecha_transporte,
        finalidad_contractual=metadata.finalidad_contractual,
        datos_auditoria=metadata.model_dump(mode="json"),
        pdf_bytes=pdf_bytes,
        firmado=firmado,
        certificado=certificado,
        quien=quien,
        detalle=detalle,
    )


def crear_expedicion_desde_datos(
    db: Session, *, tenant_id: uuid.UUID, deca: DecaCreate, quien: str | None = None, detalle: str | None = None
) -> Expedicion:
    """El ERP no genera PDF propio: MADeCa redacta el documento a partir
    de los datos estructurados y valida los campos mínimos del Art. 6."""
    _comprobar_idempotencia(
        db, tenant_id=tenant_id, erp_origen=deca.erp_origen.value, referencia_externa=deca.referencia_externa
    )

    settings = get_settings()
    expedicion_id = uuid.uuid4()

    pdf_bytes, _url = generar_pdf_desde_datos(deca=deca, expedicion_id=expedicion_id, base_url=settings.app_base_url)
    pdf_bytes, firmado, certificado = _firmar_si_procede(
        db, tenant_id=tenant_id, finalidad_contractual=deca.finalidad_contractual, pdf_bytes=pdf_bytes
    )

    return _persistir(
        db,
        expedicion_id=expedicion_id,
        tenant_id=tenant_id,
        referencia_externa=deca.referencia_externa,
        erp_origen=deca.erp_origen.value,
        matricula_vehiculo=deca.matricula_vehiculo,
        matricula_remolque=deca.matricula_remolque,
        fecha_transporte=deca.fecha_transporte,
        finalidad_contractual=deca.finalidad_contractual,
        datos_auditoria=deca.model_dump(mode="json"),
        pdf_bytes=pdf_bytes,
        firmado=firmado,
        certificado=certificado,
        quien=quien,
        detalle=detalle,
    )


def finalizar_servicio(
    db: Session, *, expedicion: Expedicion, estado_final: str = "entregado", quien: str | None = None
) -> Expedicion:
    """Marca la expedición como finalizada. A partir de este momento
    empieza a correr el plazo de 7 días tras el cual la URL puede
    desactivarse (Apartado Tercero.5).

    Solo desde "en_transito" y hacia "entregado" o "anulado": antes se
    aceptaba volver a finalizar (reiniciaba el plazo) o "finalizar" como
    en_transito (arrancaba el plazo con el servicio en curso)."""
    if estado_final not in ("entregado", "anulado"):
        raise ModificacionNoPermitidaError("El estado final debe ser entregado o anulado")
    if expedicion.estado != "en_transito":
        raise ModificacionNoPermitidaError(f"La expedición ya está finalizada ({expedicion.estado})")
    expedicion.estado = estado_final
    expedicion.fecha_finalizacion_servicio = datetime.now(timezone.utc)
    db.add(expedicion)
    registrar_evento(db, expedicion, estado_final, quien)
    db.commit()
    db.refresh(expedicion)
    return expedicion


def _aplicar_campo_indexado(expedicion: Expedicion, campo: str, nuevo: str) -> None:
    """Mantiene coherentes los datos que MADeCa guarda fuera del PDF."""
    datos = dict(expedicion.datos_deca or {})
    if campo == "matricula_vehiculo":
        pais = datos.get("pais_matricula_vehiculo", "ES")
        expedicion.matricula_vehiculo = validar_matricula(nuevo, pais)
        datos[campo] = expedicion.matricula_vehiculo
    elif campo == "matricula_remolque":
        pais = datos.get("pais_matricula_remolque", "ES")
        expedicion.matricula_remolque = validar_matricula(nuevo, pais)
        datos[campo] = expedicion.matricula_remolque
    elif campo == "fecha_transporte":
        try:
            fecha = datetime.fromisoformat(nuevo)
        except ValueError as exc:
            raise ModificacionNoPermitidaError(f"Fecha no válida: '{nuevo}' (formato AAAA-MM-DDTHH:MM)") from exc
        try:
            fecha, zona = con_zona(fecha, datos.get("zona_horaria"))
        except ValueError as exc:
            raise ModificacionNoPermitidaError(str(exc)) from exc
        expedicion.fecha_transporte = fecha
        datos[campo] = fecha.isoformat()
        datos["zona_horaria"] = zona
    expedicion.datos_deca = datos


def modificar_expedicion(
    db: Session, *, expedicion: Expedicion, modificacion: ModificacionCreate, origen: str
) -> ModificacionDeca:
    """Modificación durante el servicio — Apartado Quinto, método 1,
    BOE-A-2026-12784: se añaden al mismo PDF los datos nuevos y el motivo,
    indicando los anteriores como no válidos. La URL y el QR no cambian.
    El PDF anterior se conserva para trazabilidad. El PDF modificado debe
    entregarse al conductor (lo indica el panel)."""
    if expedicion.estado != "en_transito":
        raise ModificacionNoPermitidaError(
            "Solo se puede modificar un DeCA durante el servicio (expedición en tránsito)"
        )

    cambios: list[dict] = []
    for cambio in modificacion.cambios:
        campo = cambio.campo.strip()
        if campo in CAMPOS_INDEXADOS:
            try:
                _aplicar_campo_indexado(expedicion, campo, cambio.nuevo.strip())
            except ValueError as exc:
                raise ModificacionNoPermitidaError(str(exc)) from exc
            campo = CAMPOS_INDEXADOS[campo]
        cambios.append({"campo": campo, "anterior": cambio.anterior.strip(), "nuevo": cambio.nuevo.strip()})

    numero = (
        db.execute(
            select(func.count()).select_from(ModificacionDeca).where(ModificacionDeca.expedicion_id == expedicion.id)
        ).scalar_one()
        + 1
    )
    ahora = datetime.now(timezone.utc)
    storage = get_storage()
    pdf_anterior = storage.leer(expedicion.storage_path)

    pdf_nuevo = anadir_pagina_modificacion(
        pdf_bytes=pdf_anterior, numero=numero, motivo=modificacion.motivo.strip(), cambios=cambios, ahora=ahora,
        zona=(expedicion.datos_deca or {}).get("zona_horaria"),
    )
    # Añadir una página invalida la firma anterior: si el DeCA iba firmado
    # se vuelve a firmar entero (Apartado Cuarto).
    pdf_nuevo, firmado, certificado = _firmar_si_procede(
        db, tenant_id=expedicion.tenant_id, finalidad_contractual=expedicion.firmado, pdf_bytes=pdf_nuevo
    )

    # Primero se guarda la versión anterior; después se sustituye la
    # vigente. Si falla la BD se restaura la vigente anterior.
    ruta_anterior = storage.guardar(expedicion.tenant_id, expedicion.id, pdf_anterior, sufijo=f".v{numero - 1}")
    storage.guardar(expedicion.tenant_id, expedicion.id, pdf_nuevo)
    try:
        registro = ModificacionDeca(
            expedicion_id=expedicion.id,
            tenant_id=expedicion.tenant_id,
            numero=numero,
            fecha=ahora,
            motivo=modificacion.motivo.strip(),
            cambios=cambios,
            ruta_pdf_anterior=ruta_anterior,
            origen=origen[:200],
        )
        expedicion.tamano_bytes = len(pdf_nuevo)
        if firmado and certificado is not None:
            expedicion.certificado_id = certificado.id
        db.add(registro)
        db.add(expedicion)
        quien = origen.split(":", 1)[1] if origen.startswith("panel:") else ("API" if origen == "api" else origen)
        registrar_evento(db, expedicion, "modificada", quien, f"Modificación n.º {numero}: {modificacion.motivo.strip()}"[:1000])
        db.commit()
    except Exception:
        db.rollback()
        storage.guardar(expedicion.tenant_id, expedicion.id, pdf_anterior)
        raise
    db.refresh(registro)
    return registro
