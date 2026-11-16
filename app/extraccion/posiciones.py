"""Dónde está cada palabra del documento de origen, para señalar en la
revisión de dónde se leyó cada dato.

Formato guardado en el borrador (JSON): una lista de páginas
    {"palabras": [[x0, y0, x1, y1, "texto"], ...]}
con coordenadas relativas a la página (0-1, origen arriba a la izquierda),
así no dependen del tamaño al que se dibuje.

- Escaneos y fotos: las posiciones las da el propio OCR (Tesseract o
  Azure), en la misma lectura que el texto.
- PDF con texto: pypdfium2 (motor PDFium). Es opcional: sin él no hay
  posiciones de esos PDF y la revisión los muestra como siempre.
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime

DECIMALES = 4
MAX_PALABRAS_PAGINA = 4000
MAX_PAGINAS = 10


def _r(v: float) -> float:
    return round(min(max(v, 0.0), 1.0), DECIMALES)


def pagina_desde_tesseract(datos: dict, ancho: int, alto: int) -> tuple[dict, str]:
    """(página, texto) a partir de pytesseract.image_to_data(DICT). El texto
    se rehace como lo da image_to_string: una línea por línea y una en
    blanco entre párrafos (la extracción usa los bloques)."""
    palabras, lineas, actual = [], [], []
    linea_actual = parrafo_actual = None
    for i, texto in enumerate(datos.get("text", [])):
        linea = (datos["block_num"][i], datos["par_num"][i], datos["line_num"][i])
        if linea != linea_actual:
            if actual:
                lineas.append(" ".join(actual))
                actual = []
            if parrafo_actual is not None and linea[:2] != parrafo_actual and lineas and lineas[-1] != "":
                lineas.append("")
            linea_actual, parrafo_actual = linea, linea[:2]
        texto = (texto or "").strip()
        if not texto:
            continue
        actual.append(texto)
        x, y, w, h = datos["left"][i], datos["top"][i], datos["width"][i], datos["height"][i]
        if len(palabras) < MAX_PALABRAS_PAGINA:
            palabras.append([_r(x / ancho), _r(y / alto), _r((x + w) / ancho), _r((y + h) / alto), texto])
    if actual:
        lineas.append(" ".join(actual))
    return {"palabras": palabras}, "\n".join(lineas).strip("\n")


def paginas_desde_azure(resultado: dict) -> list[dict]:
    """De analyzeResult.pages[].words[] (polígono de 8 valores)."""
    paginas = []
    for pagina in (resultado.get("pages") or [])[:MAX_PAGINAS]:
        ancho, alto = pagina.get("width") or 1, pagina.get("height") or 1
        palabras = []
        for palabra in (pagina.get("words") or [])[:MAX_PALABRAS_PAGINA]:
            pol = palabra.get("polygon") or []
            if len(pol) < 8:
                continue
            xs, ys = pol[0::2], pol[1::2]
            palabras.append([_r(min(xs) / ancho), _r(min(ys) / alto), _r(max(xs) / ancho), _r(max(ys) / alto),
                             palabra.get("content", "")])
        paginas.append({"palabras": palabras})
    return paginas


def paginas_desde_pdf(contenido: bytes) -> list[dict] | None:
    """Palabras de un PDF con texto (pypdfium2). None si no está disponible
    o el PDF no se puede leer."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        documento = pdfium.PdfDocument(io.BytesIO(contenido))
    except Exception:
        return None
    paginas = []
    try:
        for n in range(min(len(documento), MAX_PAGINAS)):
            pagina = documento[n]
            ancho, alto = pagina.get_size()
            texto = pagina.get_textpage()
            palabras, letras, caja = [], [], None
            for i in range(texto.count_chars()):
                letra = texto.get_text_range(i, 1)
                if not letra or letra.isspace():
                    if letras:
                        palabras.append(caja + ["".join(letras)])
                    letras, caja = [], None
                    continue
                izq, abajo, der, arriba = texto.get_charbox(i)
                rect = [izq / ancho, 1 - arriba / alto, der / ancho, 1 - abajo / alto]
                caja = rect if caja is None else [min(caja[0], rect[0]), min(caja[1], rect[1]), max(caja[2], rect[2]), max(caja[3], rect[3])]
                letras.append(letra)
            if letras:
                palabras.append(caja + ["".join(letras)])
            paginas.append({"palabras": [[_r(a), _r(b), _r(c), _r(d), t] for a, b, c, d, t in palabras[:MAX_PALABRAS_PAGINA]]})
    finally:
        documento.close()
    return paginas


def _compacto(texto: str) -> str:
    """Solo letras y cifras, en minúscula y sin tildes: «R-5678-BBC» = «r5678bbc»."""
    sin_tildes = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[^0-9a-z]", "", sin_tildes.lower())


def variantes(valor) -> list[str]:
    """Formas en que un dato puede aparecer escrito en el documento."""
    if valor is None or valor == "":
        return []
    if isinstance(valor, bool):
        return []
    if isinstance(valor, (int, float)):
        numero = float(valor)
        entero = int(numero)
        formas = {f"{numero:.3f}".rstrip("0").rstrip("."), f"{entero:,}".replace(",", ".")}
        if numero != entero:
            decimales = f"{numero:.3f}".rstrip("0").split(".")[1]
            formas.add(f"{entero:,}".replace(",", ".") + "," + decimales)
        return sorted({_compacto(f) for f in formas if _compacto(f)}, key=len, reverse=True)
    texto = str(valor)
    try:
        fecha = datetime.fromisoformat(texto)
        return [_compacto(f"{fecha:%d/%m/%Y}"), _compacto(f"{fecha:%d/%m/%y}")]
    except ValueError:
        pass
    compacto = _compacto(texto)
    return [compacto] if len(compacto) >= 2 else []


def ubicar(paginas: list[dict] | None, valor) -> list[dict]:
    """Recuadro [{pagina, x0, y0, x1, y1}] de la primera aparición del dato
    como palabras seguidas (también si una palabra lleva pegada la unidad
    o la etiqueta: «1.250,5kg», «CIF:B00345678»). [] si no aparece."""
    if not paginas:
        return []

    def caja(n: int, palabras: list) -> list[dict]:
        return [{"pagina": n, "x0": min(c[0] for c in palabras), "y0": min(c[1] for c in palabras),
                 "x1": max(c[2] for c in palabras), "y1": max(c[3] for c in palabras)}]

    for buscado in variantes(valor):
        for n, pagina in enumerate(paginas):
            palabras = pagina.get("palabras") or []
            compactas = [_compacto(p[4]) for p in palabras]
            for i in range(len(palabras)):
                # Una sola palabra que contiene el dato con poco más alrededor.
                if buscado in compactas[i] and len(compactas[i]) - len(buscado) <= 6:
                    return caja(n, palabras[i:i + 1])
                acumulado = ""
                for j in range(i, min(i + 12, len(palabras))):
                    acumulado += compactas[j]
                    if acumulado == buscado or (acumulado.startswith(buscado) and len(acumulado) - len(buscado) <= 3):
                        return caja(n, palabras[i:j + 1])
                    if not buscado.startswith(acumulado):
                        break
    return []


LADO_MAXIMO_VISTA_PX = 1600


def imagen_pagina(contenido: bytes, nombre: str, origen: str, numero: int) -> bytes | None:
    """PNG de la página `numero` tal como se leyó (mismas páginas y
    proporciones que las posiciones guardadas). None si no existe."""
    from PIL import Image

    imagen = None
    if origen == "pdf":
        try:
            import pypdfium2 as pdfium
        except ImportError:
            return None
        documento = pdfium.PdfDocument(io.BytesIO(contenido))
        try:
            if not 0 <= numero < min(len(documento), MAX_PAGINAS):
                return None
            pagina = documento[numero]
            ancho_pt = pagina.get_size()[0] or 1
            imagen = pagina.render(scale=min(2.5, LADO_MAXIMO_VISTA_PX / ancho_pt)).to_pil()
        finally:
            documento.close()
    else:
        # Mismo recorrido que el OCR (app/extraccion/motores.py): la imagen
        # o, en un PDF escaneado, cada imagen de cada página.
        if nombre.lower().endswith(".pdf"):
            from pypdf import PdfReader

            imagenes = (img.image for pagina in PdfReader(io.BytesIO(contenido)).pages for img in pagina.images)
        else:
            imagenes = iter([Image.open(io.BytesIO(contenido))])
        for i, img in enumerate(imagenes):
            if i == numero:
                imagen = img
                break
            if i >= MAX_PAGINAS:
                break
    if imagen is None:
        return None
    if imagen.mode not in ("RGB", "L"):
        imagen = imagen.convert("RGB")
    if max(imagen.size) > LADO_MAXIMO_VISTA_PX:
        imagen = imagen.copy()
        imagen.thumbnail((LADO_MAXIMO_VISTA_PX, LADO_MAXIMO_VISTA_PX))
    salida = io.BytesIO()
    imagen.save(salida, "PNG", optimize=True)
    return salida.getvalue()


def marcas(posiciones: dict | None, datos: dict, rutas: dict[str, str]) -> dict[str, list[dict]]:
    """Recuadro de cada campo del formulario en el documento: {campo: [caja]}.
    `rutas`: campo del formulario -> ruta en los datos («cargador.nif_cif»)."""
    if not posiciones or not posiciones.get("paginas"):
        return {}
    salida = {}
    for campo, ruta in rutas.items():
        valor = datos
        for parte in ruta.split("."):
            valor = valor.get(parte) if isinstance(valor, dict) else None
        cajas = ubicar(posiciones["paginas"], valor)
        if cajas:
            salida[campo] = cajas
    return salida
