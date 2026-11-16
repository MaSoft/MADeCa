"""Rate limiting de los formularios de login (fuerza bruta de
contraseñas). Usa slowapi con backend Redis para que el límite se
comparta entre todos los workers/instancias del despliegue.

El visor público /inspect NO lleva límite: ver public_inspector.py
(Apartado Tercero.4 BOE-A-2026-12784).
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.redis_url,
    strategy="fixed-window",
)
