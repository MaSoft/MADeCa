"""Bandeja de revisión: API para conectores y pantallas del panel.

API (cabecera X-API-Key):
- POST /api/v1/borradores: el conector local manda solo los DATOS ya
  extraídos en el equipo del cliente (el documento no sale de su red).
- POST /api/v1/borradores/documento: se sube el documento y MADeCa
  extrae los datos en su servidor (o en Azure si la empresa lo autorizó).

Panel: /dashboard/bandeja (lista, subida, revisión, emisión, descarte).
"""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.domain.models import BorradorDeca, Expedicion, FuenteDatos, Tenant, Usuario
from app.extraccion.posiciones import imagen_pagina, marcas
from app.revision import CAMPOS, errores_por_campo, faltan
from app.filtros import (
    PERIODOS,
    buscar_en,
    en_rango,
    ficha,
    paginar,
    rango_fechas,
    resumen_periodo,
    siguiente_orden,
    texto_busqueda,
)
from app.extraccion.texto import EXTENSIONES_SOPORTADAS
from app.routers.formulario_deca import deca_desde_valores, valores_desde_datos, valores_desde_formulario
from app.security.dependencies import SesionUsuario, obtener_tenant_por_api_key, obtener_usuario_actual, requerir_rol
from app.services.copia_conductor import disposicion
from app.services.borradores import (
    BorradorNoPendienteError,
    crear_borrador,
    descartar_borrador,
    emitir_borrador,
    procesar_documento,
)
from app.services.expedicion_service import CertificadoNoDisponibleError, ExpedicionYaExisteError
from app.services.pdf_generator import MotorPdfNoDisponibleError, PdfDemasiadoGrandeError
from app.services.signing import FirmaError
from app.services.storage import get_storage
from app.plantillas import crear_plantillas

router = APIRouter(tags=["bandeja"])
templates = crear_plantillas()

MAX_DATOS_JSON = 64 * 1024


class BorradorEntrada(BaseModel):
    referencia_fuente: str = Field(min_length=1, max_length=500, description="Id del documento en su origen (evita duplicados)")
    origen: str = Field(default="conector_local", min_length=1, max_length=60)
    nombre_documento: str | None = Field(default=None, max_length=300)
    datos: dict[str, Any] = Field(default_factory=dict, description="Misma forma que el JSON de /expediciones/datos")
    confianza: dict[str, str] = Field(default_factory=dict)
    avisos: list[str] = Field(default_factory=list, max_length=50)


class BorradorSalida(BaseModel):
    id: str
    estado: str
    creado: bool


def _nombre_seguro(nombre: str | None) -> str:
    base = PurePath(nombre or "documento").name
    return "".join(c if c.isalnum() or c in "-_. " else "_" for c in base)[:200] or "documento"


# ------------------------------- API -------------------------------


@router.post("/api/v1/borradores", response_model=BorradorSalida, status_code=status.HTTP_201_CREATED)
def api_crear_borrador(
    entrada: BorradorEntrada,
    tenant_id: uuid.UUID = Depends(obtener_tenant_por_api_key),
    db: Session = Depends(get_db),
) -> BorradorSalida:
    import json

    if len(json.dumps(entrada.datos)) > MAX_DATOS_JSON:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Datos demasiado grandes")
    borrador, creado = crear_borrador(
        db,
        tenant_id=tenant_id,
        origen=f"api:{entrada.origen}",
        referencia_fuente=entrada.referencia_fuente,
        nombre_documento=entrada.nombre_documento,
        datos=entrada.datos,
        confianza=entrada.confianza,
        avisos=entrada.avisos,
    )
    return BorradorSalida(id=str(borrador.id), estado=borrador.estado, creado=creado)


@router.post("/api/v1/borradores/documento", response_model=BorradorSalida, status_code=status.HTTP_201_CREATED)
async def api_subir_documento(
    archivo: UploadFile = File(...),
    referencia_fuente: str | None = Form(None),
    tenant_id: uuid.UUID = Depends(obtener_tenant_por_api_key),
    db: Session = Depends(get_db),
) -> BorradorSalida:
    nombre = _nombre_seguro(archivo.filename)
    if PurePath(nombre).suffix.lower() not in EXTENSIONES_SOPORTADAS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Tipo de fichero no soportado: {nombre}")
    contenido = await archivo.read()
    tenant = db.get(Tenant, tenant_id)
    # El OCR puede tardar: en un hilo, para no congelar el servidor.
    borrador, creado = await run_in_threadpool(
        procesar_documento, db, tenant=tenant, origen="api:documento",
        referencia_fuente=referencia_fuente or f"{nombre}:{uuid.uuid4()}", nombre=nombre, contenido=contenido,
    )
    return BorradorSalida(id=str(borrador.id), estado=borrador.estado, creado=creado)


# ------------------------------ Panel ------------------------------


def _borrador_del_tenant(db: Session, borrador_id: uuid.UUID, sesion: SesionUsuario) -> BorradorDeca:
    borrador = db.get(BorradorDeca, borrador_id)
    if borrador is None or borrador.tenant_id != sesion.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Borrador no encontrado")
    return borrador


def _email(db: Session, sesion: SesionUsuario) -> str:
    usuario = db.get(Usuario, sesion.usuario_id)
    return usuario.email if usuario else str(sesion.usuario_id)


ESTADOS_BANDEJA = {"pendiente": "Pendientes", "emitido": "Emitidos", "descartado": "Descartados"}
FECHAS_BANDEJA = {"alta": "Recepción", "resolucion": "Resolución"}
ORDEN_BANDEJA_DEFECTO = "recibido_desc"
ORDEN_BANDEJA = {
    "recibido_desc": (BorradorDeca.fecha_alta.desc(),),
    "recibido": (BorradorDeca.fecha_alta.asc(),),
    "documento": (func.coalesce(BorradorDeca.nombre_documento, BorradorDeca.referencia_fuente).asc(),),
    "documento_desc": (func.coalesce(BorradorDeca.nombre_documento, BorradorDeca.referencia_fuente).desc(),),
    "origen": (BorradorDeca.origen.asc(),),
    "origen_desc": (BorradorDeca.origen.desc(),),
    "avisos_desc": (func.jsonb_array_length(BorradorDeca.avisos).desc(),),
    "avisos": (func.jsonb_array_length(BorradorDeca.avisos).asc(),),
}


@router.get("/dashboard/bandeja")
def bandeja(
    request: Request,
    estado: str = "pendiente",
    buscar: str | None = None,
    origen: str | None = None,
    con_avisos: bool = False,
    ocultar_entregados: bool = False,
    fecha_de: str = "alta",
    periodo: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    orden: str = ORDEN_BANDEJA_DEFECTO,
    pagina: int = 1,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
    aviso: str | None = None,
):
    if estado not in ESTADOS_BANDEJA:
        estado = "pendiente"
    texto = texto_busqueda(buscar)
    # Los pendientes no tienen fecha de resolución.
    fecha_de = fecha_de if fecha_de in FECHAS_BANDEJA and estado != "pendiente" else "alta"
    orden = orden if orden in ORDEN_BANDEJA else ORDEN_BANDEJA_DEFECTO
    periodo = periodo if periodo in PERIODOS else ""
    ocultar_entregados = ocultar_entregados and estado == "emitido"
    inicio, fin, error_fechas = rango_fechas(periodo, desde, hasta)

    # Filtros comunes a las tres pestañas: con ellos se cuenta cada una.
    condiciones = [BorradorDeca.tenant_id == sesion.tenant_id]
    busqueda = buscar_en(texto, [BorradorDeca.nombre_documento, BorradorDeca.referencia_fuente,
                                 BorradorDeca.resuelto_por], BorradorDeca.datos)
    if busqueda is not None:
        condiciones.append(busqueda)
    if origen:
        condiciones.append(BorradorDeca.origen == origen)
    if con_avisos:
        condiciones.append(func.jsonb_array_length(BorradorDeca.avisos) > 0)
    columna_fecha = BorradorDeca.fecha_resolucion if fecha_de == "resolucion" else BorradorDeca.fecha_alta
    condiciones += en_rango(columna_fecha, inicio, fin)
    por_estado = dict(db.execute(
        select(BorradorDeca.estado, func.count()).where(*condiciones).group_by(BorradorDeca.estado)
    ).all())

    condiciones.append(BorradorDeca.estado == estado)
    if ocultar_entregados:
        # Los emitidos cuya expedición ya se entregó.
        entregadas = select(Expedicion.id).where(
            Expedicion.tenant_id == sesion.tenant_id, Expedicion.estado == "entregado"
        )
        condiciones.append(or_(BorradorDeca.expedicion_id.is_(None), BorradorDeca.expedicion_id.not_in(entregadas)))
    total = (
        db.execute(select(func.count()).select_from(BorradorDeca).where(*condiciones)).scalar_one()
        if ocultar_entregados else por_estado.get(estado, 0)
    )
    pag = paginar(total, pagina)
    borradores = db.execute(
        select(BorradorDeca).where(*condiciones)
        .order_by(*ORDEN_BANDEJA[orden], BorradorDeca.fecha_alta.desc()).offset(pag["offset"]).limit(pag["tamano"])
    ).scalars().all()
    pendientes = db.execute(
        select(func.count()).select_from(BorradorDeca).where(
            BorradorDeca.tenant_id == sesion.tenant_id, BorradorDeca.estado == "pendiente"
        )
    ).scalar_one()

    # Estado de la expedición de cada borrador emitido, para verlo en la tabla.
    ids_exp = [b.expedicion_id for b in borradores if b.expedicion_id]
    estados_exp = dict(db.execute(
        select(Expedicion.id, Expedicion.estado).where(Expedicion.id.in_(ids_exp))
    ).all()) if ids_exp else {}

    # Orígenes del desplegable, con el nombre de la fuente de datos si lo es.
    nombres_fuente = {
        f"fuente:{f.id}": f.nombre
        for f in db.execute(select(FuenteDatos).where(FuenteDatos.tenant_id == sesion.tenant_id)).scalars().all()
    }
    origenes = {
        o: nombre_origen(o, nombres_fuente)
        for o in db.execute(
            select(BorradorDeca.origen).where(BorradorDeca.tenant_id == sesion.tenant_id)
            .distinct().order_by(BorradorDeca.origen)
        ).scalars().all()
    }

    resumen_fecha = resumen_periodo(periodo, desde, hasta, error_fechas)
    fichas = []
    if texto:
        fichas.append(ficha(f"«{texto}»", "buscar"))
    if origen:
        fichas.append(ficha(f"Origen: {origenes.get(origen, origen)}", "origen"))
    if resumen_fecha != "Cualquier fecha":
        fichas.append(ficha(f"{FECHAS_BANDEJA[fecha_de]}: {resumen_fecha}", "periodo", "desde", "hasta"))
    if con_avisos:
        fichas.append(ficha("Con datos que faltan", "con_avisos"))
    if ocultar_entregados:
        fichas.append(ficha("Sin entregados", "ocultar_entregados"))

    tenant = db.get(Tenant, sesion.tenant_id)
    return templates.TemplateResponse(
        request,
        "dashboard/bandeja.html",
        {
            "sesion": sesion,
            "borradores": borradores,
            "estado": estado,
            "estados": ESTADOS_BANDEJA,
            "por_estado": por_estado,
            "pendientes": pendientes,
            "total": total,
            "pag": pag,
            "estados_exp": estados_exp,
            "origenes": origenes,
            "error_fechas": error_fechas,
            "periodos": PERIODOS,
            "fechas_de": FECHAS_BANDEJA,
            "resumen_fecha": resumen_fecha,
            "fichas": fichas,
            "hay_filtros": bool(fichas),
            "orden_columna": lambda primero, segundo: siguiente_orden(orden, primero, segundo, ORDEN_BANDEJA_DEFECTO),
            "filtros": {
                "buscar": texto, "origen": origen or "", "con_avisos": con_avisos,
                "ocultar_entregados": ocultar_entregados, "fecha_de": fecha_de,
                "periodo": "rango" if (desde or hasta) else periodo, "desde": desde or "", "hasta": hasta or "",
                "orden": orden, "pagina": pag["pagina"],
            },
            "aviso": aviso,
            "ocr_tercero": bool(tenant and tenant.ocr_tercero_autorizado and get_settings().azure_di_endpoint),
        },
    )


def nombre_origen(origen: str, nombres_fuente: dict[str, str]) -> str:
    """Texto legible del origen de un borrador."""
    if origen in nombres_fuente:
        return f"Fuente: {nombres_fuente[origen]}"
    if origen == "panel":
        return "Subido en el panel"
    if origen == "api:documento":
        return "API (documento)"
    if origen.startswith("api:"):
        return f"API ({origen[4:]})"
    return origen


MAX_LOTE = 50


@router.post("/dashboard/bandeja/lote")
async def bandeja_lote(
    request: Request,
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    """Acciones sobre varios pendientes a la vez.

    - descartar: los pasa a «descartado» y borra sus documentos.
    - emitir: solo los completos y válidos, y solo si la
      persona confirma que ha comprobado sus datos con los documentos (el
      cargador responde de su exactitud, art. 7.2 Orden FOM/2861/2012; en
      MADeCa nada se emite sin revisión humana). Los demás quedan
      pendientes para revisarlos uno a uno.
    """
    from urllib.parse import quote

    form = await request.form()
    accion = form.get("accion")
    ids = []
    for valor in form.getlist("ids")[:MAX_LOTE]:
        try:
            ids.append(uuid.UUID(str(valor)))
        except ValueError:
            continue
    if accion not in ("emitir", "descartar") or not ids:
        return RedirectResponse(url="/dashboard/bandeja", status_code=303)
    if accion == "emitir" and form.get("confirmo") != "1":
        return RedirectResponse(url="/dashboard/bandeja?aviso=" + quote("Marca la confirmación de que has comprobado los datos."), status_code=303)

    usuario = _email(db, sesion)
    hechos, pendientes = [], []
    for borrador_id in ids:
        borrador = db.get(BorradorDeca, borrador_id)
        if borrador is None or borrador.tenant_id != sesion.tenant_id or borrador.estado != "pendiente":
            continue
        nombre = borrador.nombre_documento or borrador.referencia_fuente
        if accion == "descartar":
            descartar_borrador(db, borrador=borrador, usuario=usuario)
            hechos.append(nombre)
            continue
        valores = valores_desde_datos(borrador.datos or {})
        deca, error = deca_desde_valores(valores)
        if error:
            pendientes.append(nombre)
            continue
        try:
            await run_in_threadpool(emitir_borrador, db, borrador=borrador, deca=deca, usuario=usuario)
            hechos.append(nombre)
        except (BorradorNoPendienteError, ExpedicionYaExisteError, CertificadoNoDisponibleError,
                PdfDemasiadoGrandeError, FirmaError, MotorPdfNoDisponibleError):
            db.rollback()
            pendientes.append(nombre)

    if accion == "descartar":
        mensaje = f"{len(hechos)} {'borrador descartado' if len(hechos) == 1 else 'borradores descartados'}."
    else:
        mensaje = f"{len(hechos)} {'DeCA emitido' if len(hechos) == 1 else 'DeCA emitidos'}."
        if pendientes:
            mensaje += (f" {len(pendientes)} siguen pendientes porque les faltan datos o tienen datos no válidos: "
                        "revísalos uno a uno.")
    return RedirectResponse(url="/dashboard/bandeja?aviso=" + quote(mensaje), status_code=303)


@router.post("/dashboard/bandeja/subir")
async def bandeja_subir(
    request: Request,
    archivos: list[UploadFile] = File(...),
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, sesion.tenant_id)
    creados, rechazados = 0, []
    for archivo in archivos:
        nombre = _nombre_seguro(archivo.filename)
        if PurePath(nombre).suffix.lower() not in EXTENSIONES_SOPORTADAS:
            rechazados.append(nombre)
            continue
        contenido = await archivo.read()
        _b, creado = await run_in_threadpool(
            procesar_documento, db, tenant=tenant, origen="panel", referencia_fuente=f"{nombre}:{uuid.uuid4()}",
            nombre=nombre, contenido=contenido,
        )
        creados += int(creado)
    from urllib.parse import quote

    mensaje = f"{creados} documento(s) en la bandeja."
    if rechazados:
        mensaje += f" No admitidos: {', '.join(rechazados)}."
    return RedirectResponse(url=f"/dashboard/bandeja?aviso={quote(mensaje)}", status_code=303)


def _siguiente_pendiente(db: Session, tenant_id, excluir) -> BorradorDeca | None:
    """El siguiente pendiente en el orden de la bandeja (más reciente primero)."""
    return db.execute(
        select(BorradorDeca)
        .where(BorradorDeca.tenant_id == tenant_id, BorradorDeca.estado == "pendiente", BorradorDeca.id != excluir)
        .order_by(BorradorDeca.fecha_alta.desc())
        .limit(1)
    ).scalars().first()


def _expedicion_misma_referencia(db: Session, tenant_id, referencia: str | None) -> Expedicion | None:
    """Posible duplicado: otra expedición de la empresa con esa referencia."""
    referencia = (referencia or "").strip()
    if not referencia:
        return None
    return db.execute(
        select(Expedicion)
        .where(Expedicion.tenant_id == tenant_id, Expedicion.referencia_externa == referencia)
        .order_by(Expedicion.fecha_emision.desc())
        .limit(1)
    ).scalars().first()


def _pagina_revision(request, sesion, db, borrador, *, valores=None, error=None, status_code=200, aviso=None):
    enviado = valores is not None
    valores = valores if enviado else valores_desde_datos(borrador.datos or {})
    pendiente = borrador.estado == "pendiente"
    quedan = db.execute(
        select(func.count()).select_from(BorradorDeca).where(
            BorradorDeca.tenant_id == sesion.tenant_id, BorradorDeca.estado == "pendiente", BorradorDeca.id != borrador.id
        )
    ).scalar_one() if pendiente else 0
    # «No encontrado: …» ya se señala en cada campo; el resto de avisos
    # (documento ilegible, OCR…) se muestran tal cual.
    otros_avisos = [a for a in (borrador.avisos or []) if not str(a).startswith("No encontrado:")]
    return templates.TemplateResponse(
        request,
        "dashboard/bandeja_revision.html",
        {
            "sesion": sesion,
            "b": borrador,
            "valores": valores,
            "error": error,
            "aviso": aviso,
            "faltan": faltan(valores) if pendiente else [],
            "errores_campos": errores_por_campo(valores) if enviado else {},
            "otros_avisos": otros_avisos,
            # Dónde está cada dato leído en el documento original.
            "paginas_doc": len((borrador.posiciones or {}).get("paginas") or []) if borrador.ruta_documento and pendiente else 0,
            "marcas": marcas(borrador.posiciones, borrador.datos or {},
                             {c: d["ruta"] for c, d in CAMPOS.items() if d.get("ruta")}) if borrador.ruta_documento and pendiente else {},
            "duplicado": _expedicion_misma_referencia(db, sesion.tenant_id, valores.get("referencia_externa")) if pendiente else None,
            "quedan": quedan,
            "boton_siguiente": pendiente and quedan > 0,
            "accion_datos": f"/dashboard/bandeja/{borrador.id}/emitir",
            "texto_boton_datos": "Emitir el DeCA",
        },
        status_code=status_code,
    )


@router.get("/dashboard/bandeja/{borrador_id}")
def bandeja_revisar(
    request: Request,
    borrador_id: uuid.UUID,
    aviso: str | None = None,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
):
    return _pagina_revision(request, sesion, db, _borrador_del_tenant(db, borrador_id, sesion), aviso=aviso)


@router.get("/dashboard/bandeja/{borrador_id}/documento")
def bandeja_documento(
    borrador_id: uuid.UUID,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
):
    borrador = _borrador_del_tenant(db, borrador_id, sesion)
    if not borrador.ruta_documento:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El documento original ya no se conserva")
    nombre = _nombre_seguro(borrador.nombre_documento)
    tipo = mimetypes.guess_type(nombre)[0] or "application/octet-stream"
    return Response(
        content=get_storage().leer(borrador.ruta_documento),
        media_type=tipo,
        headers={"Content-Disposition": disposicion("inline", nombre), "Cache-Control": "no-store",
                 "X-Frame-Options": "SAMEORIGIN",
                 "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"},
    )


@router.get("/dashboard/bandeja/{borrador_id}/pagina/{numero}")
async def bandeja_pagina(
    borrador_id: uuid.UUID,
    numero: int,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
):
    """Página del documento original como imagen, para señalar encima de
    dónde se leyó cada dato. Solo mientras el borrador está pendiente (el
    original se borra al emitir o descartar)."""
    borrador = _borrador_del_tenant(db, borrador_id, sesion)
    if not borrador.ruta_documento or not borrador.posiciones or not 0 <= numero < len(borrador.posiciones.get("paginas") or []):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Página no disponible")
    contenido = get_storage().leer(borrador.ruta_documento)
    png = await run_in_threadpool(imagen_pagina, contenido, borrador.nombre_documento or "", borrador.posiciones.get("origen"), numero)
    if png is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Página no disponible")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"})


@router.post("/dashboard/bandeja/{borrador_id}/emitir")
async def bandeja_emitir(
    request: Request,
    borrador_id: uuid.UUID,
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    borrador = _borrador_del_tenant(db, borrador_id, sesion)
    form = await request.form()
    valores = valores_desde_formulario(form)
    deca, error = deca_desde_valores(valores)
    if error:
        return _pagina_revision(request, sesion, db, borrador, valores=valores, error=error, status_code=422)
    try:
        usuario = _email(db, sesion)
        exp = await run_in_threadpool(emitir_borrador, db, borrador=borrador, deca=deca, usuario=usuario)
    except (BorradorNoPendienteError, ExpedicionYaExisteError, CertificadoNoDisponibleError,
            PdfDemasiadoGrandeError, FirmaError, MotorPdfNoDisponibleError) as exc:
        db.rollback()
        return _pagina_revision(request, sesion, db, borrador, valores=valores, error=str(exc), status_code=422)
    if form.get("despues") == "siguiente":
        return _ir_al_siguiente(db, sesion, borrador.id, f"DeCA {exp.referencia_externa} emitido.")
    return RedirectResponse(url=f"/dashboard/expediciones/{exp.id}", status_code=303)


def _ir_al_siguiente(db: Session, sesion: SesionUsuario, actual, mensaje: str) -> RedirectResponse:
    from urllib.parse import quote

    siguiente = _siguiente_pendiente(db, sesion.tenant_id, actual)
    if siguiente is None:
        return RedirectResponse(url=f"/dashboard/bandeja?aviso={quote(mensaje + ' No quedan pendientes.')}", status_code=303)
    return RedirectResponse(url=f"/dashboard/bandeja/{siguiente.id}?aviso={quote(mensaje)}", status_code=303)


@router.post("/dashboard/bandeja/{borrador_id}/descartar")
def bandeja_descartar(
    borrador_id: uuid.UUID,
    despues: str | None = Form(None),
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    borrador = _borrador_del_tenant(db, borrador_id, sesion)
    try:
        descartar_borrador(db, borrador=borrador, usuario=_email(db, sesion))
    except BorradorNoPendienteError:
        pass
    if despues == "siguiente":
        return _ir_al_siguiente(db, sesion, borrador.id, "Borrador descartado.")
    return RedirectResponse(url="/dashboard/bandeja", status_code=303)
