"""Historial: estadísticas de todo lo gestionado en un periodo (solo
administradores).

Se trabaja con lo que MADeCa guarda: expediciones (con los datos del DeCA
cuando los tiene: en el camino del PDF propio del ERP solo constan los
metadatos), su actividad (quién emitió) y los borradores de la bandeja
en todos sus estados. No hay conductor: el art. 6 de la Orden
FOM/2861/2012 no lo pide y MADeCa no lo guarda.

Cada estadística es también un filtro: al pulsarla se añade a la
consulta y la página entera se recalcula con él.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Numeric, and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from app.domain.models import AccesoLog, BorradorDeca, EventoExpedicion, Expedicion, ModificacionDeca
from app.zonas import zona_predeterminada

PERIODOS = {"dia": "Diario", "semana": "Semanal", "mes": "Mensual", "trimestre": "Trimestral", "anio": "Anual",
            "personalizado": "A elegir"}
FECHAS = {"transporte": "Transporte", "emision": "Emisión", "entrega": "Entrega"}
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")
VACIO = "__vacio__"
SIN_DATO = "Sin dato"
NOMBRES_ESTADO = {"en_transito": "En tránsito", "entregado": "Entregado", "anulado": "Anulado"}
ESTADOS_BANDEJA = {"pendiente": "Pendiente", "emitido": "Emitido", "descartado": "Descartado"}

# Provincias por los dos primeros dígitos del código postal (INE).
PROVINCIAS = {
    "01": "Álava", "02": "Albacete", "03": "Alicante", "04": "Almería", "05": "Ávila", "06": "Badajoz", "07": "Illes Balears",
    "08": "Barcelona", "09": "Burgos", "10": "Cáceres", "11": "Cádiz", "12": "Castellón", "13": "Ciudad Real", "14": "Córdoba",
    "15": "A Coruña", "16": "Cuenca", "17": "Girona", "18": "Granada", "19": "Guadalajara", "20": "Gipuzkoa", "21": "Huelva",
    "22": "Huesca", "23": "Jaén", "24": "León", "25": "Lleida", "26": "La Rioja", "27": "Lugo", "28": "Madrid", "29": "Málaga",
    "30": "Murcia", "31": "Navarra", "32": "Ourense", "33": "Asturias", "34": "Palencia", "35": "Las Palmas", "36": "Pontevedra",
    "37": "Salamanca", "38": "Santa Cruz de Tenerife", "39": "Cantabria", "40": "Segovia", "41": "Sevilla", "42": "Soria",
    "43": "Tarragona", "44": "Teruel", "45": "Toledo", "46": "Valencia", "47": "Valladolid", "48": "Bizkaia", "49": "Zamora",
    "50": "Zaragoza", "51": "Ceuta", "52": "Melilla",
}


# ------------------------------------------------------------------ periodo

def _zona() -> ZoneInfo:
    return ZoneInfo(zona_predeterminada())


def _dia(valor: str | None) -> date | None:
    try:
        return date.fromisoformat((valor or "").strip())
    except ValueError:
        return None


@dataclass
class Periodo:
    tipo: str
    inicio: date  # incluido
    fin: date  # excluido
    etiqueta: str
    anterior: date
    siguiente: date
    error: str | None = None

    def limites(self) -> tuple[datetime, datetime]:
        z = _zona()
        return datetime.combine(self.inicio, time.min, tzinfo=z), datetime.combine(self.fin, time.min, tzinfo=z)

    @property
    def dias(self) -> int:
        return (self.fin - self.inicio).days


def _mas_meses(d: date, meses: int) -> date:
    total = d.year * 12 + d.month - 1 + meses
    return date(total // 12, total % 12 + 1, 1)


def calcular_periodo(tipo: str, ref: str | None, desde: str | None, hasta: str | None) -> Periodo:
    tipo = tipo if tipo in PERIODOS else "mes"
    hoy = datetime.now(_zona()).date()
    r = _dia(ref) or hoy
    if tipo == "dia":
        return Periodo(tipo, r, r + timedelta(days=1), f"{r:%d/%m/%Y}", r - timedelta(days=1), r + timedelta(days=1))
    if tipo == "semana":
        lunes = r - timedelta(days=r.weekday())
        domingo = lunes + timedelta(days=6)
        return Periodo(tipo, lunes, lunes + timedelta(days=7),
                       f"Semana {lunes.isocalendar()[1]} · {lunes:%d/%m} – {domingo:%d/%m/%Y}",
                       lunes - timedelta(days=7), lunes + timedelta(days=7))
    if tipo == "mes":
        primero = r.replace(day=1)
        return Periodo(tipo, primero, _mas_meses(primero, 1), f"{MESES[primero.month - 1].capitalize()} {primero.year}",
                       _mas_meses(primero, -1), _mas_meses(primero, 1))
    if tipo == "trimestre":
        primero = date(r.year, (r.month - 1) // 3 * 3 + 1, 1)
        return Periodo(tipo, primero, _mas_meses(primero, 3), f"{(primero.month - 1) // 3 + 1}.º trimestre {primero.year}",
                       _mas_meses(primero, -3), _mas_meses(primero, 3))
    if tipo == "anio":
        primero = date(r.year, 1, 1)
        return Periodo(tipo, primero, date(r.year + 1, 1, 1), str(r.year), date(r.year - 1, 1, 1), date(r.year + 1, 1, 1))
    # A elegir: «desde» y «hasta» incluidos.
    d, h = _dia(desde), _dia(hasta)
    error = None
    if d is None or h is None:
        d = d or h or hoy.replace(day=1)
        h = h or d
        if (desde or hasta) and (_dia(desde) is None and desde or _dia(hasta) is None and hasta):
            error = "Fecha no válida: usa el selector de fecha."
    if d > h:
        d, h, error = h, d, "«Desde» era posterior a «Hasta»: se han intercambiado."
    largo = (h - d).days + 1
    return Periodo(tipo, d, h + timedelta(days=1), f"{d:%d/%m/%Y} – {h:%d/%m/%Y}",
                   d - timedelta(days=largo), d + timedelta(days=largo), error)


# ------------------------------------------------------------------ dimensiones

def _texto(*ruta: str):
    return Expedicion.datos_deca[ruta].astext


def _numero(*ruta: str):
    elemento = Expedicion.datos_deca[ruta]
    return case((func.jsonb_typeof(elemento) == "number", elemento.astext.cast(Numeric)), else_=None)


def _cp(expr):
    return func.substring(expr, r"\m(\d{5})\M")


PESO = _numero("mercancia", "peso_kg")
BULTOS = _numero("mercancia", "bultos")
_EMITIDA_POR = (
    select(EventoExpedicion.quien).where(EventoExpedicion.expedicion_id == Expedicion.id, EventoExpedicion.accion == "emitida")
    .limit(1).correlate(Expedicion).scalar_subquery()
)


@dataclass
class Dimension:
    clave: str
    titulo: str
    valor: object  # expresión SQL que identifica el grupo y por la que se filtra
    nombre: object | None = None  # expresión con el nombre a mostrar (si distinto)
    columna: str = "Nombre"
    con_nif: bool = False
    etiquetas: dict | None = None
    texto_vacio: str = SIN_DATO


DIMENSIONES: list[Dimension] = [
    Dimension("destinatario", "Destinatarios", _texto("destinatario", "nif_cif"), _texto("destinatario", "nombre_razon_social"),
              "Destinatario", True),
    Dimension("cargador", "Cargadores", _texto("cargador", "nif_cif"), _texto("cargador", "nombre_razon_social"), "Cargador", True),
    Dimension("transportista", "Transportistas", _texto("transportista", "nif_cif"), _texto("transportista", "nombre_razon_social"),
              "Transportista", True),
    Dimension("vehiculo", "Vehículos", Expedicion.matricula_vehiculo, columna="Matrícula"),
    Dimension("remolque", "Remolques", Expedicion.matricula_remolque, columna="Matrícula"),
    Dimension("prov_destino", "Provincias de destino", func.left(_cp(_texto("destino_direccion")), 2), columna="Provincia",
              etiquetas=PROVINCIAS),
    Dimension("prov_origen", "Provincias de origen", func.left(_cp(_texto("origen_direccion")), 2), columna="Provincia",
              etiquetas=PROVINCIAS),
    Dimension("destino", "Destinos", _texto("destino_direccion"), columna="Dirección"),
    Dimension("origen", "Orígenes", _texto("origen_direccion"), columna="Dirección"),
    Dimension("mercancia", "Mercancías", func.lower(func.trim(_texto("mercancia", "naturaleza"))),
              func.trim(_texto("mercancia", "naturaleza")), "Mercancía"),
    Dimension("canal", "ERP o canal de origen", Expedicion.erp_origen, columna="Origen"),
    Dimension("emitida_por", "Emitidas por", _EMITIDA_POR, columna="Usuario o canal",
              texto_vacio="No consta (anterior al registro de actividad)"),
]
POR_CLAVE = {d.clave: d for d in DIMENSIONES}

# Filtros de sí/no y de estado (indicadores).
INDICADORES_FILTRO = {
    "estado": lambda v: Expedicion.estado == v,
    "firmado": lambda v: Expedicion.firmado.is_(v == "si"),
    "adr": lambda v: (_texto("mercancia", "adr_numero_onu").is_not(None)) if v == "si" else (_texto("mercancia", "adr_numero_onu").is_(None)),
    "modificada": lambda v: (exists().where(ModificacionDeca.expedicion_id == Expedicion.id)) if v == "si"
    else ~exists().where(ModificacionDeca.expedicion_id == Expedicion.id),
    "lecturas": lambda v: (exists().where(AccesoLog.expedicion_id == Expedicion.id)) if v == "si"
    else ~exists().where(AccesoLog.expedicion_id == Expedicion.id),
}
TEXTO_INDICADOR = {
    ("firmado", "si"): "Firmadas", ("firmado", "no"): "Sin firmar", ("adr", "si"): "Mercancía peligrosa (ADR)",
    ("adr", "no"): "Sin ADR", ("modificada", "si"): "Modificadas", ("modificada", "no"): "Sin modificar",
    ("lecturas", "si"): "Con lecturas del QR", ("lecturas", "no"): "Sin lecturas del QR",
}
FILTROS_BANDEJA = ("b_estado", "b_origen", "b_resuelto")


@dataclass
class Consulta:
    periodo: Periodo
    fecha: str
    filtros: dict[str, str] = field(default_factory=dict)  # clave -> valor (dimensiones e indicadores)
    tramo: tuple[date, date] | None = None  # sub-rango pulsado en la evolución

    def limites(self) -> tuple[datetime, datetime]:
        if self.tramo:
            z = _zona()
            return datetime.combine(self.tramo[0], time.min, tzinfo=z), datetime.combine(self.tramo[1], time.min, tzinfo=z)
        return self.periodo.limites()

    def columna_fecha(self):
        return {"transporte": Expedicion.fecha_transporte, "emision": Expedicion.fecha_emision,
                "entrega": Expedicion.fecha_finalizacion_servicio}[self.fecha]

    def condiciones(self, tenant_id, sin: str | None = None) -> list:
        """Condiciones de las expediciones. `sin`: filtro que se omite (para
        el desglose de esa misma dimensión, que muestra todas sus filas)."""
        inicio, fin = self.limites()
        col = self.columna_fecha()
        condiciones = [Expedicion.tenant_id == tenant_id, col >= inicio, col < fin]
        for clave, valor in self.filtros.items():
            if clave == sin or clave in FILTROS_BANDEJA:
                continue
            if clave in POR_CLAVE:
                expr = POR_CLAVE[clave].valor
                condiciones.append(or_(expr.is_(None), expr == "") if valor == VACIO else expr == valor)
            elif clave in INDICADORES_FILTRO:
                condiciones.append(INDICADORES_FILTRO[clave](valor))
        return condiciones

    def condiciones_bandeja(self, tenant_id) -> list:
        inicio, fin = self.limites()
        condiciones = [BorradorDeca.tenant_id == tenant_id, BorradorDeca.fecha_alta >= inicio, BorradorDeca.fecha_alta < fin]
        if "b_estado" in self.filtros:
            condiciones.append(BorradorDeca.estado == self.filtros["b_estado"])
        if "b_origen" in self.filtros:
            condiciones.append(BorradorDeca.origen == self.filtros["b_origen"])
        if "b_resuelto" in self.filtros:
            v = self.filtros["b_resuelto"]
            condiciones.append(BorradorDeca.resuelto_por.is_(None) if v == VACIO else BorradorDeca.resuelto_por == v)
        return condiciones


VALORES_INDICADOR = {"firmado": ("si", "no"), "adr": ("si", "no"), "modificada": ("si", "no"), "lecturas": ("si", "no"),
                     "estado": tuple(NOMBRES_ESTADO)}


def leer_filtros(parametros: dict) -> tuple[dict[str, str], tuple[date, date] | None]:
    filtros = {}
    for clave, valor in parametros.items():
        valor = (valor or "").strip()[:300]
        if not valor:
            continue
        if clave in POR_CLAVE:
            filtros[clave] = valor
        elif clave in VALORES_INDICADOR and valor in VALORES_INDICADOR[clave]:
            filtros[clave] = valor
        elif clave == "b_estado" and valor in ESTADOS_BANDEJA:
            filtros[clave] = valor
        elif clave in ("b_origen", "b_resuelto"):
            filtros[clave] = valor
    tramo = None
    if parametros.get("tramo"):
        partes = parametros["tramo"].split(":")
        d, h = (_dia(partes[0]), _dia(partes[1])) if len(partes) == 2 else (None, None)
        if d and h and d < h:
            tramo = (d, h)
    return filtros, tramo


# ------------------------------------------------------------------ cálculos

def _horas(intervalo) -> float | None:
    return round(intervalo.total_seconds() / 3600, 1) if intervalo is not None else None


def totales(db: Session, tenant_id, c: Consulta) -> dict:
    cond = c.condiciones(tenant_id)
    fila = db.execute(
        select(
            func.count(),
            func.count().filter(Expedicion.estado == "en_transito"),
            func.count().filter(Expedicion.estado == "entregado"),
            func.count().filter(Expedicion.estado == "anulado"),
            func.count().filter(Expedicion.firmado.is_(True)),
            func.count().filter(_texto("mercancia", "adr_numero_onu").is_not(None)),
            func.count().filter(exists().where(ModificacionDeca.expedicion_id == Expedicion.id)),
            func.count().filter(exists().where(AccesoLog.expedicion_id == Expedicion.id)),
            func.coalesce(func.sum(PESO), 0),
            func.coalesce(func.sum(BULTOS), 0),
            func.avg(Expedicion.fecha_finalizacion_servicio - Expedicion.fecha_emision).filter(Expedicion.estado == "entregado"),
            func.count(func.distinct(_texto("destinatario", "nif_cif"))),
            func.count(func.distinct(_texto("cargador", "nif_cif"))),
            func.count(func.distinct(Expedicion.matricula_vehiculo)),
            func.count().filter(PESO.is_(None)),
        ).where(*cond)
    ).one()
    lecturas = db.execute(
        select(func.count()).select_from(AccesoLog).where(AccesoLog.expedicion_id.in_(select(Expedicion.id).where(*cond)))
    ).scalar_one()
    return {
        "total": fila[0], "en_transito": fila[1], "entregado": fila[2], "anulado": fila[3], "firmadas": fila[4], "adr": fila[5],
        "modificadas": fila[6], "con_lecturas": fila[7], "lecturas": lecturas, "peso_kg": float(fila[8] or 0),
        "bultos": int(fila[9] or 0), "horas_entrega": _horas(fila[10]), "destinatarios": fila[11], "cargadores": fila[12],
        "vehiculos": fila[13], "sin_peso": fila[14],
    }


def totales_bandeja(db: Session, tenant_id, c: Consulta) -> dict:
    cond = c.condiciones_bandeja(tenant_id)
    fila = db.execute(
        select(
            func.count(),
            func.count().filter(BorradorDeca.estado == "pendiente"),
            func.count().filter(BorradorDeca.estado == "emitido"),
            func.count().filter(BorradorDeca.estado == "descartado"),
            func.count().filter(func.jsonb_array_length(BorradorDeca.avisos) > 0),
            func.avg(BorradorDeca.fecha_resolucion - BorradorDeca.fecha_alta).filter(BorradorDeca.fecha_resolucion.is_not(None)),
        ).where(*cond)
    ).one()
    return {"total": fila[0], "pendiente": fila[1], "emitido": fila[2], "descartado": fila[3], "con_avisos": fila[4],
            "horas_revision": _horas(fila[5])}


def tramos(c: Consulta) -> tuple[str, list[tuple[date, date, str]]]:
    """Grupos de la evolución: por día hasta 31 días, por semana hasta 92 y
    por mes a partir de ahí."""
    inicio, fin = (c.tramo or (c.periodo.inicio, c.periodo.fin))
    dias = (fin - inicio).days
    salida = []
    if dias <= 31:
        d = inicio
        while d < fin:
            salida.append((d, d + timedelta(days=1), f"{d:%d/%m}"))
            d += timedelta(days=1)
        return "día", salida
    if dias <= 92:
        d = inicio - timedelta(days=inicio.weekday())
        while d < fin:
            salida.append((max(d, inicio), min(d + timedelta(days=7), fin), f"Sem. {d.isocalendar()[1]}"))
            d += timedelta(days=7)
        return "semana", salida
    d = inicio.replace(day=1)
    while d < fin:
        siguiente = _mas_meses(d, 1)
        salida.append((max(d, inicio), min(siguiente, fin), f"{MESES[d.month - 1][:3].capitalize()} {d:%y}"))
        d = siguiente
    return "mes", salida


def evolucion(db: Session, tenant_id, c: Consulta) -> tuple[str, list[dict]]:
    unidad, grupos = tramos(c)
    col = c.columna_fecha()
    z = zona_predeterminada()
    truncado = {"día": "day", "semana": "week", "mes": "month"}[unidad]
    filas = db.execute(
        select(func.date_trunc(truncado, func.timezone(z, col)).label("t"), func.count(), func.coalesce(func.sum(PESO), 0))
        .where(*c.condiciones(tenant_id)).group_by("t")
    ).all()
    por_inicio = {}
    for t, n, peso in filas:
        if t is not None:
            por_inicio[t.date()] = (n, float(peso or 0))
    salida = []
    for desde, hasta, etiqueta in grupos:
        clave = desde if unidad == "día" else (desde - timedelta(days=desde.weekday()) if unidad == "semana" else desde.replace(day=1))
        n, peso = por_inicio.get(clave, (0, 0.0))
        salida.append({"desde": desde, "hasta": hasta, "etiqueta": etiqueta, "n": n, "peso_kg": peso})
    return unidad, salida


def desglose(db: Session, tenant_id, c: Consulta, dim: Dimension, limite: int | None = 500) -> dict:
    """Filas {valor, nombre, nif, n, peso_kg, bultos} de la dimensión, de más
    a menos expediciones, y el total de grupos. Sin el filtro de la propia
    dimensión, para ver todas sus filas y cuál está elegida."""
    cond = c.condiciones(tenant_id, sin=dim.clave)
    nombre = func.max(dim.nombre) if dim.nombre is not None else literal(None)
    consulta = (
        select(dim.valor.label("v"), nombre, func.count().label("n"), func.coalesce(func.sum(PESO), 0), func.coalesce(func.sum(BULTOS), 0))
        .where(*cond).group_by(dim.valor).order_by(func.count().desc(), dim.valor)
    )
    if limite:
        consulta = consulta.limit(limite)
    grupos = db.execute(select(func.count(func.distinct(func.coalesce(dim.valor, VACIO)))).where(*cond)).scalar_one()
    filas = []
    for valor, nom, n, peso, bultos in db.execute(consulta).all():
        vacio = valor is None or valor == ""
        if dim.etiquetas is not None:
            texto = dim.texto_vacio if vacio else dim.etiquetas.get(valor, f"Código {valor}")
        else:
            texto = dim.texto_vacio if vacio else (nom or valor)
        filas.append({"valor": VACIO if vacio else valor, "nombre": texto, "nif": valor if dim.con_nif and not vacio else None,
                      "n": n, "peso_kg": float(peso or 0), "bultos": int(bultos or 0)})
    return {"filas": filas, "grupos": grupos}


def desglose_bandeja(db: Session, tenant_id, c: Consulta, columna, clave: str) -> list[dict]:
    cond = [x for x in c.condiciones_bandeja(tenant_id)]
    # Sin el filtro de la propia columna.
    sin = {"b_origen": BorradorDeca.origen, "b_resuelto": BorradorDeca.resuelto_por, "b_estado": BorradorDeca.estado}
    if clave in c.filtros:
        c2 = Consulta(c.periodo, c.fecha, {k: v for k, v in c.filtros.items() if k != clave}, c.tramo)
        cond = c2.condiciones_bandeja(tenant_id)
    filas = db.execute(
        select(columna, func.count()).where(*cond).group_by(columna).order_by(func.count().desc()).limit(500)
    ).all()
    return [{"valor": VACIO if v is None else v, "n": n} for v, n in filas]
