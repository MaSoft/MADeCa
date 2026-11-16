"""Motor de plantillas común del panel, con filtros de fecha.

Las fechas se guardan con su zona; en pantalla se muestran en la hora
local de la instalación (ZONA_HORARIA) o, con `zona=`, en la del propio
transporte, y con la fecha (DD/MM/AAAA) y la hora (HH:MM) por separado.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.revision import ALTERNATIVAS, CAMPOS, OBLIGATORIOS, TIPOS, texto_articulo
from app.zonas import ZONAS, local, zona_predeterminada

ESTATICOS = Path(__file__).resolve().parent / "static"


def _zona_valida(zona: str | None) -> str | None:
    return zona if zona in ZONAS else None


def fecha(valor: datetime | None, vacio: str = "—", zona: str | None = None) -> str:
    return local(valor, _zona_valida(zona)).strftime("%d/%m/%Y") if valor else vacio


def hora(valor: datetime | None, segundos: bool = False, vacio: str = "", zona: str | None = None) -> str:
    return local(valor, _zona_valida(zona)).strftime("%H:%M:%S" if segundos else "%H:%M") if valor else vacio


def nombre_zona(zona: str | None) -> str:
    """«hora de Canarias» / «hora peninsular»."""
    return ZONAS[_zona_valida(zona) or zona_predeterminada()]


def decimal_es(valor, decimales: int = 3) -> str:
    """1250.5 -> «1250,5»: coma decimal en los documentos en español y sin
    ceros sobrantes."""
    if valor is None or valor == "":
        return ""
    texto = f"{float(valor):.{decimales}f}".rstrip("0").rstrip(".")
    return texto.replace(".", ",")


@lru_cache(maxsize=None)
def estatico(ruta: str) -> str:
    """URL de un fichero de app/static con la huella de su contenido
    (?v=…): al cambiar el fichero cambia la URL y el navegador no puede
    seguir usando la copia vieja (página nueva con estilos viejos)."""
    try:
        huella = hashlib.sha256((ESTATICOS / ruta).read_bytes()).hexdigest()[:12]
    except OSError:
        return f"/static/{ruta}"
    return f"/static/{ruta}?v={huella}"


def crear_plantillas() -> Jinja2Templates:
    plantillas = Jinja2Templates(directory="app/templates")
    plantillas.env.filters["fecha"] = fecha
    plantillas.env.filters["hora"] = hora
    plantillas.env.filters["nombre_zona"] = nombre_zona
    plantillas.env.filters["decimal"] = decimal_es
    plantillas.env.globals["zonas_horarias"] = ZONAS
    plantillas.env.globals["estatico"] = estatico
    # Formulario del DeCA: obligatorios del art. 6 y comprobación por campo.
    plantillas.env.globals["obligatorios"] = OBLIGATORIOS
    plantillas.env.globals["alternativas"] = ALTERNATIVAS
    plantillas.env.globals["tipos"] = TIPOS
    plantillas.env.globals["campos_deca"] = CAMPOS
    plantillas.env.globals["texto_articulo"] = texto_articulo
    plantillas.env.globals["nombres_estado"] = {"en_transito": "En tránsito", "entregado": "Entregado", "anulado": "Anulado"}
    plantillas.env.globals["zona_predeterminada"] = zona_predeterminada
    return plantillas
