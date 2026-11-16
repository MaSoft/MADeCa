"""Ayudas del formulario del DeCA mientras se escribe (panel, con sesión).

- POST /dashboard/formulario/validar: comprueba un campo con las mismas
  reglas que al emitir (app/revision.py) y avisa de posibles duplicados.
- POST /dashboard/formulario/partes: agenda de cargadores, transportistas
  y destinatarios sacada de los DeCA ya emitidos por la empresa, para
  rellenar nombre, NIF y domicilio de una vez.

POST y no GET: los NIF no deben quedar en direcciones ni en registros de
acceso. Todo se limita a la empresa de la sesión.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.domain.models import Expedicion
from app.filtros import contiene, texto_busqueda
from app.revision import validar_campo
from app.security.dependencies import SesionUsuario, obtener_usuario_actual

router = APIRouter(prefix="/dashboard/formulario", tags=["formulario"])

PARTES = ("cargador", "transportista", "destinatario")
MAX_SUGERENCIAS = 8
# Se buscan entre los DeCA más recientes que coinciden: suficiente para
# una agenda y acotado aunque la empresa tenga muchos.
MAX_FILAS = 400


def _normalizar_nif(nif: str) -> str:
    return "".join(c for c in (nif or "").upper() if c.isalnum())


@router.post("/validar")
def validar(
    campo: str = Form(..., max_length=60),
    valor: str = Form("", max_length=500),
    pais: str | None = Form(None, max_length=2),
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
) -> dict:
    respuesta: dict = {"ok": True, "mensaje": None, "aviso": None}
    mensaje = validar_campo(campo, valor, pais)
    if mensaje:
        respuesta.update(ok=False, mensaje=mensaje)
    if campo == "referencia_externa" and valor.strip():
        previa = db.execute(
            select(Expedicion.id, Expedicion.fecha_emision)
            .where(Expedicion.tenant_id == sesion.tenant_id, Expedicion.referencia_externa == valor.strip())
            .order_by(Expedicion.fecha_emision.desc()).limit(1)
        ).first()
        if previa:
            from app.plantillas import fecha

            respuesta["aviso"] = {
                "texto": f"Ya hay una expedición con esta referencia (emitida el {fecha(previa.fecha_emision)}).",
                "enlace": f"/dashboard/expediciones/{previa.id}",
            }
    return respuesta


@router.post("/partes")
def partes(
    texto: str = Form(..., max_length=100),
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
) -> dict:
    """Partes de DeCA anteriores cuyo nombre o NIF contiene el texto. Una
    por NIF, con el nombre y domicilio más recientes y cuántas veces se ha
    usado."""
    texto = texto_busqueda(texto)
    if len(texto) < 2:
        return {"partes": []}
    columnas = []
    condiciones = []
    for parte in PARTES:
        nombre = Expedicion.datos_deca[(parte, "nombre_razon_social")].astext
        nif = Expedicion.datos_deca[(parte, "nif_cif")].astext
        columnas += [nombre, nif, Expedicion.datos_deca[(parte, "domicilio")].astext]
        condiciones += [contiene(nombre, texto), contiene(nif, texto)]
    filas = db.execute(
        select(*columnas)
        .where(Expedicion.tenant_id == sesion.tenant_id, or_(*condiciones))
        .order_by(Expedicion.fecha_emision.desc())
        .limit(MAX_FILAS)
    ).all()

    buscado = texto.lower()
    agenda: dict[str, dict] = {}
    for fila in filas:  # de la más reciente a la más antigua
        for i in range(len(PARTES)):
            nombre, nif, domicilio = fila[3 * i], fila[3 * i + 1], fila[3 * i + 2]
            if not nombre or not nif:
                continue
            if buscado not in nombre.lower() and buscado not in nif.lower():
                continue
            clave = _normalizar_nif(nif)
            if clave in agenda:
                agenda[clave]["usos"] += 1
                if not agenda[clave]["domicilio"] and domicilio:
                    agenda[clave]["domicilio"] = domicilio
            else:
                agenda[clave] = {"nombre": nombre, "nif": nif, "domicilio": domicilio or "", "usos": 1}
    mejores = sorted(agenda.values(), key=lambda p: -p["usos"])[:MAX_SUGERENCIAS]
    return {"partes": mejores}
