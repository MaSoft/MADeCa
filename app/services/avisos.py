"""Avisos de una empresa: lo que necesita atención.

Los muestra el inicio del panel y los envía el resumen diario por correo
(app/services/avisos_correo.py): una sola fuente para que nunca digan
cosas distintas.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import BorradorDeca, Certificado, FuenteDatos

DIAS_AVISO_CERTIFICADO = 30
HORAS_PENDIENTE_ANTIGUO = 24


def avisos_empresa(db: Session, tenant_id, ahora: datetime | None = None, es_admin: bool = True) -> list[dict]:
    """[{nivel: aviso|grave, texto, enlace, accion}] ordenados por gravedad."""
    from app.plantillas import fecha

    ahora = ahora or datetime.now(timezone.utc)
    avisos: list[dict] = []
    for c in db.execute(
        select(Certificado).where(Certificado.tenant_id == tenant_id, Certificado.activo.is_(True))
    ).scalars().all():
        if c.fecha_caducidad is None:
            continue
        if c.fecha_caducidad < ahora:
            avisos.append({"nivel": "grave", "texto": f"El certificado «{c.nombre}» caducó el {fecha(c.fecha_caducidad)}.",
                           "enlace": "/dashboard/certificados", "accion": "Ver certificados"})
        elif c.fecha_caducidad < ahora + timedelta(days=DIAS_AVISO_CERTIFICADO):
            dias = (c.fecha_caducidad - ahora).days
            avisos.append({"nivel": "aviso",
                           "texto": f"El certificado «{c.nombre}» caduca en {dias} {'día' if dias == 1 else 'días'} ({fecha(c.fecha_caducidad)}).",
                           "enlace": "/dashboard/certificados", "accion": "Ver certificados"})
    antiguos = db.execute(
        select(func.count()).select_from(BorradorDeca).where(
            BorradorDeca.tenant_id == tenant_id, BorradorDeca.estado == "pendiente",
            BorradorDeca.fecha_alta < ahora - timedelta(hours=HORAS_PENDIENTE_ANTIGUO),
        )
    ).scalar_one()
    if antiguos:
        avisos.append({"nivel": "aviso",
                       "texto": f"{antiguos} {'borrador lleva' if antiguos == 1 else 'borradores llevan'} más de {HORAS_PENDIENTE_ANTIGUO} h sin revisar.",
                       "enlace": "/dashboard/bandeja?orden=recibido", "accion": "Revisar"})
    for fuente in db.execute(
        select(FuenteDatos).where(FuenteDatos.tenant_id == tenant_id, FuenteDatos.activo.is_(True),
                                  FuenteDatos.ultimo_error.is_not(None))
    ).scalars().all():
        avisos.append({"nivel": "grave", "texto": f"La fuente de datos «{fuente.nombre}» dio error en su última lectura.",
                       "enlace": "/dashboard/fuentes", "accion": "Ver fuentes" if es_admin else None})
    return sorted(avisos, key=lambda a: a["nivel"] != "grave")
