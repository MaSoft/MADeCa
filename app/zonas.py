"""Zonas horarias del DeCA.

En España hay dos horas oficiales: la peninsular (también Baleares, Ceuta
y Melilla) y la de Canarias, una hora menos. La fecha y hora del
transporte (art. 6.f Orden FOM/2861/2012) se guarda siempre con su zona y
se imprime en la zona del propio transporte, indicándola: si no, un envío
que sale a las 06:31 en Tenerife aparecía como «07:31».
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PENINSULA = "Europe/Madrid"
CANARIAS = "Atlantic/Canary"

ZONAS: dict[str, str] = {
    PENINSULA: "hora peninsular",
    CANARIAS: "hora de Canarias",
}


def validar_zona(nombre: str) -> str:
    nombre = (nombre or "").strip()
    if nombre not in ZONAS:
        raise ValueError(f"Zona horaria no admitida: '{nombre}' (usa {' o '.join(ZONAS)})")
    return nombre


def zona_predeterminada() -> str:
    """La de la instalación (ZONA_HORARIA en .env); peninsular si no hay
    configuración disponible (p. ej. en el conector local)."""
    try:
        from app.config import get_settings

        return validar_zona(get_settings().zona_horaria)
    except Exception:
        return PENINSULA


def zona_de(fecha: datetime) -> str:
    """Zona de una fecha con desfase: la peninsular y la canaria siempre se
    diferencian en una hora, así que el desfase basta para distinguirlas.
    Si no coincide con ninguna (p. ej. UTC), la predeterminada."""
    desfase = fecha.utcoffset()
    for nombre in (CANARIAS, PENINSULA):
        if desfase == fecha.astimezone(ZoneInfo(nombre)).utcoffset():
            return nombre
    return zona_predeterminada()


def con_zona(fecha: datetime, zona: str | None = None) -> tuple[datetime, str]:
    """(fecha con zona, nombre de la zona). Una fecha sin zona se toma como
    hora local de `zona` (o de la predeterminada)."""
    if fecha.tzinfo is None:
        nombre = validar_zona(zona) if zona else zona_predeterminada()
        return fecha.replace(tzinfo=ZoneInfo(nombre)), nombre
    return fecha, validar_zona(zona) if zona else zona_de(fecha)


def local(fecha: datetime, zona: str | None = None) -> datetime:
    """La fecha en la hora local de `zona` (predeterminada si no se indica).
    Una fecha sin zona se considera UTC (así se guardan las del sistema)."""
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return fecha.astimezone(ZoneInfo(validar_zona(zona) if zona else zona_predeterminada()))


def texto(fecha: datetime, zona: str | None = None) -> str:
    """«10/10/2026 06:31 (hora de Canarias)»."""
    nombre = validar_zona(zona) if zona else zona_predeterminada()
    return f"{local(fecha, nombre):%d/%m/%Y %H:%M} ({ZONAS[nombre]})"
