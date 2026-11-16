"""El camino passthrough (pypdf + reportlab) es puro Python y corre
igual en Windows que en Docker. El camino de generación usa WeasyPrint,
que necesita librerías nativas (Pango/Cairo/GDK-Pixbuf) — sus tests se
omiten automáticamente si no están disponibles (típico en Windows sin
GTK instalado; siempre disponibles en el contenedor Docker).
"""

import io
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.domain.schemas import DecaCreate
from app.services.pdf_generator import (
    MotorPdfNoDisponibleError,
    PdfInvalidoError,
    PdfPosibleEscaneoError,
    TAMANO_MAXIMO_BYTES,
    estampar_qr_en_pdf,
    generar_pdf_desde_datos,
    validar_pdf_origen,
)


def _pdf_con_texto(texto: str = "Albarán de prueba ALB-2026-001") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(50, 800, texto)
    c.drawString(50, 780, "Cargador: Transportes Ejemplo SL - NIF B00345678")
    c.save()
    buf.seek(0)
    return buf.getvalue()


def _pdf_solo_imagen() -> bytes:
    """Simula el patrón típico de un PDF resultante de escanear papel:
    una página sin texto extraíble que solo contiene una imagen."""
    imagen = Image.new("RGB", (200, 200), color="white")
    img_buf = io.BytesIO()
    imagen.save(img_buf, format="PNG")
    img_buf.seek(0)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawImage(ImageReader(img_buf), 0, 0, width=595, height=842)
    c.save()
    buf.seek(0)
    return buf.getvalue()


def test_estampar_qr_en_pdf_produce_pdf_valido():
    pdf_original = _pdf_con_texto()
    pdf_bytes, url = estampar_qr_en_pdf(
        pdf_original_bytes=pdf_original, expedicion_id=uuid.uuid4(), base_url="https://deca.test.local"
    )
    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) <= TAMANO_MAXIMO_BYTES
    assert "/inspect/" in url


def test_estampar_qr_no_toca_el_texto_original():
    pdf_original = _pdf_con_texto("Contenido original identificable ABC123")
    pdf_bytes, _url = estampar_qr_en_pdf(
        pdf_original_bytes=pdf_original, expedicion_id=uuid.uuid4(), base_url="https://deca.test.local"
    )
    from pypdf import PdfReader

    texto = PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()
    assert "ABC123" in texto


def test_estampar_qr_rechaza_bytes_que_no_son_pdf():
    with pytest.raises(PdfInvalidoError):
        estampar_qr_en_pdf(
            pdf_original_bytes=b"esto no es un pdf",
            expedicion_id=uuid.uuid4(),
            base_url="https://deca.test.local",
        )


def test_estampar_qr_rechaza_pdf_que_parece_escaneado():
    with pytest.raises(PdfPosibleEscaneoError):
        estampar_qr_en_pdf(
            pdf_original_bytes=_pdf_solo_imagen(),
            expedicion_id=uuid.uuid4(),
            base_url="https://deca.test.local",
        )


def test_validar_pdf_origen_acepta_pdf_con_texto():
    validar_pdf_origen(_pdf_con_texto())  # no debe lanzar


def test_validar_pdf_origen_rechaza_escaneado():
    with pytest.raises(PdfPosibleEscaneoError):
        validar_pdf_origen(_pdf_solo_imagen())


def _deca_ejemplo() -> DecaCreate:
    return DecaCreate.model_validate(
        {
            "referencia_externa": "ALB-2026-001",
            "cargador": {
                "nombre_razon_social": "Transportes Ejemplo SL",
                "nif_cif": "B00345678",
                "domicilio": "Calle Industria 14, Madrid",
            },
            "transportista": {
                "nombre_razon_social": "Transportista Efectivo SL",
                "nif_cif": "B00345678",
            },
            "origen_direccion": "Calle Industria 14, Madrid",
            "destino_direccion": "Polígono Sur, Sevilla",
            "mercancia": {"naturaleza": "Palés de material eléctrico", "peso_kg": 850.5},
            "fecha_transporte": datetime.now(timezone.utc).isoformat(),
            "matricula_vehiculo": "1234BBC",
        }
    )


def _generar_o_saltar(deca: DecaCreate) -> tuple[bytes, str]:
    try:
        return generar_pdf_desde_datos(deca=deca, expedicion_id=uuid.uuid4(), base_url="https://deca.test.local")
    except MotorPdfNoDisponibleError:
        pytest.skip(
            "WeasyPrint no dispone de sus librerías nativas (Pango/Cairo/"
            "GDK-Pixbuf) en este entorno — normal en Windows sin GTK; "
            "funciona en Docker."
        )


def test_generar_pdf_desde_datos_produce_pdf_valido():
    deca = _deca_ejemplo()
    pdf_bytes, url = _generar_o_saltar(deca)
    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) <= TAMANO_MAXIMO_BYTES
    assert "/inspect/" in url


def test_generar_pdf_desde_datos_incluye_los_datos_clave():
    from pypdf import PdfReader

    deca = _deca_ejemplo()
    pdf_bytes, _url = _generar_o_saltar(deca)
    texto = PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()
    assert "Transportes Ejemplo SL" in texto
    assert "B00345678" in texto
    assert "1234BBC" in texto


def test_estampado_conserva_fecha_creacion_y_fija_modificacion():
    # Apartado Segundo.1 BOE-A-2026-12784: fechas de creación y
    # modificación como metadatos del PDF.
    from pypdf import PdfReader, PdfWriter

    origen = PdfWriter(clone_from=PdfReader(io.BytesIO(_pdf_con_texto())))
    origen.add_metadata({"/CreationDate": "D:20260801090000+00'00'", "/Producer": "ERP de prueba"})
    buf = io.BytesIO()
    origen.write(buf)

    pdf_bytes, _url = estampar_qr_en_pdf(
        pdf_original_bytes=buf.getvalue(), expedicion_id=uuid.uuid4(), base_url="https://deca.example.es"
    )
    meta = PdfReader(io.BytesIO(pdf_bytes)).metadata
    assert meta["/CreationDate"] == "D:20260801090000+00'00'"
    assert meta["/Producer"] == "ERP de prueba"
    assert str(meta["/ModDate"]).startswith(f"D:{datetime.now(timezone.utc):%Y%m%d}")


def test_estampado_pone_fecha_creacion_si_el_original_no_la_tiene():
    from pypdf import PdfReader

    pdf_bytes, _url = estampar_qr_en_pdf(
        pdf_original_bytes=_pdf_con_texto(), expedicion_id=uuid.uuid4(), base_url="https://deca.example.es"
    )
    meta = PdfReader(io.BytesIO(pdf_bytes)).metadata
    assert "/CreationDate" in meta and "/ModDate" in meta


def test_pdf_generado_lleva_fechas_de_creacion_y_modificacion():
    # Mismo post-proceso que se aplica a la salida de WeasyPrint (que en
    # Windows sin GTK no se puede ejecutar en los tests).
    from pypdf import PdfReader

    from app.services.pdf_generator import _con_fechas_pdf

    ahora = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc)
    meta = PdfReader(io.BytesIO(_con_fechas_pdf(_pdf_con_texto(), ahora=ahora))).metadata
    assert meta["/ModDate"] == "D:20261009083000+00'00'"
    assert "/CreationDate" in meta
