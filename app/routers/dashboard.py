from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.domain.models import AccesoLog, Certificado, ErpApiKey, Expedicion, ModificacionDeca, Usuario
from app.filtros import (
    PERIODOS,
    buscar_en,
    contiene,
    en_rango,
    ficha,
    paginar,
    rango_fechas,
    resumen_periodo,
    siguiente_orden,
    texto_busqueda,
)
from app.domain.schemas import CAMPOS_INDEXADOS, ExpedicionMetadata, ModificacionCreate
from app.security.auth import generar_api_key, hash_password, validar_password_nueva
from app.security.dependencies import SesionUsuario, obtener_usuario_actual, requerir_rol
from app.services.certificado_service import CertificadoInvalidoError, guardar_certificado
from app.routers.contraparte import url_contraparte
from app.routers.formulario_deca import deca_desde_valores, valores_desde_formulario
from app.services.actividad import actividad
from app.services.copia_conductor import respuesta_pdf, respuesta_qr
from app.services.expedicion_service import (
    CertificadoNoDisponibleError,
    ExpedicionYaExisteError,
    ModificacionNoPermitidaError,
    crear_expedicion_desde_datos,
    crear_expedicion_desde_pdf,
    finalizar_servicio,
    modificar_expedicion,
)
from app.services.pdf_generator import (
    MotorPdfNoDisponibleError,
    PdfDemasiadoGrandeError,
    PdfInvalidoError,
    PdfPosibleEscaneoError,
)
from app.services.retention import url_publica_activa
from app.services.signing import FirmaError
from app.plantillas import crear_plantillas

ROLES_VALIDOS = ("admin", "operador", "lectura")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = crear_plantillas()


DETALLE_PDF_PROPIO = "Panel: PDF propio"


def _correo(db: Session, sesion: SesionUsuario) -> str:
    usuario = db.get(Usuario, sesion.usuario_id)
    return usuario.email if usuario else str(sesion.usuario_id)


@router.get("")
def raiz_dashboard():
    return RedirectResponse(url="/dashboard/inicio", status_code=303)


@router.get("/inicio")
def inicio(
    request: Request,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
):
    """Resumen de la empresa: lo pendiente, lo que está en marcha y lo que
    necesita atención (certificados, borradores atascados, fuentes con error)."""
    from zoneinfo import ZoneInfo

    from app.domain.models import BorradorDeca
    from app.services.avisos import avisos_empresa
    from app.zonas import zona_predeterminada

    t = sesion.tenant_id
    ahora = datetime.now(timezone.utc)
    zona = ZoneInfo(zona_predeterminada())
    hoy = datetime.now(zona).date()
    inicio_mes = datetime.combine(hoy.replace(day=1), datetime.min.time(), tzinfo=zona)
    inicio_hoy = datetime.combine(hoy, datetime.min.time(), tzinfo=zona)
    manana = inicio_hoy + timedelta(days=1)

    def contar(modelo, *condiciones) -> int:
        return db.execute(select(func.count()).select_from(modelo).where(*condiciones)).scalar_one()

    pendientes = contar(BorradorDeca, BorradorDeca.tenant_id == t, BorradorDeca.estado == "pendiente")
    pendientes_incompletos = contar(BorradorDeca, BorradorDeca.tenant_id == t, BorradorDeca.estado == "pendiente",
                                    func.jsonb_array_length(BorradorDeca.avisos) > 0)
    en_transito = contar(Expedicion, Expedicion.tenant_id == t, Expedicion.estado == "en_transito")
    salen_hoy = contar(Expedicion, Expedicion.tenant_id == t, Expedicion.estado == "en_transito",
                       Expedicion.fecha_transporte >= inicio_hoy, Expedicion.fecha_transporte < manana)
    emitidos_mes = contar(Expedicion, Expedicion.tenant_id == t, Expedicion.fecha_emision >= inicio_mes)
    entregados_mes = contar(Expedicion, Expedicion.tenant_id == t, Expedicion.estado == "entregado",
                            Expedicion.fecha_finalizacion_servicio >= inicio_mes)

    avisos = avisos_empresa(db, t, ahora, es_admin=sesion.rol == "admin")

    ultimos_pendientes = db.execute(
        select(BorradorDeca).where(BorradorDeca.tenant_id == t, BorradorDeca.estado == "pendiente")
        .order_by(BorradorDeca.fecha_alta.desc()).limit(5)
    ).scalars().all()
    ultimas_expediciones = db.execute(
        select(Expedicion).where(Expedicion.tenant_id == t).order_by(Expedicion.fecha_emision.desc()).limit(5)
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "dashboard/inicio.html",
        {
            "sesion": sesion,
            "indicadores": [
                {"titulo": "Pendientes de revisar", "valor": pendientes, "enlace": "/dashboard/bandeja",
                 "nota": f"{pendientes_incompletos} con datos que faltan" if pendientes_incompletos else "Ninguno con datos que faltan"},
                {"titulo": "En tránsito", "valor": en_transito, "enlace": "/dashboard/expediciones?f=1&estado=en_transito",
                 "nota": f"{salen_hoy} con transporte hoy"},
                {"titulo": "Emitidos este mes", "valor": emitidos_mes, "enlace": "/dashboard/expediciones?fecha_de=emision&periodo=mes",
                 "nota": f"Desde el {inicio_mes:%d/%m/%Y}"},
                {"titulo": "Entregados este mes", "valor": entregados_mes,
                 "enlace": "/dashboard/expediciones?f=1&estado=entregado&fecha_de=finalizacion&periodo=mes",
                 "nota": "Servicio finalizado"},
            ],
            "avisos": avisos,
            "correo_activo": bool(get_settings().smtp_host),
            "ultimos_pendientes": ultimos_pendientes,
            "ultimas_expediciones": ultimas_expediciones,
        },
    )


ESTADOS_EXPEDICION = {"en_transito": "En tránsito", "entregado": "Entregados", "anulado": "Anulados"}
FECHAS_EXPEDICION = {"transporte": "Transporte", "emision": "Emisión", "finalizacion": "Entrega"}
FIRMAS = {"": "Todas", "si": "Firmadas", "no": "Sin firmar"}
ORDEN_POR_DEFECTO = "emision_desc"
ORDEN_EXPEDICIONES = {
    "emision_desc": (Expedicion.fecha_emision.desc(),),
    "emision_asc": (Expedicion.fecha_emision.asc(),),
    "transporte_desc": (Expedicion.fecha_transporte.desc(),),
    "transporte_asc": (Expedicion.fecha_transporte.asc(),),
    "referencia": (Expedicion.referencia_externa.asc(),),
    "referencia_desc": (Expedicion.referencia_externa.desc(),),
    "matricula": (Expedicion.matricula_vehiculo.asc(),),
    "matricula_desc": (Expedicion.matricula_vehiculo.desc(),),
    "estado": (Expedicion.estado.asc(),),
    "estado_desc": (Expedicion.estado.desc(),),
}


@dataclass
class FiltroExpediciones:
    """Filtros de la lista de expediciones, ya validados. Los usan la lista
    y la exportación, para que el fichero tenga exactamente lo que se ve."""

    texto: str
    matricula: str
    referencia: str
    visibles: list[str]
    firmado: str
    erp: str
    fecha_de: str
    periodo: str
    desde: str
    hasta: str
    orden: str
    pagina: int
    inicio: datetime | None
    fin: datetime | None
    error_fechas: str | None

    def condiciones(self, tenant_id, con_estado: bool = True) -> list:
        """Condiciones SQL; sin el estado sirven para contar cada estado."""
        condiciones = [Expedicion.tenant_id == tenant_id]
        busqueda = buscar_en(self.texto, [Expedicion.referencia_externa, Expedicion.matricula_vehiculo,
                                          Expedicion.matricula_remolque], Expedicion.datos_deca)
        if busqueda is not None:
            condiciones.append(busqueda)
        if self.matricula:
            condiciones.append(or_(contiene(Expedicion.matricula_vehiculo, self.matricula),
                                   contiene(Expedicion.matricula_remolque, self.matricula)))
        if self.referencia:
            condiciones.append(contiene(Expedicion.referencia_externa, self.referencia))
        if self.firmado:
            condiciones.append(Expedicion.firmado.is_(self.firmado == "si"))
        if self.erp:
            condiciones.append(Expedicion.erp_origen == self.erp)
        columna_fecha = {
            "transporte": Expedicion.fecha_transporte,
            "emision": Expedicion.fecha_emision,
            "finalizacion": Expedicion.fecha_finalizacion_servicio,
        }[self.fecha_de]
        condiciones += en_rango(columna_fecha, self.inicio, self.fin)
        if con_estado:
            condiciones.append(Expedicion.estado.in_(self.visibles))
        return condiciones

    def ordenacion(self) -> tuple:
        return (*ORDEN_EXPEDICIONES[self.orden], Expedicion.fecha_emision.desc())


def leer_filtros_expediciones(
    buscar: str | None = None,
    matricula: str | None = None,
    referencia: str | None = None,
    estado: list[str] = Query(default=[]),
    f: str | None = None,
    ocultar_entregados: bool = False,
    ocultar_anulados: bool = False,
    firmado: str | None = None,
    erp: str | None = None,
    fecha_de: str = "transporte",
    periodo: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    orden: str = ORDEN_POR_DEFECTO,
    pagina: int = 1,
) -> FiltroExpediciones:
    """`estado` son los estados que se muestran (casillas); `f=1` indica que
    vienen del formulario, así «ninguna marcada» no se confunde con «sin
    filtrar». `ocultar_*` y `?estado=x` de enlaces anteriores siguen
    funcionando."""
    periodo = periodo if periodo in PERIODOS else ""
    inicio, fin, error_fechas = rango_fechas(periodo, desde, hasta)
    visibles = [e for e in ESTADOS_EXPEDICION if e in estado] if (f or estado) else list(ESTADOS_EXPEDICION)
    if ocultar_entregados and "entregado" in visibles:
        visibles.remove("entregado")
    if ocultar_anulados and "anulado" in visibles:
        visibles.remove("anulado")
    return FiltroExpediciones(
        texto=texto_busqueda(buscar),
        matricula=texto_busqueda(matricula),
        referencia=texto_busqueda(referencia),
        visibles=visibles,
        firmado=firmado if firmado in FIRMAS else "",
        erp=(erp or "")[:30],
        fecha_de=fecha_de if fecha_de in FECHAS_EXPEDICION else "transporte",
        periodo="rango" if (desde or hasta) else periodo,
        desde=desde or "",
        hasta=hasta or "",
        orden=orden if orden in ORDEN_EXPEDICIONES else ORDEN_POR_DEFECTO,
        pagina=pagina,
        inicio=inicio,
        fin=fin,
        error_fechas=error_fechas,
    )


@router.get("/expediciones")
def listar_expediciones(
    request: Request,
    fx: FiltroExpediciones = Depends(leer_filtros_expediciones),
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    por_estado = dict(db.execute(
        select(Expedicion.estado, func.count()).where(*fx.condiciones(sesion.tenant_id, con_estado=False))
        .group_by(Expedicion.estado)
    ).all())
    total = sum(por_estado.get(e, 0) for e in fx.visibles)

    pag = paginar(total, fx.pagina)
    expediciones = db.execute(
        select(Expedicion).where(*fx.condiciones(sesion.tenant_id)).order_by(*fx.ordenacion())
        .offset(pag["offset"]).limit(pag["tamano"])
    ).scalars().all() if fx.visibles else []

    ids = [e.id for e in expediciones]
    conteos: dict = {}
    if ids:
        conteos = dict(
            db.execute(
                select(AccesoLog.expedicion_id, func.count())
                .where(AccesoLog.expedicion_id.in_(ids))
                .group_by(AccesoLog.expedicion_id)
            ).all()
        )

    filas = [
        {
            "exp": e,
            "url_activa": url_publica_activa(e, settings),
            "num_escaneos": conteos.get(e.id, 0),
        }
        for e in expediciones
    ]
    erps = db.execute(
        select(Expedicion.erp_origen).where(Expedicion.tenant_id == sesion.tenant_id)
        .distinct().order_by(Expedicion.erp_origen)
    ).scalars().all()

    resumen_fecha = resumen_periodo(fx.periodo, fx.desde, fx.hasta, fx.error_fechas)
    fichas = []
    if fx.texto:
        fichas.append(ficha(f"«{fx.texto}»", "buscar"))
    if resumen_fecha != "Cualquier fecha":
        fichas.append(ficha(f"{FECHAS_EXPEDICION[fx.fecha_de]}: {resumen_fecha}", "periodo", "desde", "hasta"))
    if fx.matricula:
        fichas.append(ficha(f"Matrícula: {fx.matricula}", "matricula"))
    if fx.referencia:
        fichas.append(ficha(f"Referencia: {fx.referencia}", "referencia"))
    if fx.firmado:
        fichas.append(ficha(FIRMAS[fx.firmado], "firmado"))
    if fx.erp:
        fichas.append(ficha(f"ERP: {fx.erp}", "erp"))

    contexto = {
        "sesion": sesion,
        "filas": filas,
        "total": total,
        "pag": pag,
        "por_estado": por_estado,
        "visibles": fx.visibles,
        "estados": ESTADOS_EXPEDICION,
        "error_fechas": fx.error_fechas,
        "erps": erps,
        "periodos": PERIODOS,
        "fechas_de": FECHAS_EXPEDICION,
        "firmas": FIRMAS,
        "resumen_fecha": resumen_fecha,
        "fichas": fichas,
        "avanzados": sum(1 for v in (fx.matricula, fx.referencia, fx.firmado, fx.erp) if v),
        "hay_filtros": bool(fichas) or len(fx.visibles) < len(ESTADOS_EXPEDICION),
        "orden_columna": lambda asc, desc: siguiente_orden(fx.orden, asc, desc, ORDEN_POR_DEFECTO),
        "url_exportar": "/dashboard/expediciones/exportar" + (f"?{request.url.query}" if request.url.query else ""),
        "puede_exportar": sesion.rol in ("admin", "operador"),
        "filtros": {
            "buscar": fx.texto, "matricula": fx.matricula, "referencia": fx.referencia,
            "firmado": fx.firmado, "erp": fx.erp, "fecha_de": fx.fecha_de,
            "periodo": fx.periodo, "desde": fx.desde, "hasta": fx.hasta, "orden": fx.orden, "pagina": pag["pagina"],
        },
    }
    return templates.TemplateResponse(request, "dashboard/expediciones_list.html", contexto)


MAX_EXPORTAR = 100000
NOMBRES_ESTADO = {"en_transito": "En tránsito", "entregado": "Entregado", "anulado": "Anulado"}

def _columnas_expediciones():
    from app.excel import Columna

    return [
        Columna("Referencia", 18), Columna("ERP", 12), Columna("Estado", 13),
        Columna("Fecha transporte", 14, "fecha"), Columna("Hora", 8, "hora"),
        Columna("Emisión", 17, "fecha_hora"), Columna("Entrega", 17, "fecha_hora"),
        Columna("Matrícula", 12), Columna("Remolque", 12),
        Columna("Cargador", 30), Columna("NIF cargador", 13),
        Columna("Transportista", 30), Columna("NIF transportista", 13),
        Columna("Destinatario", 30), Columna("NIF destinatario", 13),
        Columna("Origen", 36), Columna("Destino", 36),
        Columna("Mercancía", 30), Columna("Peso (kg)", 12, "decimal", "suma"), Columna("Otra magnitud", 16),
        Columna("Bultos", 9, "entero", "suma"), Columna("ADR n.º ONU", 11),
        Columna("Firmado", 9), Columna("URL pública", 11), Columna("Lecturas del QR", 10, "entero", "suma"),
    ]


def fila_expedicion(e: Expedicion, lecturas: int, url_activa: bool) -> list:
    """Fila de una expedición para Excel (Expediciones e Historial)."""
    d = e.datos_deca or {}

    def parte(clave, campo):
        valor = d.get(clave)
        return valor.get(campo) if isinstance(valor, dict) else None

    mercancia = d.get("mercancia") if isinstance(d.get("mercancia"), dict) else {}
    peso = mercancia.get("peso_kg")
    return [
        e.referencia_externa, e.erp_origen, NOMBRES_ESTADO.get(e.estado, e.estado),
        _solo_fecha(e.fecha_transporte), e.fecha_transporte, e.fecha_emision, e.fecha_finalizacion_servicio,
        e.matricula_vehiculo, e.matricula_remolque,
        parte("cargador", "nombre_razon_social"), parte("cargador", "nif_cif"),
        parte("transportista", "nombre_razon_social"), parte("transportista", "nif_cif"),
        parte("destinatario", "nombre_razon_social"), parte("destinatario", "nif_cif"),
        d.get("origen_direccion"), d.get("destino_direccion"),
        mercancia.get("naturaleza"), float(peso) if isinstance(peso, (int, float)) else None,
        mercancia.get("magnitud_alternativa"), mercancia.get("bultos"), mercancia.get("adr_numero_onu"),
        "Sí" if e.firmado else "No", "Activa" if url_activa else "Inactiva", lecturas,
    ]


def _solo_fecha(valor):
    from app.excel import solo_fecha

    return solo_fecha(valor)


@router.get("/expediciones/exportar")
def exportar_expediciones(
    fx: FiltroExpediciones = Depends(leer_filtros_expediciones),
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    """Libro de Excel con las expediciones que cumplen los filtros de la
    lista, en su orden: hoja «Resumen» (filtros y totales) y hoja
    «Expediciones» (tabla con totales). Contiene datos personales de las
    partes: solo administrador y operador, y nada fuera de la empresa."""
    from app.excel import LibroExcel, respuesta_xlsx

    settings = get_settings()
    condiciones = fx.condiciones(sesion.tenant_id)
    ids = select(Expedicion.id).where(*condiciones)
    lecturas = dict(db.execute(
        select(AccesoLog.expedicion_id, func.count()).where(AccesoLog.expedicion_id.in_(ids)).group_by(AccesoLog.expedicion_id)
    ).all()) if fx.visibles else {}
    por_estado = dict(db.execute(
        select(Expedicion.estado, func.count()).where(*condiciones).group_by(Expedicion.estado)
    ).all()) if fx.visibles else {}
    total = sum(por_estado.values())

    libro = LibroExcel("Expediciones DeCA")
    filtros = []
    if fx.texto:
        filtros.append(("Buscar", fx.texto))
    resumen_fecha = resumen_periodo(fx.periodo, fx.desde, fx.hasta, fx.error_fechas)
    if resumen_fecha != "Cualquier fecha":
        filtros.append((f"Fecha de {FECHAS_EXPEDICION[fx.fecha_de].lower()}", resumen_fecha))
    if len(fx.visibles) < len(ESTADOS_EXPEDICION):
        filtros.append(("Estados", ", ".join(NOMBRES_ESTADO[e] for e in fx.visibles) or "Ninguno"))
    for etiqueta, valor in (("Matrícula", fx.matricula), ("Referencia", fx.referencia), ("ERP", fx.erp)):
        if valor:
            filtros.append((etiqueta, valor))
    if fx.firmado:
        filtros.append(("Firma", FIRMAS[fx.firmado]))
    totales = [("Expediciones", total)] + [(NOMBRES_ESTADO[e], por_estado.get(e, 0)) for e in ESTADOS_EXPEDICION]
    if total > MAX_EXPORTAR:
        totales.append(("Aviso", f"Se exportan las primeras {MAX_EXPORTAR} filas; afina los filtros para el resto."))
    libro.resumen("Exportación de la lista de expediciones", filtros, [("Totales", totales)])
    filas = (
        fila_expedicion(e, lecturas.get(e.id, 0), url_publica_activa(e, settings))
        for e in db.execute(
            select(Expedicion).where(*condiciones).order_by(*fx.ordenacion()).limit(MAX_EXPORTAR)
            .execution_options(yield_per=1000)
        ).scalars()
    ) if fx.visibles else iter([])
    libro.tabla("Expediciones", _columnas_expediciones(), filas)
    return respuesta_xlsx(libro.guardar(), "expediciones")


@router.get("/expediciones/nueva")
def form_nueva_expedicion(
    request: Request,
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    error: str | None = None,
    modo: str = "pdf",
):
    return templates.TemplateResponse(
        request,
        "dashboard/expedicion_nueva.html",
        {"sesion": sesion, "error": error, "modo": modo, "valores": {}},
    )


@router.post("/expediciones/nueva-pdf")
async def crear_expedicion_nueva_pdf(
    request: Request,
    referencia_externa: str = Form(...),
    matricula_vehiculo: str = Form(...),
    matricula_remolque: str | None = Form(None),
    pais_matricula_vehiculo: str = Form("ES"),
    pais_matricula_remolque: str = Form("ES"),
    fecha_transporte: str = Form(...),
    zona_horaria: str | None = Form(None),
    finalidad_contractual: bool = Form(False),
    archivo: UploadFile = File(...),
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    # Se conservan los valores ya escritos para volver a mostrarlos si
    # algo falla — un fichero PDF no se puede "recordar" en un formulario
    # HTML por motivos de seguridad del navegador, así que ese campo
    # concreto sí hay que volver a seleccionarlo.
    valores = {
        "referencia_externa": referencia_externa,
        "matricula_vehiculo": matricula_vehiculo,
        "matricula_remolque": matricula_remolque or "",
        "pais_matricula_vehiculo": pais_matricula_vehiculo,
        "pais_matricula_remolque": pais_matricula_remolque,
        "fecha_transporte": fecha_transporte,
        "zona_horaria": zona_horaria or "",
        "finalidad_contractual": finalidad_contractual,
    }

    def _error(mensaje: str):
        return templates.TemplateResponse(
            request,
            "dashboard/expedicion_nueva.html",
            {"sesion": sesion, "error": mensaje, "modo": "pdf", "valores": valores},
            status_code=422,
        )

    nombre = (archivo.filename or "").lower()
    if archivo.content_type != "application/pdf" and not nombre.endswith(".pdf"):
        return _error("El archivo debe ser un PDF")
    pdf_bytes = await archivo.read()

    try:
        metadata = ExpedicionMetadata.model_validate(
            {
                "referencia_externa": referencia_externa,
                "matricula_vehiculo": matricula_vehiculo,
                "matricula_remolque": matricula_remolque or None,
                "pais_matricula_vehiculo": pais_matricula_vehiculo or "ES",
                "pais_matricula_remolque": pais_matricula_remolque or "ES",
                "fecha_transporte": fecha_transporte,
                "zona_horaria": zona_horaria or None,
                "finalidad_contractual": finalidad_contractual,
                "erp_origen": "generico",
            }
        )
    except ValidationError as exc:
        return _error("; ".join(e["msg"] for e in exc.errors()))

    try:
        exp = await run_in_threadpool(crear_expedicion_desde_pdf, db, tenant_id=sesion.tenant_id, metadata=metadata, pdf_original_bytes=pdf_bytes,
                                      quien=_correo(db, sesion), detalle=DETALLE_PDF_PROPIO)
    except ExpedicionYaExisteError as exc:
        return _error(str(exc))
    except CertificadoNoDisponibleError as exc:
        return _error(str(exc))
    except (PdfInvalidoError, PdfPosibleEscaneoError, PdfDemasiadoGrandeError, FirmaError) as exc:
        return _error(str(exc))
    except MotorPdfNoDisponibleError as exc:
        return _error(str(exc))

    return RedirectResponse(url=f"/dashboard/expediciones/{exp.id}", status_code=303)


@router.post("/expediciones/nueva-datos")
async def crear_expedicion_nueva_datos(
    request: Request,
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    valores = valores_desde_formulario(await request.form())

    def _error(mensaje: str):
        return templates.TemplateResponse(
            request,
            "dashboard/expedicion_nueva.html",
            {"sesion": sesion, "error": mensaje, "modo": "datos", "valores": valores},
            status_code=422,
        )

    deca, error = deca_desde_valores(valores)
    if error:
        return _error(error)

    try:
        exp = await run_in_threadpool(crear_expedicion_desde_datos, db, tenant_id=sesion.tenant_id, deca=deca,
                                      quien=_correo(db, sesion), detalle="Panel: formulario de datos")
    except (ExpedicionYaExisteError, CertificadoNoDisponibleError, PdfDemasiadoGrandeError, FirmaError, MotorPdfNoDisponibleError) as exc:
        return _error(str(exc))

    return RedirectResponse(url=f"/dashboard/expediciones/{exp.id}", status_code=303)


def _leer_filas_metadatos(nombre: str, contenido: bytes) -> list[dict]:
    nombre = nombre.lower()
    if nombre.endswith(".json"):
        datos = json.loads(contenido.decode("utf-8"))
        filas = datos if isinstance(datos, list) else [datos]
        # Un elemento que no es un objeto JSON hacía fallar la carga con un 500.
        if not all(isinstance(f, dict) for f in filas):
            raise ValueError("cada elemento del JSON debe ser un objeto con las columnas de una fila")
        return filas
    if nombre.endswith(".csv"):
        texto = contenido.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(texto)))
    raise ValueError("El fichero de metadatos debe ser .csv o .json")


@router.post("/expediciones/nueva-lote")
async def crear_expediciones_lote(
    request: Request,
    archivos: list[UploadFile] = File(...),
    metadatos: UploadFile = File(...),
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    def _error(mensaje: str):
        return templates.TemplateResponse(
            request,
            "dashboard/expedicion_nueva.html",
            {"sesion": sesion, "error": mensaje, "modo": "lote", "valores": {}},
            status_code=422,
        )

    pdfs_por_nombre: dict[str, bytes] = {}
    for archivo in archivos:
        if not (archivo.filename or "").lower().endswith(".pdf"):
            return _error(f"'{archivo.filename}' no es un PDF")
        pdfs_por_nombre[archivo.filename] = await archivo.read()

    try:
        contenido_metadatos = await metadatos.read()
        filas = _leer_filas_metadatos(metadatos.filename or "", contenido_metadatos)
    except (ValueError, json.JSONDecodeError) as exc:
        return _error(f"No se pudo leer el fichero de metadatos: {exc}")

    resultados: list[dict] = []
    for i, fila in enumerate(filas):
        nombre_pdf = (fila.get("archivo") or "").strip()
        pdf_bytes = pdfs_por_nombre.get(nombre_pdf)
        numero_fila = i + 1

        if not nombre_pdf:
            resultados.append({"fila": numero_fila, "archivo": "(sin especificar)", "exito": False, "mensaje": "La fila no indica la columna 'archivo'"})
            continue
        if pdf_bytes is None:
            resultados.append({"fila": numero_fila, "archivo": nombre_pdf, "exito": False, "mensaje": "No se ha subido ningún PDF con ese nombre exacto"})
            continue

        # Los campos vacíos se descartan para que los valores por defecto
        # del esquema (None / False) se apliquen, en vez de intentar
        # validar una cadena vacía como matrícula o booleano.
        datos_fila = {k: v for k, v in fila.items() if k != "archivo" and v not in (None, "")}

        try:
            metadata = ExpedicionMetadata.model_validate({**datos_fila, "erp_origen": "generico"})
        except ValidationError as exc:
            resultados.append({
                "fila": numero_fila, "archivo": nombre_pdf, "exito": False,
                "mensaje": "; ".join(e["msg"] for e in exc.errors()),
            })
            continue

        try:
            exp = await run_in_threadpool(crear_expedicion_desde_pdf, db, tenant_id=sesion.tenant_id, metadata=metadata, pdf_original_bytes=pdf_bytes,
                                      quien=_correo(db, sesion), detalle=f"Panel: carga por lotes ({nombre_pdf})"[:1000])
            resultados.append({
                "fila": numero_fila, "archivo": nombre_pdf, "exito": True,
                "expedicion_id": str(exp.id), "referencia": exp.referencia_externa,
            })
        except ExpedicionYaExisteError as exc:
            resultados.append({"fila": numero_fila, "archivo": nombre_pdf, "exito": False, "mensaje": str(exc)})
        except (
            CertificadoNoDisponibleError,
            PdfInvalidoError,
            PdfPosibleEscaneoError,
            PdfDemasiadoGrandeError,
            FirmaError,
            MotorPdfNoDisponibleError,
        ) as exc:
            resultados.append({"fila": numero_fila, "archivo": nombre_pdf, "exito": False, "mensaje": str(exc)})

    return templates.TemplateResponse(
        request,
        "dashboard/expedicion_lote_resultado.html",
        {
            "sesion": sesion,
            "resultados": resultados,
            "num_exito": sum(1 for r in resultados if r["exito"]),
            "num_total": len(resultados),
        },
    )


def _expedicion_del_tenant(db: Session, expedicion_id: uuid.UUID, sesion: SesionUsuario) -> Expedicion:
    exp = db.get(Expedicion, expedicion_id)
    if exp is None or exp.tenant_id != sesion.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expedición no encontrada")
    return exp


def _pagina_detalle(
    request: Request,
    sesion: SesionUsuario,
    db: Session,
    exp: Expedicion,
    *,
    error: str | None = None,
    aviso: str | None = None,
    status_code: int = 200,
):
    settings = get_settings()
    accesos = db.execute(
        select(AccesoLog).where(AccesoLog.expedicion_id == exp.id).order_by(AccesoLog.timestamp.desc())
    ).scalars().all()
    modificaciones = db.execute(
        select(ModificacionDeca).where(ModificacionDeca.expedicion_id == exp.id).order_by(ModificacionDeca.numero)
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "dashboard/expedicion_detail.html",
        {
            "sesion": sesion,
            "exp": exp,
            "accesos": accesos,
            "modificaciones": modificaciones,
            "pasos": actividad(db, exp, modificaciones),
            "campos_indexados": CAMPOS_INDEXADOS,
            "error": error,
            "aviso": aviso,
            "url_activa": url_publica_activa(exp, settings),
            "url_publica": f"{settings.app_base_url.rstrip('/')}/inspect/{exp.id}",
            "url_contraparte": url_contraparte(settings.app_base_url, exp),
        },
        status_code=status_code,
    )


@router.get("/expediciones/{expedicion_id}")
def detalle_expedicion(
    request: Request,
    expedicion_id: uuid.UUID,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
):
    return _pagina_detalle(request, sesion, db, _expedicion_del_tenant(db, expedicion_id, sesion))


@router.get("/expediciones/{expedicion_id}/pdf")
def descargar_pdf_panel(
    expedicion_id: uuid.UUID,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
):
    """Copia para el conductor (Apartado Séptimo): PDF vigente."""
    return respuesta_pdf(_expedicion_del_tenant(db, expedicion_id, sesion))


@router.get("/expediciones/{expedicion_id}/qr.png")
def descargar_qr_panel(
    expedicion_id: uuid.UUID,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
):
    """QR como fichero independiente (Apartado Séptimo)."""
    return respuesta_qr(_expedicion_del_tenant(db, expedicion_id, sesion))


@router.post("/expediciones/{expedicion_id}/modificar")
async def modificar_expedicion_panel(
    request: Request,
    expedicion_id: uuid.UUID,
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    """Modificación durante el servicio (Apartado Quinto, método 1)."""
    exp = _expedicion_del_tenant(db, expedicion_id, sesion)
    formulario = await request.form()

    cambios = []
    for i in (1, 2, 3):
        campo = (formulario.get(f"campo_{i}") or "").strip()
        if campo == "otro":
            campo = (formulario.get(f"campo_otro_{i}") or "").strip()
        anterior = (formulario.get(f"anterior_{i}") or "").strip()
        nuevo = (formulario.get(f"nuevo_{i}") or "").strip()
        if not (campo or anterior or nuevo):
            continue
        cambios.append({"campo": campo, "anterior": anterior, "nuevo": nuevo})

    try:
        modificacion = ModificacionCreate.model_validate(
            {"motivo": (formulario.get("motivo") or "").strip(), "cambios": cambios}
        )
    except ValidationError as exc:
        return _pagina_detalle(
            request, sesion, db, exp, error="; ".join(e["msg"] for e in exc.errors()), status_code=422
        )

    usuario = db.get(Usuario, sesion.usuario_id)
    try:
        await run_in_threadpool(
            modificar_expedicion,
            db, expedicion=exp, modificacion=modificacion, origen=f"panel:{usuario.email if usuario else sesion.usuario_id}",
        )
    except (ModificacionNoPermitidaError, CertificadoNoDisponibleError, PdfInvalidoError, PdfDemasiadoGrandeError, FirmaError) as exc:
        db.refresh(exp)
        return _pagina_detalle(request, sesion, db, exp, error=str(exc), status_code=422)

    db.refresh(exp)
    return _pagina_detalle(
        request,
        sesion,
        db,
        exp,
        aviso="Modificación añadida al DeCA. Entrega el PDF modificado al conductor (Apartado Quinto).",
    )


@router.post("/expediciones/{expedicion_id}/finalizar")
def finalizar_expedicion(
    expedicion_id: uuid.UUID,
    sesion: SesionUsuario = Depends(requerir_rol("admin", "operador")),
    db: Session = Depends(get_db),
):
    exp = db.get(Expedicion, expedicion_id)
    if exp is None or exp.tenant_id != sesion.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expedición no encontrada")
    try:
        finalizar_servicio(db, expedicion=exp, estado_final="entregado", quien=_correo(db, sesion))
    except ModificacionNoPermitidaError:
        pass  # ya finalizada (doble clic): se muestra tal cual está
    return RedirectResponse(url=f"/dashboard/expediciones/{expedicion_id}", status_code=303)


# --- Integraciones ERP (API keys) ---


def _pagina_integraciones(request: Request, sesion: SesionUsuario, db: Session, clave_generada: str | None = None):
    keys = db.execute(
        select(ErpApiKey).where(ErpApiKey.tenant_id == sesion.tenant_id).order_by(ErpApiKey.fecha_creacion.desc())
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "dashboard/integraciones.html",
        {"sesion": sesion, "keys": keys, "clave_generada": clave_generada},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/integraciones")
def listar_integraciones(
    request: Request,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
):
    return _pagina_integraciones(request, sesion, db)


@router.post("/integraciones")
def crear_integracion(
    request: Request,
    nombre: str = Form(...),
    erp_origen: str = Form("generico"),
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    from app.domain.schemas import OrigenErp

    # Antes un nombre de más de 150 caracteres o un ERP desconocido llegaban
    # a la base de datos y la respuesta era un error 500.
    if erp_origen not in {o.value for o in OrigenErp}:
        erp_origen = OrigenErp.GENERICO.value
    nombre = nombre.strip()[:150] or "Integración"
    clave_completa, prefijo, key_hash = generar_api_key()
    db.add(
        ErpApiKey(
            tenant_id=sesion.tenant_id,
            nombre=nombre,
            prefijo=prefijo,
            key_hash=key_hash,
            erp_origen=erp_origen,
        )
    )
    db.commit()
    # La clave completa se muestra una sola vez en esta misma respuesta.
    # Antes viajaba en la URL de una redirección y quedaba en el historial
    # del navegador y en los registros del servidor y del proxy.
    return _pagina_integraciones(request, sesion, db, clave_generada=clave_completa)


@router.post("/integraciones/{key_id}/revocar")
def revocar_integracion(
    key_id: uuid.UUID,
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    key = db.get(ErpApiKey, key_id)
    if key is None or key.tenant_id != sesion.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key no encontrada")
    key.activo = False
    db.add(key)
    db.commit()
    return RedirectResponse(url="/dashboard/integraciones", status_code=303)


# --- Certificados de firma ---


@router.get("/certificados")
def listar_certificados(
    request: Request,
    buscar: str | None = None,
    ocultar_desactivados: bool = False,
    caducidad: str | None = None,
    sesion: SesionUsuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db),
    error: str | None = None,
):
    ahora = datetime.now(timezone.utc)
    texto = texto_busqueda(buscar)
    condiciones = [Certificado.tenant_id == sesion.tenant_id]
    busqueda = buscar_en(texto, [Certificado.nombre, Certificado.titular_cn])
    if busqueda is not None:
        condiciones.append(busqueda)
    if ocultar_desactivados:
        condiciones.append(Certificado.activo.is_(True))
    if caducidad == "vigentes":
        condiciones.append(or_(Certificado.fecha_caducidad.is_(None), Certificado.fecha_caducidad >= ahora))
    elif caducidad == "caducados":
        condiciones.append(Certificado.fecha_caducidad < ahora)
    elif caducidad == "30d":
        condiciones += [Certificado.fecha_caducidad >= ahora, Certificado.fecha_caducidad < ahora + timedelta(days=30)]
    else:
        caducidad = ""
    certs = db.execute(
        select(Certificado).where(*condiciones).order_by(Certificado.fecha_subida.desc())
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "dashboard/certificados.html",
        {
            "sesion": sesion, "certs": certs, "error": error, "ahora": ahora,
            "filtros": {"buscar": texto, "ocultar_desactivados": ocultar_desactivados, "caducidad": caducidad},
        },
    )


@router.post("/certificados")
async def subir_certificado(
    nombre: str = Form(...),
    password: str = Form(...),
    archivo: UploadFile = File(...),
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    contenido = await archivo.read()
    if len(contenido) > 1 * 1024 * 1024:
        from urllib.parse import quote

        return RedirectResponse(
            url=f"/dashboard/certificados?error={quote('El fichero supera el tamaño máximo permitido (1 MB) para un certificado .p12/.pfx')}",
            status_code=303,
        )
    try:
        metadata = guardar_certificado(
            tenant_id=sesion.tenant_id, nombre=nombre.strip()[:150] or "Certificado", archivo_bytes=contenido, password=password
        )
    except CertificadoInvalidoError as exc:
        from urllib.parse import quote

        return RedirectResponse(url=f"/dashboard/certificados?error={quote(str(exc))}", status_code=303)

    db.add(
        Certificado(
            id=metadata["id"],
            tenant_id=sesion.tenant_id,
            nombre=metadata["nombre"],
            ruta_archivo=metadata["ruta_archivo"],
            password_cifrado=metadata["password_cifrado"],
            titular_cn=metadata["titular_cn"],
            fecha_caducidad=metadata["fecha_caducidad"],
        )
    )
    db.commit()
    return RedirectResponse(url="/dashboard/certificados", status_code=303)


@router.post("/certificados/{cert_id}/desactivar")
def desactivar_certificado(
    cert_id: uuid.UUID,
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    cert = db.get(Certificado, cert_id)
    if cert is None or cert.tenant_id != sesion.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificado no encontrado")
    cert.activo = False
    db.add(cert)
    db.commit()
    return RedirectResponse(url="/dashboard/certificados", status_code=303)


# --- Usuarios del tenant ---


@router.get("/usuarios")
def listar_usuarios(
    request: Request,
    buscar: str | None = None,
    rol: str | None = None,
    ocultar_desactivados: bool = False,
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
    error: str | None = None,
):
    texto = texto_busqueda(buscar)
    condiciones = [Usuario.tenant_id == sesion.tenant_id]
    if texto:
        condiciones.append(contiene(Usuario.email, texto))
    if rol in ROLES_VALIDOS:
        condiciones.append(Usuario.rol == rol)
    else:
        rol = ""
    if ocultar_desactivados:
        condiciones.append(Usuario.activo.is_(True))
    usuarios = db.execute(
        select(Usuario).where(*condiciones).order_by(Usuario.fecha_alta.desc())
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "dashboard/usuarios.html",
        {
            "sesion": sesion, "usuarios": usuarios, "error": error, "roles_validos": ROLES_VALIDOS,
            "filtros": {"buscar": texto, "rol": rol, "ocultar_desactivados": ocultar_desactivados},
        },
    )


@router.post("/usuarios")
def crear_usuario(
    email: str = Form(...),
    password: str = Form(...),
    rol: str = Form("operador"),
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote

    if rol not in ROLES_VALIDOS:
        return RedirectResponse(url=f"/dashboard/usuarios?error={quote('Rol no válido')}", status_code=303)

    email_normalizado = email.strip().lower()
    existente = db.execute(
        select(Usuario).where(Usuario.tenant_id == sesion.tenant_id, Usuario.email == email_normalizado)
    ).scalar_one_or_none()
    if existente is not None:
        return RedirectResponse(
            url=f"/dashboard/usuarios?error={quote('Ya existe un usuario con ese correo en esta empresa')}",
            status_code=303,
        )

    if "@" not in email_normalizado or len(email_normalizado) > 320:
        return RedirectResponse(url=f"/dashboard/usuarios?error={quote('Correo no válido')}", status_code=303)
    try:
        password_hash = hash_password(validar_password_nueva(password))
    except ValueError as exc:
        return RedirectResponse(url=f"/dashboard/usuarios?error={quote(str(exc))}", status_code=303)

    db.add(
        Usuario(
            tenant_id=sesion.tenant_id,
            email=email_normalizado,
            password_hash=password_hash,
            rol=rol,
        )
    )
    db.commit()
    return RedirectResponse(url="/dashboard/usuarios", status_code=303)


@router.post("/usuarios/{usuario_id}/desactivar")
def desactivar_usuario(
    usuario_id: uuid.UUID,
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote

    if usuario_id == sesion.usuario_id:
        return RedirectResponse(
            url=f"/dashboard/usuarios?error={quote('No puedes desactivar tu propio usuario')}", status_code=303
        )

    usuario = db.get(Usuario, usuario_id)
    if usuario is None or usuario.tenant_id != sesion.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")

    usuario.activo = False
    db.add(usuario)
    db.commit()
    return RedirectResponse(url="/dashboard/usuarios", status_code=303)


@router.post("/usuarios/{usuario_id}/activar")
def activar_usuario(
    usuario_id: uuid.UUID,
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    usuario = db.get(Usuario, usuario_id)
    if usuario is None or usuario.tenant_id != sesion.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    usuario.activo = True
    db.add(usuario)
    db.commit()
    return RedirectResponse(url="/dashboard/usuarios", status_code=303)


@router.post("/usuarios/{usuario_id}/rol")
def cambiar_rol_usuario(
    usuario_id: uuid.UUID,
    rol: str = Form(...),
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote

    if rol not in ROLES_VALIDOS:
        return RedirectResponse(url=f"/dashboard/usuarios?error={quote('Rol no válido')}", status_code=303)

    if usuario_id == sesion.usuario_id:
        return RedirectResponse(
            url=f"/dashboard/usuarios?error={quote('No puedes cambiar el rol de tu propio usuario')}",
            status_code=303,
        )

    usuario = db.get(Usuario, usuario_id)
    if usuario is None or usuario.tenant_id != sesion.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")

    usuario.rol = rol
    db.add(usuario)
    db.commit()
    return RedirectResponse(url="/dashboard/usuarios", status_code=303)
