"""Lectura de los datos del Art. 6 (Orden FOM/2861/2012) en el texto de un
albarán, carta de porte, CMR o pedido.

Reglas, no "inteligencia": busca etiquetas habituales en español (y las
del CMR en inglés) y valida lo encontrado (NIF/CIF con su dígito de
control, matrículas con su formato). Cada dato lleva una confianza:
- "alta": encontrado junto a su etiqueta y validado.
- "media": encontrado junto a su etiqueta, sin validación posible.
- "baja": deducido sin etiqueta (p. ej. una matrícula suelta).
Lo que no se encuentra no se inventa: queda vacío y con un aviso, y la
persona que revisa el borrador lo completa.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.validation import validar_matricula, validar_nif_cif

# Etiquetas por dato (sin tildes, en minúsculas).
ETIQUETAS: dict[str, tuple[str, ...]] = {
    "cargador": ("cargador contractual", "cargador", "remitente", "expedidor", "shipper", "consignor", "sender"),
    "transportista": ("transportista efectivo", "transportista", "porteador", "agencia de transporte", "carrier"),
    "destinatario": ("destinatario", "consignatario", "consignee", "receptor"),
    "origen": ("lugar de origen", "origen", "lugar de carga", "direccion de carga", "punto de carga", "place of loading", "recogida"),
    "destino": ("lugar de destino", "destino", "lugar de entrega", "direccion de entrega", "punto de descarga", "lugar de descarga", "place of delivery", "entrega"),
    "naturaleza": ("naturaleza de la mercancia", "naturaleza", "mercancia", "descripcion de la mercancia", "descripcion", "nature of the goods", "goods"),
    "peso": ("peso bruto", "peso total", "peso", "kilos", "gross weight", "weight"),
    "bultos": ("numero de bultos", "n de bultos", "bultos", "packages"),
    "fecha": ("fecha de carga", "fecha del transporte", "fecha de transporte", "fecha de salida", "fecha de expedicion", "fecha"),
    "matricula": ("matricula del vehiculo", "matricula tractora", "matricula", "vehiculo", "tractora", "camion"),
    "remolque": ("matricula del remolque", "matricula remolque", "remolque", "semirremolque", "trailer"),
    "referencia": ("albaran n", "n albaran", "numero de albaran", "albaran", "carta de porte n", "carta de porte", "n pedido", "pedido", "referencia", "cmr n", "documento n"),
    "autorizacion": ("autorizacion especial de circulacion", "autorizacion especial", "aec"),
}

_NIF_RE = re.compile(r"\b(?:ES)?([A-HJNP-SUVW][\s.-]?\d{7}[\s.-]?[0-9A-J]|\d{8}[\s.-]?[A-Z]|[XYZ][\s.-]?\d{7}[\s.-]?[A-Z])\b", re.I)
_MATRICULA_RE = re.compile(r"\b(R[\s-]?\d{4}[\s-]?[BCDFGHJKLMNPRSTVWXYZ]{3}|\d{4}[\s-]?[BCDFGHJKLMNPRSTVWXYZ]{3}|[A-Z]{1,2}[\s-]?\d{4}[\s-]?[A-Z]{1,2})\b")
_FECHA_RE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b(?:\s+(\d{1,2}):(\d{2}))?")
_PESO_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(kg|kgs|kilos|kilogramos|t|tn|tm|toneladas)\b", re.I)
_ENTERO_RE = re.compile(r"\b(\d{1,6})\b")


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


@dataclass
class ResultadoExtraccion:
    datos: dict = field(default_factory=dict)
    confianza: dict = field(default_factory=dict)
    avisos: list = field(default_factory=list)


@dataclass
class _Linea:
    original: str
    normal: str


def _buscar_etiqueta(linea: _Linea, dato: str) -> str | None:
    """Si la línea empieza por una etiqueta del dato (o la contiene seguida
    de ':'), devuelve el valor que hay detrás ("" si no hay nada)."""
    for etiqueta in ETIQUETAS[dato]:
        patron = re.compile(r"(?:^|\s|\|)" + re.escape(etiqueta) + r"\.?(?:º|o|°|\*)?\s*[:.\-]?\s*", re.I)
        m = patron.search(linea.normal)
        if m and (m.start() == 0 or ":" in linea.normal[m.start():m.end() + 1] or linea.normal[: m.start()].strip() == ""):
            # Mismo desplazamiento en el texto original (quitar tildes no
            # cambia la longitud en los caracteres latinos habituales).
            return linea.original[m.end():].strip(" :|\t")
    return None


def _es_resto_de_titulo(linea: _Linea, valor: str) -> bool:
    """«LUGAR DE CARGA (ORIGEN)» o «MERCANCÍA TRANSPORTADA» son títulos de
    un bloque, no «etiqueta: valor»: lo que sigue a la etiqueta es parte del
    título (entre paréntesis, o línea en mayúsculas sin «:» ni cifras) y el
    dato está en las líneas siguientes."""
    v = valor.strip()
    if re.fullmatch(r"\([^)]*\)", v):
        return True
    original = linea.original.strip()
    return (":" not in original and original == original.upper()
            and any(c.isalpha() for c in original) and not any(c.isdigit() for c in v))


# Cabeceras de columna habituales en la tabla de mercancía.
_CABECERAS = {"n", "no", "num", "numero", "embalaje", "bultos", "peso", "peso bruto", "peso bruto (kg)", "kg", "kilos",
              "cantidad", "unidades", "descripcion", "adr", "adr (onu / clase)", "onu", "clase", "volumen", "importe"}


def _es_cabecera(valor: str) -> bool:
    normal = _sin_tildes(valor).strip(" :.º°").lower()
    # Solo igualdad: «Mercancía general» es un valor, «Naturaleza de la
    # mercancía» es la cabecera.
    return normal in _CABECERAS or any(normal == e for etiquetas in ETIQUETAS.values() for e in etiquetas)


def _es_texto_de_dato(valor: str) -> bool:
    """Un valor de texto útil: con letras y que no sea otra cabecera."""
    return sum(c.isalpha() for c in valor) >= 3 and not _es_cabecera(valor)


# «No precisa», «N/A», «—»...: la autorización especial no aplica.
_SIN_AUTORIZACION = re.compile(r"^(no( precisa| procede| aplica| requiere| necesaria| necesita)?|n/?a|ninguna|sin autorizacion|-+|—+)\.?$")


def _valor_de(lineas: list[_Linea], dato: str, acepta=None) -> tuple[str | None, int | None]:
    """Primer valor tras una etiqueta del dato. Con `acepta`, las
    etiquetas cuyo valor no lo cumple se saltan y se sigue buscando (un
    título «ALBARÁN DE ENTREGA» no da la referencia «DE»)."""
    descartada = None
    for i, linea in enumerate(lineas):
        valor = _buscar_etiqueta(linea, dato)
        if valor is None:
            continue
        if valor and _es_resto_de_titulo(linea, valor):
            valor = ""
        if not valor:
            # Etiqueta sola en su línea: el valor está en la siguiente.
            for j in range(i + 1, min(i + 3, len(lineas))):
                if lineas[j].original.strip():
                    valor, i = lineas[j].original.strip(), j
                    break
        if valor and (acepta is None or acepta(valor)):
            return valor, i
        if acepta is None:
            return None, i
        descartada = i if descartada is None else descartada
    return None, descartada


def _primer_nif(texto: str) -> str | None:
    for m in _NIF_RE.finditer(texto):
        try:
            return validar_nif_cif(m.group(1))
        except ValueError:
            continue
    return None


def _quitar_nif(texto: str) -> str:
    sin = _NIF_RE.sub("", texto)
    sin = re.sub(r"\b(NIF|CIF|N\.?I\.?F\.?|C\.?I\.?F\.?|VAT)\b\s*[:.]?", "", sin, flags=re.I)
    return re.sub(r"\s{2,}", " ", sin).strip(" ,;-|")


# Subetiquetas habituales dentro del bloque de una parte o de un lugar
# («Razón social:», «CIF/NIF:», «Domicilio:», «Centro:»...). Solo cuentan
# seguidas de «:», para no confundirlas con texto corriente.
_SUBETIQUETAS: dict[str, tuple[str, ...]] = {
    "nombre": ("razon social", "denominacion social", "denominacion", "nombre", "empresa"),
    "nif": ("cif/nif", "nif/cif", "nif-iva", "nif", "cif", "vat"),
    "domicilio": ("domicilio social", "domicilio", "direccion"),
    "centro": ("centro", "lugar", "almacen", "instalacion"),
}


def _subetiqueta(linea: _Linea) -> tuple[str, str] | None:
    for clave, etiquetas in _SUBETIQUETAS.items():
        for etiqueta in etiquetas:
            m = re.match(re.escape(etiqueta) + r"\s*:\s*", linea.normal)
            if m:
                return clave, linea.original[m.end():].strip()
    return None


def _indice_etiqueta(lineas: list[_Linea], dato: str) -> int | None:
    return next((i for i, l in enumerate(lineas) if _buscar_etiqueta(l, dato) is not None), None)


def _bloque(lineas: list[_Linea], idx: int, dato: str, maximo: int = 10) -> list[_Linea]:
    """Líneas que siguen a la etiqueta hasta la etiqueta de otro dato (si
    no, un cargador sin NIF se quedaría con el NIF del transportista)."""
    bloque = []
    for k in range(idx + 1, min(idx + 1 + maximo, len(lineas))):
        if any(_buscar_etiqueta(lineas[k], otro) is not None for otro in ETIQUETAS if otro != dato):
            break
        bloque.append(lineas[k])
    return bloque


def _campos_bloque(bloque: list[_Linea]) -> dict[str, str]:
    """{'nombre': ..., 'nif': ..., 'domicilio': ..., 'centro': ...} con el
    valor en la misma línea o en la siguiente. Un domicilio partido en dos
    líneas («... (Santa Cruz de» / «Tenerife)») se une."""
    campos: dict[str, str] = {}
    k = 0
    while k < len(bloque):
        encontrada = _subetiqueta(bloque[k])
        if encontrada:
            clave, valor = encontrada
            if not valor and k + 1 < len(bloque) and not _subetiqueta(bloque[k + 1]):
                k += 1
                valor = bloque[k].original.strip()
            siguiente = bloque[k + 1].original.strip() if k + 1 < len(bloque) else ""
            if clave == "domicilio" and valor and siguiente and not _subetiqueta(bloque[k + 1]) and (
                valor.count("(") > valor.count(")") or re.search(r"\b(de|del|la|las|los|,)$", valor.lower())
                # Población en la línea siguiente («..., 38509» / «Güímar»).
                or (re.search(r"\b\d{5}$", valor) and ":" not in siguiente and len(siguiente.split()) <= 4)
            ):
                k += 1
                valor = f"{valor} {bloque[k].original.strip()}"
            if valor:
                campos.setdefault(clave, valor)
        k += 1
    return campos


def _parte(lineas: list[_Linea], rol: str, res: ResultadoExtraccion) -> None:
    valor, _ = _valor_de(lineas, rol)
    idx = _indice_etiqueta(lineas, rol)
    if idx is None:
        return
    bloque_lineas = _bloque(lineas, idx, rol)
    campos = _campos_bloque(bloque_lineas)
    bloque = [lineas[idx].original] + [l.original for l in bloque_lineas]
    parte: dict = {}
    # El valor junto a la etiqueta no sirve si es una subetiqueta («Razón
    # social:») que el título del bloque dejó en la línea siguiente.
    if valor and _subetiqueta(_Linea(valor, _sin_tildes(valor))):
        valor = None
    nombre = _quitar_nif(campos.get("nombre") or valor or "")
    if nombre:
        parte["nombre_razon_social"] = nombre[:300]
        res.confianza[f"{rol}.nombre_razon_social"] = "media"
    nif = _primer_nif(campos.get("nif", "")) or _primer_nif(" ".join(bloque[:4] if "nif" not in campos else bloque))
    if nif:
        parte["nif_cif"] = nif
        res.confianza[f"{rol}.nif_cif"] = "alta"
    if campos.get("domicilio"):
        parte["domicilio"] = _quitar_nif(campos["domicilio"])[:500]
        res.confianza[f"{rol}.domicilio"] = "media"
    # Sin subetiqueta: primera línea del bloque (tras el nombre) que no sea
    # el NIF ni otra etiqueta.
    for linea in [] if "domicilio" in parte else bloque[1:4]:
        candidato = _quitar_nif(linea)
        normal = _sin_tildes(candidato)
        if len(candidato) >= 6 and not any(
            normal.startswith(e) for etiquetas in ETIQUETAS.values() for e in etiquetas
        ) and re.search(r"\d|calle|c/|avda|avenida|pol|plaza|ctra|carretera|camino|paseo", normal):
            parte["domicilio"] = candidato[:500]
            res.confianza[f"{rol}.domicilio"] = "media"
            break
    if parte:
        res.datos[rol] = parte


def _fecha(texto: str) -> datetime | None:
    m = _FECHA_RE.search(texto)
    if not m:
        return None
    dia, mes, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if anio < 100:
        anio += 2000
    hora, minuto = (int(m.group(4)), int(m.group(5))) if m.group(4) else (0, 0)
    try:
        return datetime(anio, mes, dia, hora, minuto)
    except ValueError:
        return None


def _peso_kg(texto: str) -> float | None:
    m = _PESO_RE.search(texto)
    if not m:
        return None
    numero = m.group(1).replace(" ", "")
    # Formato español: 1.234,5 -> 1234.5 ; también 850.5 o 850,5.
    if "," in numero:
        numero = numero.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", numero):
        numero = numero.replace(".", "")
    try:
        valor = float(numero)
    except ValueError:
        return None
    if m.group(2).lower() in ("t", "tn", "tm", "toneladas"):
        valor *= 1000
    return valor if valor > 0 else None


def _matricula(texto: str) -> str | None:
    for m in _MATRICULA_RE.finditer(texto.upper()):
        try:
            return validar_matricula(m.group(1))
        except ValueError:
            continue
    return None


def extraer_campos(texto: str) -> ResultadoExtraccion:
    res = ResultadoExtraccion()
    lineas = [_Linea(l.rstrip(), _sin_tildes(l.rstrip())) for l in texto.splitlines()]
    lineas = [l for l in lineas if l.original.strip()]

    for rol in ("cargador", "transportista", "destinatario"):
        _parte(lineas, rol, res)

    for dato, clave in (("origen", "origen_direccion"), ("destino", "destino_direccion")):
        valor, _ = _valor_de(lineas, dato)
        idx = _indice_etiqueta(lineas, dato)
        campos = _campos_bloque(_bloque(lineas, idx, dato, maximo=6)) if idx is not None else {}
        # «Centro: ...» + «Dirección: ...» bajo un título de bloque.
        compuesto = ", ".join(v for v in (campos.get("centro"), campos.get("domicilio")) if v)
        if compuesto:
            valor = compuesto
        elif valor and _subetiqueta(_Linea(valor, _sin_tildes(valor))):
            valor = None
        if valor:
            res.datos[clave] = valor[:500]
            res.confianza[clave] = "media"

    mercancia: dict = {}
    valor, idx = _valor_de(lineas, "naturaleza", acepta=_es_texto_de_dato)
    de_tabla = False
    if not valor and idx is not None:
        # Tabla: tras la fila de cabeceras, la primera fila con texto.
        for linea in lineas[idx + 1: idx + 14]:
            texto_linea = linea.original.strip()
            if _es_texto_de_dato(texto_linea) and _buscar_etiqueta(linea, "peso") is None:
                valor, de_tabla = texto_linea, True
                break
    if valor:
        mercancia["naturaleza"] = valor[:500]
        res.confianza["mercancia.naturaleza"] = "baja" if de_tabla else "media"
    # Se salta lo que no es un peso (p. ej. la cabecera «Peso bruto (kg)»)
    # hasta dar con «Peso bruto total: 13.700 kg».
    valor, idx = _valor_de(lineas, "peso", acepta=lambda v: _peso_kg(v) is not None)
    peso = _peso_kg(valor or "") if valor else None
    if peso is None and idx is not None:
        peso = _peso_kg(lineas[idx].original)
    if peso is not None:
        mercancia["peso_kg"] = peso
        res.confianza["mercancia.peso_kg"] = "alta"
    valor, _ = _valor_de(lineas, "bultos", acepta=lambda v: _ENTERO_RE.search(v) is not None)
    if valor and (m := _ENTERO_RE.search(valor)):
        mercancia["bultos"] = int(m.group(1))
        res.confianza["mercancia.bultos"] = "media"
    if mercancia:
        res.datos["mercancia"] = mercancia

    valor, _ = _valor_de(lineas, "fecha")
    fecha = _fecha(valor) if valor else None
    if fecha:
        res.confianza["fecha_transporte"] = "media"
    else:
        fecha = _fecha(texto)
        if fecha:
            res.confianza["fecha_transporte"] = "baja"
    if fecha:
        res.datos["fecha_transporte"] = fecha.isoformat()

    valor, _ = _valor_de(lineas, "remolque")
    remolque = _matricula(valor) if valor else None
    if remolque:
        res.datos["matricula_remolque"] = remolque
        res.confianza["matricula_remolque"] = "alta"
    valor, _ = _valor_de(lineas, "matricula")
    matricula = _matricula(valor) if valor else None
    if matricula and matricula != remolque:
        res.confianza["matricula_vehiculo"] = "alta"
    else:
        matricula = None
        for m in _MATRICULA_RE.finditer(texto.upper()):
            try:
                candidata = validar_matricula(m.group(1))
            except ValueError:
                continue
            if candidata != remolque and not candidata.startswith("R"):
                matricula = candidata
                res.confianza["matricula_vehiculo"] = "baja"
                break
    if matricula:
        res.datos["matricula_vehiculo"] = matricula

    # Una referencia de albarán, pedido o CMR lleva al menos un dígito.
    valor, _ = _valor_de(lineas, "referencia", acepta=lambda v: any(c.isdigit() for c in v.split()[0]))
    if valor:
        ref = valor.split()[0].strip(" :.#") if valor.split() else ""
        if ref:
            res.datos["referencia_externa"] = ref[:200]
            res.confianza["referencia_externa"] = "media"

    valor, _ = _valor_de(lineas, "autorizacion")
    # «No precisa» indica que no hace falta (art. 6.e): no es un número de
    # autorización y no debe pasar al DeCA como tal.
    if valor and not _SIN_AUTORIZACION.match(_sin_tildes(valor).strip().lower()):
        res.datos["autorizacion_especial_circulacion"] = valor[:100]
        res.confianza["autorizacion_especial_circulacion"] = "media"

    obligatorios = {
        "cargador.nombre_razon_social": "nombre del cargador (Art. 6.a)",
        "cargador.nif_cif": "NIF del cargador (Art. 6.a)",
        "cargador.domicilio": "domicilio del cargador (Art. 6.a)",
        "transportista.nombre_razon_social": "nombre del transportista (Art. 6.b)",
        "transportista.nif_cif": "NIF del transportista (Art. 6.b)",
        "origen_direccion": "lugar de origen (Art. 6.c)",
        "destino_direccion": "lugar de destino (Art. 6.c)",
        "mercancia.naturaleza": "naturaleza de la mercancía (Art. 6.d)",
        "mercancia.peso_kg": "peso de la mercancía (Art. 6.d)",
        "fecha_transporte": "fecha del transporte (Art. 6.f)",
        "matricula_vehiculo": "matrícula del vehículo (Art. 6.g)",
    }
    for ruta, descripcion in obligatorios.items():
        grupo, _, sub = ruta.partition(".")
        presente = res.datos.get(grupo, {}).get(sub) if sub else res.datos.get(grupo)
        if not presente:
            res.avisos.append(f"No encontrado: {descripcion}")
    return res
