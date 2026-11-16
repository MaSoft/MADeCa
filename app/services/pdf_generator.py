"""PDF del DeCA: estampado del QR sobre el PDF del ERP (camino 1) y
redacción del documento a partir de datos (camino 2, al final del módulo).

En el camino 1 MADeCa no redacta el contenido del DeCA — lo genera el ERP
(albarán, factura o declaración de contenido). Legalmente es válido: el DeCA no es
un impreso oficial específico, puede ser cualquier documento del
operador siempre que contenga los datos del Art. 6 de la Orden
FOM/2861/2012 y sea un PDF nativo digital (Apartado Segundo.2,
BOE-A-2026-12784) — no un escaneo de papel.

El trabajo de este módulo es exclusivamente:
  1. Comprobar que el PDF recibido es válido y no tiene pinta de ser un
     escaneo (heurística, no una prueba formal — ver `_parece_escaneado`).
  2. Añadir el código QR de verificación a la primera página, sin tocar
     el resto del contenido.
  3. Comprobar que el resultado sigue por debajo de 5 MB (Apartado
     Segundo.1).

El estampado no usa WeasyPrint ni librerías nativas (Pango/Cairo/GDK-
Pixbuf) — pypdf y reportlab son puro Python. La redacción del camino 2 sí
usa WeasyPrint (incluido en la imagen Docker).
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from app.zonas import con_zona
from app.zonas import texto as texto_fecha

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

from app.domain.schemas import DecaCreate
from app.plantillas import decimal_es
from app.services.qr_service import construir_url_publica, generar_qr_data_uri, generar_qr_png_bytes

TAMANO_MAXIMO_BYTES = 5 * 1024 * 1024  # 5 MB, Apartado Segundo.1
UMBRAL_TEXTO_MINIMO = 20  # caracteres extraíbles por página para no considerarla "solo imagen"
# Las fechas que lee el conductor o el agente en el documento van en hora
# peninsular; los metadatos del PDF llevan su propio desfase horario.

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "pdf"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)
_env.filters["decimal"] = decimal_es


class PdfInvalidoError(Exception):
    pass


class PdfDemasiadoGrandeError(Exception):
    pass


class PdfPosibleEscaneoError(Exception):
    pass


class MotorPdfNoDisponibleError(Exception):
    pass


def _parece_escaneado(reader: PdfReader) -> bool:
    """Heurística, no una prueba formal: una página sin texto extraíble
    que solo contiene una imagen incrustada es el patrón típico de un PDF
    resultante de escanear papel. No detecta todos los casos (un escaneo
    con OCR sí tendría texto) ni descarta falsos positivos (un PDF nativo
    compuesto solo de una imagen vectorial grande también lo activaría),
    pero cubre el caso más común y da un error explicable en vez de
    aceptar en silencio algo que la norma prohíbe.
    """
    for page in reader.pages:
        texto = (page.extract_text() or "").strip()
        if len(texto) >= UMBRAL_TEXTO_MINIMO:
            continue
        try:
            tiene_imagen = any(True for _ in page.images)
        except Exception:
            tiene_imagen = False
        if tiene_imagen:
            return True
    return False


def _cargar_pdf(pdf_bytes: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise PdfInvalidoError(f"El fichero no es un PDF válido: {exc}") from exc
    if len(reader.pages) == 0:
        raise PdfInvalidoError("El PDF no contiene páginas")
    return reader


def _fecha_pdf(momento: datetime) -> str:
    """Formato de fecha de PDF (ISO 32000-1, 7.9.4): D:AAAAMMDDHHmmSS+HH'mm'."""
    utc = momento.astimezone(timezone.utc)
    return utc.strftime("D:%Y%m%d%H%M%S+00'00'")


def _aplicar_fechas_pdf(writer: PdfWriter, *, metadatos_previos: dict | None, ahora: datetime) -> None:
    """Apartado Segundo.1 BOE-A-2026-12784: la fecha y hora de creación y
    de modificación deben constar como metadatos del PDF.

    Se conservan los metadatos que trajera el documento (p. ej. el PDF del
    ERP) y su fecha de creación original; si no tenía, se toma `ahora`.
    La fecha de modificación siempre es `ahora`, porque estampar el QR (o
    redactar el documento) es la última modificación del fichero.
    """
    previos = {str(k): v for k, v in (metadatos_previos or {}).items()}
    previos.setdefault("/CreationDate", _fecha_pdf(ahora))
    previos["/ModDate"] = _fecha_pdf(ahora)
    writer.add_metadata(previos)


def _con_fechas_pdf(pdf_bytes: bytes, *, ahora: datetime) -> bytes:
    """Reescribe un PDF ya generado añadiendo las fechas de creación y
    modificación (WeasyPrint no las pone si la plantilla no las declara)."""
    reader = _cargar_pdf(pdf_bytes)
    writer = PdfWriter(clone_from=reader)
    _aplicar_fechas_pdf(writer, metadatos_previos=reader.metadata, ahora=ahora)
    salida = io.BytesIO()
    writer.write(salida)
    return salida.getvalue()


def validar_pdf_origen(pdf_bytes: bytes) -> None:
    """Validaciones aplicables sin conocer el contenido de negocio del
    documento (eso es responsabilidad del ERP de origen, MADeCa no hace
    parsing/OCR del contenido)."""
    reader = _cargar_pdf(pdf_bytes)
    if _parece_escaneado(reader):
        raise PdfPosibleEscaneoError(
            "El PDF parece resultado de escanear un documento en papel "
            "(página sin texto extraíble que solo contiene una imagen "
            "incrustada). El Apartado Segundo.2 de BOE-A-2026-12784 exige "
            "un PDF nativo digital generado directamente por el ERP, no "
            "un escaneo."
        )


def _generar_pagina_qr(*, ancho: float, alto: float, qr_png_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(ancho, alto))

    lado_qr = min(90.0, ancho * 0.22, alto * 0.22)
    margen = 18.0
    x = ancho - lado_qr - margen
    y = margen

    c.drawImage(
        ImageReader(io.BytesIO(qr_png_bytes)),
        x, y, width=lado_qr, height=lado_qr,
        preserveAspectRatio=True, mask="auto",
    )
    c.setFont("Helvetica", 5.5)
    c.drawRightString(x + lado_qr, max(y - 8, 2), "Verificacion DeCA")
    c.save()
    buf.seek(0)
    return buf.getvalue()


def estampar_qr_en_pdf(
    *,
    pdf_original_bytes: bytes,
    expedicion_id: uuid.UUID,
    base_url: str,
) -> tuple[bytes, str]:
    """Añade el QR de verificación a la primera página del PDF del ERP
    sin alterar el resto del documento. Devuelve (pdf_bytes, url_publica).

    El PDF resultante todavía NO está firmado — la firma AdES, si
    procede, se aplica después con app.services.signing sobre estos bytes.
    """
    reader = _cargar_pdf(pdf_original_bytes)
    if _parece_escaneado(reader):
        raise PdfPosibleEscaneoError(
            "El PDF parece resultado de escanear un documento en papel; "
            "no se puede emitir como DeCA (Apartado Segundo.2 BOE-A-2026-12784)."
        )

    url_publica = construir_url_publica(base_url, expedicion_id)
    qr_png = generar_qr_png_bytes(url_publica)

    primera_pagina_original = reader.pages[0]
    ancho = float(primera_pagina_original.mediabox.width)
    alto = float(primera_pagina_original.mediabox.height)
    pagina_qr_bytes = _generar_pagina_qr(ancho=ancho, alto=alto, qr_png_bytes=qr_png)
    overlay_reader = PdfReader(io.BytesIO(pagina_qr_bytes))

    # Las páginas se añaden al writer ANTES de fusionar el QR: fusionar
    # sobre una página todavía no asociada a ningún writer está
    # deprecado en pypdf (se eliminará en 7.0) y no es fiable.
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.pages[0].merge_page(overlay_reader.pages[0])
    # Un PdfWriter nuevo no hereda el diccionario /Info del original: sin
    # esto se perdían las fechas de creación/modificación del PDF del ERP.
    _aplicar_fechas_pdf(writer, metadatos_previos=reader.metadata, ahora=datetime.now(timezone.utc))

    salida = io.BytesIO()
    writer.write(salida)
    pdf_bytes = salida.getvalue()

    if len(pdf_bytes) > TAMANO_MAXIMO_BYTES:
        raise PdfDemasiadoGrandeError(
            f"El PDF resultante ({len(pdf_bytes)} bytes) supera el máximo "
            f"de {TAMANO_MAXIMO_BYTES} bytes exigido por el Apartado "
            "Segundo.1 de BOE-A-2026-12784"
        )

    return pdf_bytes, url_publica


# --- Modificación durante el servicio (Apartado Quinto, método 1) ---


def _generar_pagina_modificacion(
    *, ancho: float, alto: float, numero: int, momento: datetime, motivo: str, cambios: list[dict],
    zona: str | None = None,
) -> bytes:
    """Página que se añade al final del DeCA con los datos nuevos, el
    motivo y los datos anteriores marcados como no válidos."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(ancho, alto))
    margen = 40.0
    util = ancho - 2 * margen
    y = alto - margen

    def linea(texto: str, fuente: str = "Helvetica", tam: float = 9, sangria: float = 0) -> None:
        nonlocal y
        for trozo in simpleSplit(texto, fuente, tam, util - sangria) or [""]:
            if y < margen + tam:
                c.showPage()
                y = alto - margen
            c.setFont(fuente, tam)
            c.drawString(margen + sangria, y, trozo)
            y -= tam * 1.35

    hora = texto_fecha(momento, zona)
    linea(f"MODIFICACIÓN N.º {numero} DEL DOCUMENTO DE CONTROL", "Helvetica-Bold", 13)
    linea("Apartado Quinto, método 1, de la Resolución de 5 de junio de 2026 (BOE-A-2026-12784)", tam=8)
    y -= 6
    linea(f"Fecha y hora de la modificación: {hora}", "Helvetica-Bold", 10)
    linea("Motivo:", "Helvetica-Bold", 10)
    linea(motivo, tam=10, sangria=10)
    y -= 6
    linea("Datos modificados:", "Helvetica-Bold", 10)
    for cambio in cambios:
        linea(cambio["campo"], "Helvetica-Bold", 9, sangria=10)
        linea(f"Valor anterior (NO VÁLIDO): {cambio['anterior']}", tam=9, sangria=20)
        linea(f"Valor nuevo: {cambio['nuevo']}", "Helvetica-Bold", 9, sangria=20)
        y -= 3
    y -= 6
    linea(
        "Los valores anteriores indicados en esta página dejan de ser válidos desde la fecha y hora "
        "de la modificación. Prevalecen los valores nuevos sobre los que figuren en las páginas anteriores.",
        tam=8,
    )
    c.save()
    return buf.getvalue()


def anadir_pagina_modificacion(
    *, pdf_bytes: bytes, numero: int, motivo: str, cambios: list[dict], ahora: datetime,
    zona: str | None = None,
) -> bytes:
    """Añade la página de modificación al final del PDF vigente. La URL y
    el QR no cambian. Conserva la fecha de creación y fija la de
    modificación (Apartados Primero.2 y Segundo.1). Si el PDF estaba
    firmado, la firma anterior deja de cubrir el documento: el llamador
    debe volver a firmarlo."""
    reader = _cargar_pdf(pdf_bytes)
    primera = reader.pages[0]
    pagina = PdfReader(
        io.BytesIO(
            _generar_pagina_modificacion(
                ancho=float(primera.mediabox.width),
                alto=float(primera.mediabox.height),
                numero=numero,
                momento=ahora,
                motivo=motivo,
                cambios=cambios,
                zona=zona,
            )
        )
    )
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    for page in pagina.pages:
        writer.add_page(page)
    _aplicar_fechas_pdf(writer, metadatos_previos=reader.metadata, ahora=ahora)
    salida = io.BytesIO()
    writer.write(salida)
    resultado = salida.getvalue()
    if len(resultado) > TAMANO_MAXIMO_BYTES:
        raise PdfDemasiadoGrandeError(
            f"Con la modificación el PDF ({len(resultado)} bytes) superaría el máximo de "
            f"{TAMANO_MAXIMO_BYTES} bytes (Apartado Segundo.1). Usa el método 2: emitir un DeCA nuevo."
        )
    return resultado


# --- Camino 2: MADeCa redacta el PDF desde datos estructurados (el ERP
# no genera uno propio) — plantilla HTML original vía WeasyPrint. ---


def generar_pdf_desde_datos(
    *,
    deca: DecaCreate,
    expedicion_id: uuid.UUID,
    base_url: str,
) -> tuple[bytes, str]:
    """MADeCa redacta el PDF completo a partir de datos estructurados,
    para ERPs que no generan su propio documento. Devuelve
    (pdf_bytes, url_publica). Todavía sin firmar — si finalidad_contractual
    es True, la firma se aplica después con app.services.signing; para
    entonces este PDF nunca llega a persistirse si la firma fallara, así
    que el sello visual "FIRMADO" que se pinta aquí es fiable.
    """
    url_publica = construir_url_publica(base_url, expedicion_id)
    qr_data_uri = generar_qr_data_uri(url_publica)
    ahora = datetime.now(timezone.utc)

    # Las fechas se imprimen en la zona del propio transporte y se indica
    # cuál: un envío canario no puede salir con la hora peninsular.
    fecha_transporte, zona = con_zona(deca.fecha_transporte, deca.zona_horaria)

    template = _env.get_template("deca_pdf.html")
    html_str = template.render(
        deca=deca,
        expedicion_id=str(expedicion_id),
        # Antes se imprimía aquí la fecha del transporte: la de emisión es
        # la de creación del documento (Apartado Primero.2).
        fecha_emision=texto_fecha(ahora, zona),
        fecha_transporte=texto_fecha(fecha_transporte, zona),
        qr_data_uri=qr_data_uri,
        url_publica=url_publica,
        firmado=deca.finalidad_contractual,
    )

    try:
        from weasyprint import HTML  # import perezoso: aísla el fallo si
        # faltan las librerías nativas (Pango/Cairo/GDK-Pixbuf) para que
        # el resto de la aplicación pueda arrancar igualmente.
    except OSError as exc:
        raise MotorPdfNoDisponibleError(
            "WeasyPrint no puede cargar sus librerías nativas (Pango/Cairo/"
            "GDK-Pixbuf). En Windows instala el runtime GTK o usa Docker "
            f"(ver Dockerfile). Detalle: {exc}"
        ) from exc

    pdf_weasy = HTML(string=html_str, base_url=str(_TEMPLATES_DIR)).write_pdf()
    pdf_bytes = _con_fechas_pdf(pdf_weasy, ahora=ahora)

    if len(pdf_bytes) > TAMANO_MAXIMO_BYTES:
        raise PdfDemasiadoGrandeError(
            f"El PDF generado ({len(pdf_bytes)} bytes) supera el máximo de "
            f"{TAMANO_MAXIMO_BYTES} bytes exigido por el Apartado Segundo.1 "
            "de BOE-A-2026-12784"
        )

    return pdf_bytes, url_publica
