"""Extracción de los datos del Art. 6 a partir de texto de documentos de
origen. Sin OCR real: se prueba con el texto ya obtenido."""

import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.extraccion import ExtraccionError, extraer_campos, obtener_texto

ALBARAN = """
TRANSPORTES DEL SUR SL - ALBARÁN DE ENTREGA
Albarán nº: ALB-2026-0457
Fecha de carga: 08/10/2026 07:30
Cargador: Distribuciones Ejemplo SL   CIF: B00345678
Calle Industria 14, 28001 Madrid
Transportista: Transportista Efectivo SL
NIF: B-0034567-8
Lugar de carga: Polígono Norte, nave 3, Getafe
Lugar de entrega: Polígono Sur, Sevilla
Destinatario: Cliente Final SA  A00818500
Mercancía: Palés de material eléctrico
Bultos: 12
Peso bruto: 1.250,5 kg
Matrícula: 1234-BCD   Remolque: R-5678-BBC
"""


def test_albaran_completo():
    res = extraer_campos(ALBARAN)
    d = res.datos
    assert d["referencia_externa"] == "ALB-2026-0457"
    assert d["fecha_transporte"] == "2026-10-08T07:30:00"
    assert d["cargador"]["nombre_razon_social"] == "Distribuciones Ejemplo SL"
    assert d["cargador"]["nif_cif"] == "B00345678"
    assert d["cargador"]["domicilio"].startswith("Calle Industria 14")
    assert d["transportista"]["nif_cif"] == "B00345678"
    assert d["destinatario"]["nif_cif"] == "A00818500"
    assert d["origen_direccion"].startswith("Polígono Norte")
    assert d["destino_direccion"] == "Polígono Sur, Sevilla"
    assert d["mercancia"]["naturaleza"] == "Palés de material eléctrico"
    assert d["mercancia"]["peso_kg"] == 1250.5
    assert d["mercancia"]["bultos"] == 12
    assert d["matricula_vehiculo"] == "1234BCD"
    assert d["matricula_remolque"] == "R5678BBC"
    assert res.confianza["cargador.nif_cif"] == "alta"
    assert res.avisos == []


def test_etiqueta_y_valor_en_lineas_distintas_y_toneladas():
    texto = "CARTA DE PORTE\nPeso\n12,5 t\nOrigen:\nPuerto de Valencia\n"
    res = extraer_campos(texto)
    assert res.datos["mercancia"]["peso_kg"] == 12500.0
    assert res.datos["origen_direccion"] == "Puerto de Valencia"


def test_no_inventa_lo_que_no_esta():
    res = extraer_campos("Documento sin datos útiles")
    assert "cargador" not in res.datos
    assert "matricula_vehiculo" not in res.datos
    assert any("NIF del cargador" in a for a in res.avisos)


def test_nif_con_digito_incorrecto_no_se_acepta():
    res = extraer_campos("Cargador: Empresa Falsa SL CIF B00000001\nCalle Mayor 1, Madrid")
    assert "nif_cif" not in res.datos.get("cargador", {})


def _pdf(texto: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for linea in texto.splitlines():
        c.drawString(40, y, linea)
        y -= 14
    c.save()
    return buf.getvalue()


def test_pdf_nativo_sin_ocr():
    doc = obtener_texto(_pdf(ALBARAN), "albaran.pdf")
    assert doc.uso_ocr is False
    assert extraer_campos(doc.texto).datos["cargador"]["nif_cif"] == "B00345678"


def test_imagen_sin_motor_da_error_claro():
    with pytest.raises(ExtraccionError, match="OCR"):
        obtener_texto(b"\x89PNG...", "foto.png")


def test_extension_no_soportada():
    with pytest.raises(ExtraccionError):
        obtener_texto(b"x", "hoja.xlsx")


def test_nif_de_otra_parte_no_se_asigna_al_cargador():
    texto = "Remitente: ACME SL\nCalle Mayor 1, Zaragoza\nTransportista: TRANS SL B00345678\n"
    res = extraer_campos(texto)
    assert "nif_cif" not in res.datos["cargador"]
    assert res.datos["transportista"]["nif_cif"] == "B00345678"


def test_motor_azure_flujo_analisis_y_consulta():
    import httpx

    from app.extraccion.motores import MotorAzure

    llamadas = []

    def responder(request: httpx.Request) -> httpx.Response:
        llamadas.append((request.method, str(request.url)))
        assert request.headers["Ocp-Apim-Subscription-Key"] == "clave"
        if request.method == "POST":
            assert "prebuilt-read:analyze" in str(request.url) and "api-version=2024-11-30" in str(request.url)
            return httpx.Response(202, headers={"Operation-Location": "https://x.cognitiveservices.azure.com/op/1", "Retry-After": "0"})
        return httpx.Response(200, json={"status": "succeeded", "analyzeResult": {"content": "Cargador: ACME"}})

    motor = MotorAzure("https://x.cognitiveservices.azure.com", "clave", cliente=httpx.Client(transport=httpx.MockTransport(responder)))
    assert motor.reconocer(b"%PDF", "pdf") == "Cargador: ACME"
    assert [m for m, _ in llamadas] == ["POST", "GET"]


def test_titulo_albaran_de_entrega_no_es_la_referencia():
    # Texto real del OCR (Tesseract) del escaneo de la demo: el título
    # «ALBARÁN DE ENTREGA» daba la referencia «DE» y «nº» se lee «n*».
    texto = (
        "ALBARÁN DE ENTREGA (documento de demostración)\n"
        "Albarán n*: ALB-DEMO-002\n"
        "Fecha de carga: 10/10/2026 08:00\n"
    )
    assert extraer_campos(texto).datos["referencia_externa"] == "ALB-DEMO-002"


def test_sin_referencia_con_digitos_no_inventa_una():
    assert "referencia_externa" not in extraer_campos("ALBARÁN DE ENTREGA\nPedido: urgente\n").datos
