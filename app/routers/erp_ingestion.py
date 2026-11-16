"""Endpoint de ingesta REST para ERPs con capacidad de webhook/API
(SAP, Dynamics 365, Odoo, integraciones a medida de a3ERP/Sigrid vía su
SDK). Autenticado con API key de tenant (cabecera X-API-Key).

Dos endpoints, uno por cada camino de ingesta (ver
app/domain/schemas.py y app/services/expedicion_service.py):

- `POST /api/v1/expediciones` (multipart/form-data): el ERP ya genera su
  propio PDF (albarán/factura/declaración de contenido) — se sube tal
  cual y MADeCa le estampa el QR.
- `POST /api/v1/expediciones/datos` (JSON): el ERP no genera PDF propio,
  manda los datos completos y MADeCa redacta el documento.

Para ERPs sin API (exportación a fichero), ver
app/adapters/hotfolder_watcher.py.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.domain.models import AccesoLog, Expedicion
from app.domain.schemas import DecaCreate, DecaEstado, ExpedicionMetadata, ExpedicionOut, ModificacionCreate
from app.routers.contraparte import url_contraparte
from app.security.dependencies import obtener_tenant_por_api_key
from app.services.expedicion_service import (
    CertificadoNoDisponibleError,
    ExpedicionYaExisteError,
    ModificacionNoPermitidaError,
    crear_expedicion_desde_datos,
    crear_expedicion_desde_pdf,
    finalizar_servicio,
    modificar_expedicion,
)
from app.services.pdf_generator import (
    MotorPdfNoDisponibleError,
    PdfDemasiadoGrandeError,
    PdfInvalidoError,
    PdfPosibleEscaneoError,
)
from app.services.signing import FirmaError

router = APIRouter(prefix="/api/v1", tags=["ingesta-erp"])

TAMANO_MAXIMO_PDF_BYTES = 5 * 1024 * 1024  # coincide con el límite legal, ver pdf_generator.py


def errores_validacion(exc: ValidationError) -> list[dict]:
    """Errores de validación aptos para JSON. Sin `ctx` (lleva el objeto
    ValueError, que no se puede serializar y convertía el 422 en un 500 sin
    explicación para el ERP) ni `input` (no se devuelven los datos enviados)."""
    return exc.errors(include_url=False, include_context=False, include_input=False)


def _a_out(exp: Expedicion, base_url: str, db: Session) -> ExpedicionOut:
    numero_escaneos = db.execute(
        select(func.count()).select_from(AccesoLog).where(AccesoLog.expedicion_id == exp.id)
    ).scalar_one()
    return ExpedicionOut(
        id=str(exp.id),
        tenant_id=str(exp.tenant_id),
        referencia_externa=exp.referencia_externa,
        erp_origen=exp.erp_origen,
        estado=DecaEstado(exp.estado),
        fecha_transporte=exp.fecha_transporte,
        fecha_emision=exp.fecha_emision,
        matricula_vehiculo=exp.matricula_vehiculo,
        matricula_remolque=exp.matricula_remolque,
        url_publica=f"{base_url.rstrip('/')}/inspect/{exp.id}",
        firmado=exp.firmado,
        numero_escaneos=numero_escaneos,
        url_contraparte=url_contraparte(base_url, exp),
    )


@router.post("/expediciones", response_model=ExpedicionOut, status_code=status.HTTP_201_CREATED)
async def crear_expedicion_desde_pdf_endpoint(
    referencia_externa: str = Form(...),
    matricula_vehiculo: str = Form(...),
    fecha_transporte: str = Form(..., description="ISO 8601, p. ej. 2026-08-10T09:00:00Z"),
    matricula_remolque: str | None = Form(None),
    pais_matricula_vehiculo: str = Form("ES", description="ISO 3166-1 alfa-2; distinto de ES en cabotaje"),
    pais_matricula_remolque: str = Form("ES"),
    finalidad_contractual: bool = Form(False),
    erp_origen: str = Form("generico"),
    archivo: UploadFile = File(..., description="El PDF tal cual lo genera el ERP (albarán/factura/declaración de contenido)"),
    tenant_id: uuid.UUID = Depends(obtener_tenant_por_api_key),
    db: Session = Depends(get_db),
) -> ExpedicionOut:
    """Camino 1: el ERP ya genera su propio PDF, solo se le estampa el QR."""
    from app.config import get_settings

    nombre = (archivo.filename or "").lower()
    if archivo.content_type != "application/pdf" and not nombre.endswith(".pdf"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El archivo debe ser un PDF")

    pdf_bytes = await archivo.read()
    if len(pdf_bytes) > TAMANO_MAXIMO_PDF_BYTES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"El PDF supera el máximo de {TAMANO_MAXIMO_PDF_BYTES} bytes (Apartado Segundo.1 BOE-A-2026-12784)",
        )

    try:
        metadata = ExpedicionMetadata.model_validate(
            {
                "referencia_externa": referencia_externa,
                "matricula_vehiculo": matricula_vehiculo,
                "matricula_remolque": matricula_remolque,
                "pais_matricula_vehiculo": pais_matricula_vehiculo,
                "pais_matricula_remolque": pais_matricula_remolque,
                "fecha_transporte": fecha_transporte,
                "finalidad_contractual": finalidad_contractual,
                "erp_origen": erp_origen,
            }
        )
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"mensaje": "Metadatos inválidos", "errores": errores_validacion(exc)},
        ) from exc

    try:
        # En un hilo: estampar y firmar no debe bloquear el servidor (el
        # visor público del QR tiene que seguir respondiendo).
        exp = await run_in_threadpool(crear_expedicion_desde_pdf, db, tenant_id=tenant_id, metadata=metadata, pdf_original_bytes=pdf_bytes,
                                      quien="API", detalle="API: PDF del ERP")
    except ExpedicionYaExisteError:
        exp = db.execute(
            select(Expedicion).where(
                Expedicion.tenant_id == tenant_id,
                Expedicion.erp_origen == metadata.erp_origen.value,
                Expedicion.referencia_externa == metadata.referencia_externa,
            )
        ).scalar_one()
    except CertificadoNoDisponibleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (PdfInvalidoError, PdfPosibleEscaneoError, PdfDemasiadoGrandeError, FirmaError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return _a_out(exp, get_settings().app_base_url, db)


@router.post("/expediciones/datos", response_model=ExpedicionOut, status_code=status.HTTP_201_CREATED)
def crear_expedicion_desde_datos_endpoint(
    payload: dict[str, Any],
    erp_origen: str = "generico",
    tenant_id: uuid.UUID = Depends(obtener_tenant_por_api_key),
    db: Session = Depends(get_db),
) -> ExpedicionOut:
    """Camino 2: el ERP no genera PDF propio, manda los datos completos
    y MADeCa redacta el documento (valida los campos mínimos Art. 6)."""
    from app.config import get_settings

    try:
        deca = DecaCreate.model_validate({**payload, "erp_origen": erp_origen})
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"mensaje": "Datos DeCA inválidos", "errores": errores_validacion(exc)},
        ) from exc

    try:
        exp = crear_expedicion_desde_datos(db, tenant_id=tenant_id, deca=deca, quien="API", detalle="API: datos del ERP")
    except ExpedicionYaExisteError:
        exp = db.execute(
            select(Expedicion).where(
                Expedicion.tenant_id == tenant_id,
                Expedicion.erp_origen == deca.erp_origen.value,
                Expedicion.referencia_externa == deca.referencia_externa,
            )
        ).scalar_one()
    except CertificadoNoDisponibleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (PdfDemasiadoGrandeError, FirmaError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except MotorPdfNoDisponibleError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return _a_out(exp, get_settings().app_base_url, db)


@router.patch("/expediciones/{expedicion_id}/finalizar", response_model=ExpedicionOut)
def finalizar_expedicion_endpoint(
    expedicion_id: uuid.UUID,
    estado_final: DecaEstado = DecaEstado.ENTREGADO,
    tenant_id: uuid.UUID = Depends(obtener_tenant_por_api_key),
    db: Session = Depends(get_db),
) -> ExpedicionOut:
    from app.config import get_settings

    exp = db.get(Expedicion, expedicion_id)
    if exp is None or exp.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expedición no encontrada")

    try:
        exp = finalizar_servicio(db, expedicion=exp, estado_final=estado_final.value, quien="API")
    except ModificacionNoPermitidaError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _a_out(exp, get_settings().app_base_url, db)


@router.post("/expediciones/{expedicion_id}/modificaciones", response_model=ExpedicionOut)
def modificar_expedicion_endpoint(
    expedicion_id: uuid.UUID,
    modificacion: ModificacionCreate,
    tenant_id: uuid.UUID = Depends(obtener_tenant_por_api_key),
    db: Session = Depends(get_db),
) -> ExpedicionOut:
    """Modificación durante el servicio (Apartado Quinto, método 1): misma
    URL y mismo QR. El PDF modificado debe entregarse al conductor."""
    from app.config import get_settings

    exp = db.get(Expedicion, expedicion_id)
    if exp is None or exp.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expedición no encontrada")
    try:
        modificar_expedicion(db, expedicion=exp, modificacion=modificacion, origen="api")
    except ModificacionNoPermitidaError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except CertificadoNoDisponibleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (PdfInvalidoError, PdfDemasiadoGrandeError, FirmaError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    db.refresh(exp)
    return _a_out(exp, get_settings().app_base_url, db)


@router.get("/expediciones/{expedicion_id}/pdf")
def descargar_pdf_endpoint(
    expedicion_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(obtener_tenant_por_api_key),
    db: Session = Depends(get_db),
):
    """PDF vigente (con modificaciones, si las hay) para entregarlo al
    conductor (Apartado Séptimo)."""
    from app.services.copia_conductor import respuesta_pdf

    exp = db.get(Expedicion, expedicion_id)
    if exp is None or exp.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expedición no encontrada")
    return respuesta_pdf(exp)


@router.get("/expediciones/{expedicion_id}/qr.png")
def descargar_qr_endpoint(
    expedicion_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(obtener_tenant_por_api_key),
    db: Session = Depends(get_db),
):
    """QR como fichero independiente (Apartado Séptimo)."""
    from app.services.copia_conductor import respuesta_qr

    exp = db.get(Expedicion, expedicion_id)
    if exp is None or exp.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expedición no encontrada")
    return respuesta_qr(exp)
