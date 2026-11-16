"""Descarga del DeCA por la otra parte — Apartado Segundo.4,
BOE-A-2026-12784.

El cargador contractual y el transportista efectivo deben conservar el
DeCA al menos un año; si uno de los dos lo genera, al otro le basta con
poder descargarlo durante ese año. La URL pública del QR puede
desactivarse 7 días después del servicio (Tercero.5), así que la otra
parte necesita su propio enlace: /descarga/{token}, con un token de 256
bits que solo conocen el emisor y la parte a la que se lo envíe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.domain.models import AccesoLog, Expedicion
from app.services.storage import get_storage

router = APIRouter(tags=["contraparte"])


def url_contraparte(base_url: str, exp: Expedicion) -> str | None:
    if not exp.token_contraparte:
        return None
    return f"{base_url.rstrip('/')}/descarga/{exp.token_contraparte}"


@router.get("/descarga/{token}")
def descargar_contraparte(token: str, request: Request, db: Session = Depends(get_db)):
    no_encontrado = PlainTextResponse("Documento no encontrado.", status_code=404)
    if len(token) != 64:
        return no_encontrado
    exp = db.execute(select(Expedicion).where(Expedicion.token_contraparte == token)).scalar_one_or_none()
    if exp is None:
        return no_encontrado

    settings = get_settings()
    if datetime.now(timezone.utc) > exp.fecha_emision + timedelta(days=settings.retention_days):
        return PlainTextResponse("El plazo de descarga de este documento ha terminado.", status_code=410)

    db.add(
        AccesoLog(
            expedicion_id=exp.id,
            ip=request.client.host if request.client else "desconocida",
            user_agent=(request.headers.get("user-agent") or "")[:500] or None,
            resultado="contraparte",
        )
    )
    db.commit()
    return Response(
        content=get_storage().leer(exp.storage_path),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="DeCA_{exp.id}.pdf"',
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )
