"""Gestión y ejecución de fuentes de datos automáticas."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.conectores.base import ConectorError
from app.conectores.registro import TIPOS, crear_conector
from app.domain.models import FuenteDatos, Tenant
from app.security.secrets import cifrar, descifrar
from app.services.borradores import crear_borrador, procesar_documento

logger = logging.getLogger("fuentes")


def leer_config(fuente: FuenteDatos) -> dict:
    return json.loads(descifrar(fuente.config_cifrada))


def guardar_config(fuente: FuenteDatos, nueva: dict) -> None:
    """Los campos secretos vacíos conservan el valor anterior: el
    navegador nunca recibe los secretos, así que no puede reenviarlos."""
    anterior = leer_config(fuente) if fuente.config_cifrada else {}
    secretos = {c.nombre for c in TIPOS[fuente.tipo].CAMPOS if c.secreto}
    final = {}
    for campo in TIPOS[fuente.tipo].CAMPOS:
        valor = (nueva.get(campo.nombre) or "").strip()
        if not valor and campo.nombre in secretos:
            valor = anterior.get(campo.nombre, "")
        final[campo.nombre] = valor
    fuente.config_cifrada = cifrar(json.dumps(final))


def config_visible(fuente: FuenteDatos) -> dict:
    """Configuración para mostrar: los secretos solo como 'guardado'."""
    config = leer_config(fuente)
    return {
        c.nombre: ("" if c.secreto else config.get(c.nombre, ""))
        for c in TIPOS[fuente.tipo].CAMPOS
    } | {f"{c.nombre}__guardado": bool(config.get(c.nombre)) for c in TIPOS[fuente.tipo].CAMPOS if c.secreto}


def conector_de(fuente: FuenteDatos, cliente=None):
    return crear_conector(fuente.tipo, leer_config(fuente), tenant_id=str(fuente.tenant_id), cliente=cliente)


def ejecutar_fuente(db: Session, fuente: FuenteDatos, cliente=None) -> str:
    """Una pasada: trae lo nuevo y lo deja en la bandeja. Devuelve el
    resumen. Si falla, el estado de sincronización no avanza (se
    reintenta en la próxima pasada) y el error queda en la fuente."""
    tenant = db.get(Tenant, fuente.tenant_id)
    ahora = datetime.now(timezone.utc)
    if tenant is None or not tenant.activo:
        fuente.ultima_ejecucion = ahora
        fuente.ultimo_error = "La empresa está desactivada"
        db.add(fuente)
        db.commit()
        return fuente.ultimo_error
    origen = f"fuente:{fuente.id}"
    try:
        elementos, nuevo_estado = conector_de(fuente, cliente).sincronizar(dict(fuente.estado_sync or {}))
        nuevos = 0
        for e in elementos:
            if e.contenido is not None:
                _b, creado = procesar_documento(db, tenant=tenant, origen=origen, referencia_fuente=e.referencia,
                                                nombre=e.nombre, contenido=e.contenido)
            else:
                _b, creado = crear_borrador(db, tenant_id=tenant.id, origen=origen, referencia_fuente=e.referencia,
                                            nombre_documento=e.nombre, datos=e.datos or {}, confianza=e.confianza,
                                            avisos=e.avisos)
            nuevos += int(creado)
        fuente.estado_sync = nuevo_estado
        fuente.ultimo_error = None
        fuente.ultimo_resultado = f"{nuevos} nuevo(s) en la bandeja ({len(elementos)} leído(s))"
    except ConectorError as exc:
        db.rollback()
        fuente.ultimo_error = str(exc)[:2000]
    except Exception as exc:
        db.rollback()
        logger.exception("Fallo no previsto en la fuente %s", fuente.id)
        # Mensaje genérico: una excepción interna podría incluir datos de
        # la petición (cabeceras con credenciales).
        fuente.ultimo_error = f"Error inesperado ({type(exc).__name__}); revisa el registro del servidor"
    fuente.ultima_ejecucion = ahora
    db.add(fuente)
    db.commit()
    return fuente.ultimo_error or fuente.ultimo_resultado


def fuentes_pendientes(db: Session, ahora: datetime | None = None) -> list[FuenteDatos]:
    ahora = ahora or datetime.now(timezone.utc)
    activas = db.execute(select(FuenteDatos).where(FuenteDatos.activo.is_(True))).scalars().all()
    return [
        f for f in activas
        if f.ultima_ejecucion is None or f.ultima_ejecucion + timedelta(minutes=max(5, f.intervalo_minutos)) <= ahora
    ]


def nueva_fuente(*, tenant_id: uuid.UUID, tipo: str, nombre: str, config: dict, intervalo_minutos: int = 15) -> FuenteDatos:
    if tipo not in TIPOS:
        raise ConectorError("Tipo de fuente desconocido")
    fuente = FuenteDatos(id=uuid.uuid4(), tenant_id=tenant_id, tipo=tipo, nombre=nombre.strip()[:150] or TIPOS[tipo].titulo,
                         config_cifrada="", estado_sync={}, activo=True,
                         intervalo_minutos=max(5, min(int(intervalo_minutos), 1440)), fecha_alta=datetime.now(timezone.utc))
    guardar_config(fuente, config)
    return fuente
