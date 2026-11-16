"""Panel: fuentes de datos automáticas y autorización de OCR de terceros.
Solo administradores de la empresa."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.conectores.base import ConectorError
from app.conectores.registro import TIPOS
from app.database import get_db
from app.domain.models import FuenteDatos, Tenant, Usuario
from app.security.dependencies import SesionUsuario, requerir_rol
from app.services.fuentes import config_visible, conector_de, ejecutar_fuente, guardar_config, nueva_fuente
from app.plantillas import crear_plantillas

router = APIRouter(prefix="/dashboard/fuentes", tags=["fuentes"])
templates = crear_plantillas()
solo_admin = requerir_rol("admin")


def _fuente_del_tenant(db: Session, fuente_id: uuid.UUID, sesion: SesionUsuario) -> FuenteDatos:
    fuente = db.get(FuenteDatos, fuente_id)
    if fuente is None or fuente.tenant_id != sesion.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fuente no encontrada")
    return fuente


def _volver(mensaje: str, error: bool = False) -> RedirectResponse:
    clave = "error" if error else "aviso"
    return RedirectResponse(url=f"/dashboard/fuentes?{clave}={quote(mensaje)}", status_code=303)


@router.get("")
def lista(request: Request, sesion: SesionUsuario = Depends(solo_admin), db: Session = Depends(get_db),
          aviso: str | None = None, error: str | None = None):
    fuentes = db.execute(
        select(FuenteDatos).where(FuenteDatos.tenant_id == sesion.tenant_id).order_by(FuenteDatos.fecha_alta)
    ).scalars().all()
    settings = get_settings()
    return templates.TemplateResponse(request, "dashboard/fuentes.html", {
        "sesion": sesion, "fuentes": fuentes, "tipos": TIPOS, "tenant": db.get(Tenant, sesion.tenant_id),
        "azure_disponible": bool(settings.azure_di_endpoint and settings.azure_di_clave),
        "carpeta_disponible": settings.fuentes_carpeta_base is not None, "aviso": aviso, "error": error,
    })


# Antes que las rutas /{fuente_id}: si no, FastAPI toma "ocr-tercero" como
# un id, falla al validarlo como UUID y responde 422.
@router.post("/ocr-tercero")
async def autorizar_ocr_tercero(request: Request, sesion: SesionUsuario = Depends(solo_admin), db: Session = Depends(get_db)):
    """Autorización expresa (o retirada) del OCR por un tercero, Azure:
    subencargado del tratamiento, art. 28.2 RGPD. Queda quién y cuándo."""
    formulario = await request.form()
    tenant = db.get(Tenant, sesion.tenant_id)
    autorizar = formulario.get("autorizar") == "si"
    if autorizar and formulario.get("confirmo") != "si":
        return _volver("Marca la casilla de confirmación para autorizarlo.", error=True)
    usuario = db.get(Usuario, sesion.usuario_id)
    tenant.ocr_tercero_autorizado = autorizar
    tenant.ocr_tercero_autorizado_en = datetime.now(timezone.utc)
    tenant.ocr_tercero_autorizado_por = usuario.email if usuario else str(sesion.usuario_id)
    db.add(tenant)
    db.commit()
    return _volver("OCR con Azure autorizado." if autorizar else "OCR con Azure retirado: se usará el del servidor.")


@router.get("/nueva")
def formulario_nueva(request: Request, tipo: str, sesion: SesionUsuario = Depends(solo_admin)):
    if tipo not in TIPOS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tipo desconocido")
    clase = TIPOS[tipo]
    return templates.TemplateResponse(request, "dashboard/fuente_form.html", {
        "sesion": sesion, "clase": clase, "fuente": None,
        "valores": {c.nombre: c.defecto for c in clase.CAMPOS}, "nombre": clase.titulo, "intervalo": 15,
    })


@router.post("/nueva")
async def crear(request: Request, sesion: SesionUsuario = Depends(solo_admin), db: Session = Depends(get_db)):
    formulario = await request.form()
    try:
        fuente = nueva_fuente(
            tenant_id=sesion.tenant_id, tipo=str(formulario.get("tipo")), nombre=str(formulario.get("nombre") or ""),
            config={k: str(v) for k, v in formulario.items()}, intervalo_minutos=int(formulario.get("intervalo") or 15),
        )
    except (ConectorError, ValueError) as exc:
        return _volver(str(exc), error=True)
    db.add(fuente)
    db.commit()
    return _volver(f"Fuente «{fuente.nombre}» creada. Pulsa «Probar» para comprobar la conexión.")


@router.get("/{fuente_id}")
def formulario_editar(request: Request, fuente_id: uuid.UUID, sesion: SesionUsuario = Depends(solo_admin),
                      db: Session = Depends(get_db)):
    fuente = _fuente_del_tenant(db, fuente_id, sesion)
    return templates.TemplateResponse(request, "dashboard/fuente_form.html", {
        "sesion": sesion, "clase": TIPOS[fuente.tipo], "fuente": fuente, "valores": config_visible(fuente),
        "nombre": fuente.nombre, "intervalo": fuente.intervalo_minutos,
    })


@router.post("/{fuente_id}")
async def guardar(request: Request, fuente_id: uuid.UUID, sesion: SesionUsuario = Depends(solo_admin),
                  db: Session = Depends(get_db)):
    fuente = _fuente_del_tenant(db, fuente_id, sesion)
    formulario = await request.form()
    fuente.nombre = str(formulario.get("nombre") or fuente.nombre).strip()[:150]
    try:
        fuente.intervalo_minutos = max(5, min(int(formulario.get("intervalo") or 15), 1440))
    except ValueError:
        pass
    guardar_config(fuente, {k: str(v) for k, v in formulario.items()})
    db.add(fuente)
    db.commit()
    return _volver(f"Fuente «{fuente.nombre}» guardada.")


@router.post("/{fuente_id}/probar")
def probar(fuente_id: uuid.UUID, sesion: SesionUsuario = Depends(solo_admin), db: Session = Depends(get_db)):
    fuente = _fuente_del_tenant(db, fuente_id, sesion)
    try:
        return _volver(f"{fuente.nombre}: {conector_de(fuente).probar()}")
    except ConectorError as exc:
        return _volver(f"{fuente.nombre}: {exc}", error=True)
    except Exception as exc:
        return _volver(f"{fuente.nombre}: error inesperado ({type(exc).__name__})", error=True)


@router.post("/{fuente_id}/sincronizar")
def sincronizar(fuente_id: uuid.UUID, sesion: SesionUsuario = Depends(solo_admin), db: Session = Depends(get_db)):
    fuente = _fuente_del_tenant(db, fuente_id, sesion)
    resumen = ejecutar_fuente(db, fuente)
    return _volver(f"{fuente.nombre}: {resumen}", error=bool(fuente.ultimo_error))


@router.post("/{fuente_id}/activar")
def activar(fuente_id: uuid.UUID, sesion: SesionUsuario = Depends(solo_admin), db: Session = Depends(get_db)):
    fuente = _fuente_del_tenant(db, fuente_id, sesion)
    fuente.activo = not fuente.activo
    db.add(fuente)
    db.commit()
    return _volver(f"{fuente.nombre}: {'activada' if fuente.activo else 'pausada'}.")


@router.post("/{fuente_id}/eliminar")
def eliminar(fuente_id: uuid.UUID, sesion: SesionUsuario = Depends(solo_admin), db: Session = Depends(get_db)):
    fuente = _fuente_del_tenant(db, fuente_id, sesion)
    nombre = fuente.nombre
    db.delete(fuente)
    db.commit()
    return _volver(f"Fuente «{nombre}» eliminada (sus borradores siguen en la bandeja).")
