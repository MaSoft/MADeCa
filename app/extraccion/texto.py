"""Texto de un documento de origen: directo si el PDF es nativo, OCR si
es un escaneo o una imagen."""

from __future__ import annotations

import io
from dataclasses import dataclass

from app.extraccion.motores import MotorNoDisponibleError, MotorOCR

EXTENSIONES_IMAGEN = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")
EXTENSIONES_SOPORTADAS = (".pdf", ".txt", *EXTENSIONES_IMAGEN)
# Por debajo de esto se considera que el PDF no tiene capa de texto útil.
_MIN_CARACTERES_TEXTO = 40


class ExtraccionError(Exception):
    pass


@dataclass
class TextoDocumento:
    texto: str
    uso_ocr: bool
    motor: str | None = None
    # Posición de cada palabra (app/extraccion/posiciones.py) y de dónde
    # salen las páginas: "pdf" (páginas del PDF) u "ocr" (imágenes leídas).
    paginas: list[dict] | None = None
    origen_paginas: str | None = None


def obtener_texto(contenido: bytes, nombre: str, motor: MotorOCR | None = None) -> TextoDocumento:
    ext = ("." + nombre.rsplit(".", 1)[-1].lower()) if "." in nombre else ""
    if ext not in EXTENSIONES_SOPORTADAS:
        raise ExtraccionError(f"Tipo de fichero no soportado: {nombre}")

    if ext == ".txt":
        return TextoDocumento(contenido.decode("utf-8", errors="replace"), uso_ocr=False)

    if ext == ".pdf":
        from pypdf import PdfReader

        try:
            lector = PdfReader(io.BytesIO(contenido))
            texto = "\n".join((p.extract_text() or "") for p in lector.pages)
        except Exception as exc:
            raise ExtraccionError(f"PDF ilegible: {exc}") from exc
        if len(texto.strip()) >= _MIN_CARACTERES_TEXTO:
            from app.extraccion.posiciones import paginas_desde_pdf

            paginas = paginas_desde_pdf(contenido)
            return TextoDocumento(texto, uso_ocr=False, paginas=paginas, origen_paginas="pdf" if paginas else None)
        tipo = "pdf"
    else:
        tipo = "imagen"

    if motor is None:
        raise ExtraccionError(
            "El documento no tiene texto (escaneo o imagen) y no hay ningún motor de OCR configurado"
        )
    try:
        try:
            texto, paginas = motor.reconocer_con_posiciones(contenido, tipo)
        except (NotImplementedError, AttributeError):
            texto, paginas = motor.reconocer(contenido, tipo), None  # motor sin posiciones
        return TextoDocumento(texto, uso_ocr=True, motor=motor.nombre, paginas=paginas,
                              origen_paginas="ocr" if paginas else None)
    except MotorNoDisponibleError as exc:
        raise ExtraccionError(str(exc)) from exc
    except Exception as exc:
        # Error HTTP o de red de Azure, imagen dañada, fallo de Tesseract...:
        # antes se escapaban, la subida acababa en error 500 y el documento
        # se perdía (y el conector local se detenía). Ahora el borrador se
        # crea con el aviso, como promete procesar_documento.
        raise ExtraccionError(f"la lectura automática falló ({type(exc).__name__})") from exc
