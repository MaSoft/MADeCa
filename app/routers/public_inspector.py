"""Visor público de inspección — Apartado Tercero, BOE-A-2026-12784.

GET /inspect/{id}: acceso libre e inmediato, sin usuario, sin contraseña,
sin pasos intermedios. El id es un UUIDv4 opaco (122 bits aleatorios, no
correlativo) que actúa como control de acceso de facto: adivinar uno por
fuerza bruta no es viable.

Sin límite de peticiones por IP a propósito: el Apartado Tercero.4 no
permite que las medidas de seguridad impidan la descarga directa, y un
límite por IP bloquearía a agentes que salen a internet por la misma IP
pública (redes corporativas, CGNAT de operadores móviles). La protección
frente a denegación de servicio corresponde al proxy/WAF de delante.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.domain.models import AccesoLog, Expedicion
from app.services.retention import url_publica_activa
from app.services.storage import get_storage

router = APIRouter(tags=["visor-publico"])


def _registrar_acceso(db: Session, expedicion_id: uuid.UUID, request: Request, resultado: str) -> None:
    db.add(
        AccesoLog(
            expedicion_id=expedicion_id,
            ip=request.client.host if request.client else "desconocida",
            # La columna admite 500 caracteres: un user-agent más largo hacía
            # fallar el registro y el agente recibía un error en vez del DeCA.
            user_agent=(request.headers.get("user-agent") or "")[:500] or None,
            resultado=resultado,
        )
    )
    db.commit()


@router.get("/inspect/{expedicion_id}")
def ver_deca_publico(
    request: Request,
    expedicion_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    expedicion = db.get(Expedicion, expedicion_id)

    if expedicion is None:
        # Nunca distinguir "no existe" de "no autorizado": ambos 404,
        # para no dar pistas a un escaneo de enumeración.
        return PlainTextResponse("Documento no encontrado.", status_code=404)

    if not url_publica_activa(expedicion, settings):
        _registrar_acceso(db, expedicion_id, request, "denegado_desactivado")
        return PlainTextResponse(
            "Este documento ya no está disponible para consulta pública "
            "(servicio finalizado hace más de "
            f"{settings.public_url_deactivation_days} días). "
            "Sigue conservado y accesible para auditoría por el operador.",
            status_code=410,
        )

    storage = get_storage()
    pdf_bytes = storage.leer(expedicion.storage_path)

    _registrar_acceso(db, expedicion_id, request, "concedido")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            # "inline" -> se abre directo en el visor del navegador sin
            # pasos intermedios, y el propio visor de PDF nativo permite
            # descargar/guardar (cumple "opción de descarga" sin forzar
            # una pantalla propia con botones).
            "Content-Disposition": f'inline; filename="DeCA_{expedicion_id}.pdf"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
