"""Actividad de una expedición para el panel: quién hizo qué y cuándo.

Desde la v0.3.0 cada acción queda en eventos_expedicion. De las
expediciones anteriores se reconstruye con lo que ya constaba (fecha de
emisión, borrador de la bandeja con quién lo resolvió, modificaciones y
fecha de entrega), indicando cuando no se sabe quién fue.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import BorradorDeca, EventoExpedicion, Expedicion, ModificacionDeca

NO_REGISTRADO = "No consta (anterior al registro de actividad)"
ACCIONES = {
    "emitida": "DeCA emitido",
    "modificada": "Modificado durante el servicio",
    "entregado": "Marcado como entregado",
    "anulado": "Anulado",
}


def _paso(fecha: datetime, accion: str, quien: str, detalle: str | None = None) -> dict:
    return {"fecha": fecha, "accion": accion, "texto": ACCIONES.get(accion, accion), "quien": quien, "detalle": detalle}


def actividad(db: Session, exp: Expedicion, modificaciones: list[ModificacionDeca] | None = None) -> list[dict]:
    """Pasos del más antiguo al más reciente."""
    eventos = db.execute(
        select(EventoExpedicion).where(EventoExpedicion.expedicion_id == exp.id).order_by(EventoExpedicion.fecha)
    ).scalars().all()
    pasos = [_paso(e.fecha, e.accion, e.quien, e.detalle) for e in eventos]
    hay = {e.accion for e in eventos}

    if "emitida" not in hay and exp.fecha_emision:
        borrador = db.execute(
            select(BorradorDeca).where(BorradorDeca.expedicion_id == exp.id).limit(1)
        ).scalars().first()
        if borrador is not None:
            pasos.append(_paso(exp.fecha_emision, "emitida", borrador.resuelto_por or NO_REGISTRADO,
                               f"Bandeja de revisión: {borrador.nombre_documento or borrador.referencia_fuente}"))
        else:
            pasos.append(_paso(exp.fecha_emision, "emitida", NO_REGISTRADO, f"Origen: {exp.erp_origen}"))

    if "modificada" not in hay:
        if modificaciones is None:
            modificaciones = db.execute(
                select(ModificacionDeca).where(ModificacionDeca.expedicion_id == exp.id)
            ).scalars().all()
        for m in modificaciones:
            quien = m.origen.split(":", 1)[1] if m.origen.startswith("panel:") else ("API" if m.origen == "api" else m.origen)
            pasos.append(_paso(m.fecha, "modificada", quien, f"Modificación n.º {m.numero}: {m.motivo}"))

    if exp.fecha_finalizacion_servicio and not hay & {"entregado", "anulado"} and exp.estado in ("entregado", "anulado"):
        pasos.append(_paso(exp.fecha_finalizacion_servicio, exp.estado, NO_REGISTRADO))

    return sorted(pasos, key=lambda p: p["fecha"])
