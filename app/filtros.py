"""Filtros de las listas del panel: búsqueda de texto y periodos de fechas.

Solo cambian qué filas se ven: no tocan los DeCA ni su conservación.
Las fechas que escribe el usuario (AAAA-MM-DD, de un <input type="date">)
son días en la hora local de la instalación (ZONA_HORARIA), la misma con
la que se muestran en las tablas; «hasta» incluye el día entero.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Text, cast, or_

from app.zonas import zona_predeterminada

MAX_TEXTO = 100

PERIODOS: dict[str, str] = {
    "": "Cualquier fecha",
    "hoy": "Hoy",
    "7d": "Últimos 7 días",
    "30d": "Últimos 30 días",
    "mes": "Este mes",
    "mes_anterior": "Mes anterior",
    "anio": "Este año",
    "rango": "Entre fechas",
}

# Rutas de los datos del DeCA (misma forma en expediciones y borradores)
# en las que busca el cuadro «Buscar».
RUTAS_DECA: tuple[tuple[str, ...], ...] = (
    ("referencia_externa",),
    ("matricula_vehiculo",),
    ("matricula_remolque",),
    ("cargador", "nombre_razon_social"),
    ("cargador", "nif_cif"),
    ("transportista", "nombre_razon_social"),
    ("transportista", "nif_cif"),
    ("destinatario", "nombre_razon_social"),
    ("destinatario", "nif_cif"),
    ("origen_direccion",),
    ("destino_direccion",),
    ("mercancia", "naturaleza"),
)


def texto_busqueda(valor: str | None) -> str:
    """Texto de búsqueda limpio y acotado ('' si no hay)."""
    return " ".join((valor or "").split())[:MAX_TEXTO]


def contiene(columna, texto: str):
    """La columna contiene el texto, sin distinguir mayúsculas. Los % y _
    que escriba el usuario se buscan tal cual (no son comodines)."""
    return cast(columna, Text).icontains(texto, autoescape=True)


def buscar_en(texto: str, columnas: list, columna_datos=None):
    """Condición «el texto aparece en alguna de estas columnas o en los
    datos del DeCA» (None si no hay texto)."""
    if not texto:
        return None
    condiciones = [contiene(c, texto) for c in columnas]
    if columna_datos is not None:
        condiciones += [contiene(columna_datos[ruta].astext, texto) for ruta in RUTAS_DECA]
    return or_(*condiciones)


def _dia(valor: str | None) -> date | None:
    try:
        return date.fromisoformat((valor or "").strip())
    except ValueError:
        return None


def rango_fechas(
    periodo: str | None, desde: str | None, hasta: str | None, hoy: date | None = None
) -> tuple[datetime | None, datetime | None, str | None]:
    """(inicio incluido, fin excluido, error). Si hay fechas «desde/hasta»
    mandan sobre el periodo. Las fechas no válidas se ignoran con aviso."""
    zona = ZoneInfo(zona_predeterminada())
    hoy = hoy or datetime.now(zona).date()

    def inicio_de(dia: date) -> datetime:
        return datetime.combine(dia, time.min, tzinfo=zona)

    if (desde or "").strip() or (hasta or "").strip():
        d, h = _dia(desde), _dia(hasta)
        if ((desde or "").strip() and d is None) or ((hasta or "").strip() and h is None):
            return None, None, "Fecha no válida: usa el selector de fecha."
        if d and h and d > h:
            return None, None, "La fecha «desde» es posterior a «hasta»."
        return (inicio_de(d) if d else None), (inicio_de(h + timedelta(days=1)) if h else None), None

    if periodo == "hoy":
        return inicio_de(hoy), inicio_de(hoy + timedelta(days=1)), None
    if periodo in ("7d", "30d"):
        dias = 7 if periodo == "7d" else 30
        return inicio_de(hoy - timedelta(days=dias - 1)), inicio_de(hoy + timedelta(days=1)), None
    if periodo == "mes":
        primero = hoy.replace(day=1)
        siguiente = (primero + timedelta(days=32)).replace(day=1)
        return inicio_de(primero), inicio_de(siguiente), None
    if periodo == "mes_anterior":
        primero = hoy.replace(day=1)
        anterior = (primero - timedelta(days=1)).replace(day=1)
        return inicio_de(anterior), inicio_de(primero), None
    if periodo == "anio":
        return inicio_de(date(hoy.year, 1, 1)), inicio_de(date(hoy.year + 1, 1, 1)), None
    return None, None, None


def en_rango(columna, inicio: datetime | None, fin: datetime | None) -> list:
    condiciones = []
    if inicio is not None:
        condiciones.append(columna >= inicio)
    if fin is not None:
        condiciones.append(columna < fin)
    return condiciones


def _ddmmaaaa(valor: str | None) -> str:
    dia = _dia(valor)
    return dia.strftime("%d/%m/%Y") if dia else ""


def resumen_periodo(periodo: str | None, desde: str | None, hasta: str | None, error: str | None = None) -> str:
    """Texto corto del filtro de fechas («Este mes», «01/10/2026 – 11/10/2026»…)."""
    if not error:
        d, h = _ddmmaaaa(desde), _ddmmaaaa(hasta)
        if d and h:
            return d if d == h else f"{d} – {h}"
        if d:
            return f"Desde {d}"
        if h:
            return f"Hasta {h}"
        if periodo and periodo != "rango" and periodo in PERIODOS:
            return PERIODOS[periodo]
    return "Cualquier fecha"


def ficha(texto: str, *campos: str) -> dict:
    """Ficha de un filtro activo: al pulsar su ✕ se vacían esos campos."""
    return {"texto": texto, "campos": " ".join(campos)}


def siguiente_orden(actual: str, primero: str, segundo: str, por_defecto: str) -> dict:
    """Datos de una cabecera ordenable: activa, sentido (por el sufijo
    «_desc») y a qué orden pasa al pulsarla: primero → segundo → defecto."""
    sentido = "desc" if actual.endswith("_desc") else "asc"
    if actual == primero:
        return {"activo": True, "sentido": sentido, "siguiente": segundo}
    if actual == segundo:
        return {"activo": True, "sentido": sentido, "siguiente": por_defecto}
    return {"activo": False, "sentido": "", "siguiente": primero}


TAMANO_PAGINA = 100


def paginar(total: int, pagina: int, tamano: int = TAMANO_PAGINA) -> dict:
    """Página válida (1..n), desplazamiento y números a mostrar en el
    paginador: 1 … p-2 p-1 p p+1 p+2 … n (None = «…»)."""
    paginas = max(1, -(-total // tamano))
    pagina = min(max(1, pagina), paginas)
    numeros = sorted({1, paginas, *range(max(1, pagina - 2), min(paginas, pagina + 2) + 1)})
    ventana: list[int | None] = []
    for n in numeros:
        if ventana and n - ventana[-1] > 1:
            ventana.append(None)
        ventana.append(n)
    desde = (pagina - 1) * tamano
    return {"pagina": pagina, "paginas": paginas, "offset": desde, "tamano": tamano,
            "desde": desde + 1 if total else 0, "hasta": min(total, desde + tamano), "ventana": ventana}
