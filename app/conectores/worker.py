"""Proceso que ejecuta periódicamente las fuentes de datos activas.

    python -m app.conectores.worker

En Docker es el servicio "conectores" (docker-compose.yml). Un fallo en
una fuente nunca detiene el proceso: queda anotado en la propia fuente.
"""

from __future__ import annotations

import logging
import signal
import time

from app.config import get_settings
from app.database import SessionLocal
from app.services.fuentes import ejecutar_fuente, fuentes_pendientes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("conectores")

_parar = False


def _senal(_signum, _frame) -> None:
    global _parar
    _parar = True


def una_pasada() -> int:
    db = SessionLocal()
    ejecutadas = 0
    try:
        for fuente in fuentes_pendientes(db):
            resumen = ejecutar_fuente(db, fuente)
            logger.info("Fuente %s (%s): %s", fuente.id, fuente.tipo, resumen)
            ejecutadas += 1
    except Exception:
        logger.exception("Error en la pasada de conectores")
        db.rollback()
    finally:
        db.close()
    return ejecutadas


def avisos_por_correo(redis) -> None:
    """Resumen diario de avisos a los administradores (si hay SMTP)."""
    from app.services import avisos_correo

    if not avisos_correo.configurado():
        return
    db = SessionLocal()
    try:
        avisos_correo.pasada(db, redis)
    except Exception:
        logger.exception("Error en los avisos por correo")
        db.rollback()
    finally:
        db.close()


def main() -> None:
    signal.signal(signal.SIGTERM, _senal)
    signal.signal(signal.SIGINT, _senal)
    ciclo = max(15, get_settings().conectores_ciclo_segundos)
    logger.info("Conectores en marcha (ciclo de %s s)", ciclo)
    import redis as _redis

    conexion = _redis.Redis.from_url(get_settings().redis_url, socket_timeout=5, socket_connect_timeout=5)
    while not _parar:
        una_pasada()
        avisos_por_correo(conexion)
        for _ in range(ciclo):
            if _parar:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
