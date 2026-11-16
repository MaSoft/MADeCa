"""Historial: estadísticas detalladas de todo lo gestionado en un periodo,
con cada cifra como filtro, registros paginados y exportación a Excel.
Solo administradores (app/estadisticas.py hace los cálculos)."""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.domain.models import AccesoLog, BorradorDeca, EventoExpedicion, Expedicion, FuenteDatos
from app.estadisticas import (
    DIMENSIONES,
    ESTADOS_BANDEJA,
    FECHAS,
    NOMBRES_ESTADO,
    PERIODOS,
    POR_CLAVE,
    PROVINCIAS,
    SIN_DATO,
    TEXTO_INDICADOR,
    VACIO,
    Consulta,
    calcular_periodo,
    desglose,
    desglose_bandeja,
    evolucion,
    leer_filtros,
    totales,
    totales_bandeja,
)
from app.filtros import paginar
from app.plantillas import crear_plantillas
from app.security.dependencies import SesionUsuario, requerir_rol
from app.services.retention import url_publica_activa
from app.zonas import zona_predeterminada

router = APIRouter(prefix="/dashboard/historial", tags=["historial"])
templates = crear_plantillas()

TAMANO = 50
MAX_FILAS_EXCEL = 100000
PARAMETROS = ("periodo", "ref", "desde", "hasta", "fecha", "tramo", "pestana", "pagina",
              *(d.clave for d in DIMENSIONES), "estado", "firmado", "adr", "modificada", "lecturas",
              "b_estado", "b_origen", "b_resuelto")


def _leer(request: Request):
    p = {k: request.query_params.get(k, "") for k in PARAMETROS if request.query_params.get(k)}
    periodo = calcular_periodo(p.get("periodo", "mes"), p.get("ref"), p.get("desde"), p.get("hasta"))
    fecha = p.get("fecha") if p.get("fecha") in FECHAS else "transporte"
    filtros, tramo = leer_filtros(p)
    if tramo and not (periodo.inicio <= tramo[0] < tramo[1] <= periodo.fin):
        tramo = None
    return p, Consulta(periodo, fecha, filtros, tramo)


def _nombres_origen(db: Session, tenant_id):
    """Función que da el nombre legible de un origen de la bandeja."""
    from app.routers.bandeja import nombre_origen

    fuentes = {f"fuente:{f.id}": f.nombre for f in db.execute(select(FuenteDatos).where(FuenteDatos.tenant_id == tenant_id)).scalars().all()}
    return lambda origen: nombre_origen(origen, fuentes)


def _texto_filtro(clave: str, valor: str, nombres: dict) -> str:
    if clave in POR_CLAVE:
        d = POR_CLAVE[clave]
        if valor == VACIO:
            return f"{d.columna}: {SIN_DATO.lower()}"
        if clave in ("prov_destino", "prov_origen"):
            return f"{'Provincia de destino' if clave == 'prov_destino' else 'Provincia de origen'}: {PROVINCIAS.get(valor, valor)}"
        return f"{d.columna}: {nombres.get((clave, valor), valor)}"
    if clave == "estado":
        return NOMBRES_ESTADO.get(valor, valor)
    if clave == "b_estado":
        return f"Bandeja: {ESTADOS_BANDEJA.get(valor, valor).lower()}"
    if clave == "b_origen":
        return f"Bandeja, origen: {nombres.get(('b_origen', valor), valor)}"
    if clave == "b_resuelto":
        return f"Bandeja, resuelto por: {'nadie' if valor == VACIO else valor}"
    return TEXTO_INDICADOR.get((clave, valor), f"{clave}: {valor}")


@router.get("")
def historial(
    request: Request,
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    p, c = _leer(request)
    t = sesion.tenant_id

    def enlace(**cambios) -> str:
        nuevos = {k: v for k, v in p.items() if k != "pagina"}
        for k, v in cambios.items():
            if v is None:
                nuevos.pop(k, None)
            else:
                nuevos[k] = v
        return "/dashboard/historial" + ("?" + urlencode(nuevos) if nuevos else "")

    tot = totales(db, t, c)
    tot_b = totales_bandeja(db, t, c)
    unidad, evo = evolucion(db, t, c)
    desgloses = []
    nombres_filtro = {}
    for d in DIMENSIONES:
        datos = desglose(db, t, c, d)
        total_dim = sum(f["n"] for f in datos["filas"]) or 1
        for f in datos["filas"]:
            f["porcentaje"] = f["n"] / total_dim
            nombres_filtro[(d.clave, f["valor"])] = f["nombre"]
        desgloses.append({"dim": d, **datos})
    f_origen = _nombres_origen(db, t)
    b_origen = desglose_bandeja(db, t, c, BorradorDeca.origen, "b_origen")
    for f in b_origen:
        f["nombre"] = SIN_DATO if f["valor"] == VACIO else f_origen(f["valor"])
        nombres_filtro[("b_origen", f["valor"])] = f["nombre"]
    b_resuelto = desglose_bandeja(db, t, c, BorradorDeca.resuelto_por, "b_resuelto")
    for f in b_resuelto:
        f["nombre"] = "Sin resolver" if f["valor"] == VACIO else f["valor"]

    pestana = p.get("pestana") if p.get("pestana") in ("expediciones", "bandeja") else "expediciones"
    try:
        pagina = int(p.get("pagina", "1"))
    except ValueError:
        pagina = 1
    if pestana == "expediciones":
        pag = paginar(tot["total"], pagina, TAMANO)
        registros = db.execute(
            select(Expedicion).where(*c.condiciones(t))
            .order_by(c.columna_fecha().desc(), Expedicion.referencia_externa).offset(pag["offset"]).limit(TAMANO)
        ).scalars().all()
    else:
        pag = paginar(tot_b["total"], pagina, TAMANO)
        registros = db.execute(
            select(BorradorDeca).where(*c.condiciones_bandeja(t)).order_by(BorradorDeca.fecha_alta.desc())
            .offset(pag["offset"]).limit(TAMANO)
        ).scalars().all()

    fichas = [{"texto": _texto_filtro(k, v, nombres_filtro), "quitar": enlace(**{k: None})} for k, v in c.filtros.items()]
    if c.tramo:
        fichas.insert(0, {"texto": f"Tramo: {c.tramo[0]:%d/%m/%Y} – {(c.tramo[1] - timedelta(days=1)):%d/%m/%Y}",
                          "quitar": enlace(tramo=None)})
    max_evo = max((e["n"] for e in evo), default=0)
    return templates.TemplateResponse(
        request,
        "dashboard/historial.html",
        {
            "sesion": sesion, "c": c, "p": p, "periodos": PERIODOS, "fechas": FECHAS, "tot": tot, "tot_b": tot_b,
            "unidad": unidad, "evo": evo, "max_evo": max_evo, "desgloses": desgloses, "b_origen": b_origen,
            "b_resuelto": b_resuelto, "pestana": pestana, "pag": pag, "registros": registros, "fichas": fichas,
            "enlace": enlace, "hoy": datetime.now(ZoneInfo(zona_predeterminada())).date().isoformat(), "vacio": VACIO, "nombres_estado": NOMBRES_ESTADO,
            "estados_bandeja": ESTADOS_BANDEJA, "f_origen": f_origen,
            "url_exportar": "/dashboard/historial/exportar" + (f"?{request.url.query}" if request.url.query else ""),
            "url_sin_filtros": "/dashboard/historial?" + urlencode({k: v for k, v in p.items() if k in ("periodo", "ref", "desde", "hasta", "fecha")}),
            "hasta_incluido": (c.periodo.fin - timedelta(days=1)).isoformat(),
        },
    )


@router.get("/exportar")
def exportar(
    request: Request,
    sesion: SesionUsuario = Depends(requerir_rol("admin")),
    db: Session = Depends(get_db),
):
    """Libro de Excel con todo lo de la consulta: resumen y totales,
    evolución, un desglose por hoja, expediciones y bandeja."""
    from app.excel import Columna, LibroExcel, respuesta_xlsx
    from app.routers.dashboard import _columnas_expediciones, fila_expedicion

    p, c = _leer(request)
    t = sesion.tenant_id
    settings = get_settings()
    tot = totales(db, t, c)
    tot_b = totales_bandeja(db, t, c)
    unidad, evo = evolucion(db, t, c)
    f_origen = _nombres_origen(db, t)

    libro = LibroExcel("Historial de MADeCa")
    nombres = {}
    datos_dims = []
    for d in DIMENSIONES:
        datos = desglose(db, t, c, d, limite=None)
        for f in datos["filas"]:
            nombres[(d.clave, f["valor"])] = f["nombre"]
        datos_dims.append((d, datos))
    filtros = [("Periodo", f"{PERIODOS[c.periodo.tipo]}: {c.periodo.etiqueta}"), ("Fecha que cuenta", FECHAS[c.fecha])]
    if c.tramo:
        filtros.append(("Tramo", f"{c.tramo[0]:%d/%m/%Y} – {(c.tramo[1] - timedelta(days=1)):%d/%m/%Y}"))
    filtros += [("Filtro", _texto_filtro(k, v, nombres)) for k, v in c.filtros.items()]
    horas = lambda h: f"{h:.1f} h" if h is not None else "—"  # noqa: E731
    libro.resumen(
        f"{PERIODOS[c.periodo.tipo]} · {c.periodo.etiqueta}",
        filtros,
        [
            ("Expediciones", [
                ("Total", tot["total"]), ("En tránsito", tot["en_transito"]), ("Entregadas", tot["entregado"]),
                ("Anuladas", tot["anulado"]), ("Firmadas (AdES)", tot["firmadas"]), ("Modificadas durante el servicio", tot["modificadas"]),
                ("Con lecturas del QR", tot["con_lecturas"]), ("Lecturas del QR", tot["lecturas"]),
                ("Mercancía peligrosa (ADR)", tot["adr"]), ("Peso total (kg)", tot["peso_kg"], "decimal"),
                ("Expediciones sin peso (otra magnitud o PDF propio)", tot["sin_peso"]), ("Bultos", tot["bultos"]),
                ("Tiempo medio de emisión a entrega", horas(tot["horas_entrega"])),
                ("Destinatarios distintos", tot["destinatarios"]), ("Cargadores distintos", tot["cargadores"]),
                ("Vehículos distintos", tot["vehiculos"]),
            ]),
            ("Bandeja de revisión (por fecha de recepción)", [
                ("Recibidos", tot_b["total"]), ("Pendientes", tot_b["pendiente"]), ("Emitidos", tot_b["emitido"]),
                ("Descartados", tot_b["descartado"]), ("Con datos que faltan", tot_b["con_avisos"]),
                ("Tiempo medio de revisión", horas(tot_b["horas_revision"])),
            ]),
        ],
    )
    libro.tabla("Evolución", [Columna(f"Tramo ({unidad})", 14), Columna("Desde", 12, "fecha"), Columna("Hasta", 12, "fecha"),
                              Columna("Expediciones", 14, "entero", "suma"), Columna("Peso (kg)", 14, "decimal", "suma")],
                ([e["etiqueta"], e["desde"], e["hasta"] - timedelta(days=1), e["n"], e["peso_kg"]] for e in evo))
    for d, datos in datos_dims:
        total_dim = sum(f["n"] for f in datos["filas"]) or 1
        columnas = [Columna(d.columna, 40)] + ([Columna("NIF", 14)] if d.con_nif else []) + [
            Columna("Expediciones", 14, "entero", "suma"), Columna("% del total", 11, "porcentaje"),
            Columna("Peso (kg)", 14, "decimal", "suma"), Columna("Bultos", 10, "entero", "suma")]
        libro.tabla(d.titulo, columnas, (
            [f["nombre"]] + ([f["nif"]] if d.con_nif else []) + [f["n"], f["n"] / total_dim, f["peso_kg"], f["bultos"]]
            for f in datos["filas"]))

    cond = c.condiciones(t)
    lecturas = dict(db.execute(
        select(AccesoLog.expedicion_id, func.count()).where(AccesoLog.expedicion_id.in_(select(Expedicion.id).where(*cond)))
        .group_by(AccesoLog.expedicion_id)
    ).all())
    emitida_por = dict(db.execute(
        select(EventoExpedicion.expedicion_id, EventoExpedicion.quien).where(
            EventoExpedicion.accion == "emitida", EventoExpedicion.expedicion_id.in_(select(Expedicion.id).where(*cond)))
    ).all())
    libro.tabla("Expediciones", _columnas_expediciones() + [Columna("Emitida por", 26)], (
        fila_expedicion(e, lecturas.get(e.id, 0), url_publica_activa(e, settings)) + [emitida_por.get(e.id, "No consta")]
        for e in db.execute(
            select(Expedicion).where(*cond).order_by(c.columna_fecha(), Expedicion.referencia_externa).limit(MAX_FILAS_EXCEL)
            .execution_options(yield_per=1000)
        ).scalars()))
    referencias = dict(db.execute(
        select(Expedicion.id, Expedicion.referencia_externa).where(
            Expedicion.id.in_(select(BorradorDeca.expedicion_id).where(*c.condiciones_bandeja(t))))
    ).all())
    libro.tabla("Bandeja", [
        Columna("Recibido", 17, "fecha_hora"), Columna("Documento", 34), Columna("Origen", 26), Columna("Estado", 12),
        Columna("Datos que faltan", 10, "entero", "suma"), Columna("Resuelto", 17, "fecha_hora"), Columna("Resuelto por", 26),
        Columna("Expedición", 18)], (
        [b.fecha_alta, b.nombre_documento or b.referencia_fuente, f_origen(b.origen), ESTADOS_BANDEJA.get(b.estado, b.estado),
         len(b.avisos or []), b.fecha_resolucion, b.resuelto_por, referencias.get(b.expedicion_id)]
        for b in db.execute(
            select(BorradorDeca).where(*c.condiciones_bandeja(t)).order_by(BorradorDeca.fecha_alta).limit(MAX_FILAS_EXCEL)
            .execution_options(yield_per=1000)
        ).scalars()))
    return respuesta_xlsx(libro.guardar(), f"historial_{c.periodo.inicio:%Y%m%d}")
