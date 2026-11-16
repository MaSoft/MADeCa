"""Reglas de disponibilidad/retención del visor público.

- Apartado Segundo.4: conservación mínima 1 año -> nunca se borra el
  fichero ni el registro antes de ese plazo (purgar_expirados solo actúa
  sobre expediciones más antiguas que RETENTION_DAYS).
- Apartado Tercero.5: la descarga vía QR puede desactivarse 7 días
  después de finalizar el servicio -> es una facultad, no un borrado;
  el documento sigue conservado y disponible para auditoría interna.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.domain.models import Expedicion


def url_publica_activa(expedicion: Expedicion, settings: Settings) -> bool:
    if not expedicion.url_activa:
        return False
    if expedicion.estado == "anulado":
        return False
    if expedicion.fecha_finalizacion_servicio is None:
        return True  # servicio en curso: debe permanecer accesible
    limite = expedicion.fecha_finalizacion_servicio + timedelta(
        days=settings.public_url_deactivation_days
    )
    return datetime.now(timezone.utc) < limite


def puede_purgarse(expedicion: Expedicion, settings: Settings) -> bool:
    """Solo para el job de purga de almacenamiento: nunca antes del año
    mínimo de conservación, independientemente del estado de la URL."""
    limite = expedicion.fecha_emision + timedelta(days=settings.retention_days)
    return datetime.now(timezone.utc) > limite
