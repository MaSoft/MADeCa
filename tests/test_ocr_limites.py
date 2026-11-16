"""El OCR tiene límite de tiempo, de hilos y de simultaneidad: sin ellos
una foto difícil dejaba MADeCa sin responder minutos (también el visor
público del QR). Tesseract se simula: no se lanza ningún proceso."""

import io
import os

import pytest
import pytesseract
from PIL import Image

from app.extraccion import ExtraccionError, obtener_texto
from app.extraccion.motores import MotorTesseract, OcrTiempoAgotadoError


def _datos(*palabras):
    """Salida de image_to_data(DICT) con palabras en una línea."""
    n = len(palabras)
    return {"text": list(palabras), "block_num": [1] * n, "par_num": [1] * n, "line_num": [1] * n,
            "left": [10 + 60 * i for i in range(n)], "top": [20] * n, "width": [50] * n, "height": [12] * n}


def _png(lado=(400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", lado, "white").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def motor(monkeypatch):
    monkeypatch.setattr(pytesseract, "get_tesseract_version", lambda: "5.0")
    monkeypatch.delenv("OMP_THREAD_LIMIT", raising=False)
    return MotorTesseract(limite_segundos=7)


def test_un_hilo_por_ocr(motor):
    assert os.environ["OMP_THREAD_LIMIT"] == "1"


def test_se_pasa_el_limite_de_tiempo(motor, monkeypatch):
    recibido = {}

    def falso(img, lang, timeout, output_type):
        recibido["timeout"] = timeout
        return _datos("ALBARAN", "123")

    monkeypatch.setattr(pytesseract, "image_to_data", falso)
    assert motor.reconocer(_png(), "imagen") == "ALBARAN 123"
    assert recibido["timeout"] == 7
    # La misma lectura da dónde está cada palabra (relativo a la página).
    texto, paginas = motor.reconocer_con_posiciones(_png(), "imagen")
    assert paginas == [{"palabras": [[0.025, 0.0667, 0.15, 0.1067, "ALBARAN"], [0.175, 0.0667, 0.3, 0.1067, "123"]]}]


def test_tiempo_agotado_da_aviso_y_no_bloquea(motor, monkeypatch):
    def lento(img, lang, timeout, output_type):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(pytesseract, "image_to_data", lento)
    with pytest.raises(OcrTiempoAgotadoError):
        motor.reconocer(_png(), "imagen")
    # Se libera el turno aunque falle.
    assert motor._turnos.acquire(timeout=0.1)
    motor._turnos.release()
    # En la bandeja llega como aviso legible, no como error del servidor.
    with pytest.raises(ExtraccionError, match="revisa el documento a mano"):
        obtener_texto(_png(), "foto.png", motor)


def test_imagen_enorme_se_reduce(motor, monkeypatch):
    tamanos = []
    monkeypatch.setattr(pytesseract, "image_to_data", lambda img, lang, timeout, output_type: tamanos.append(img.size) or _datos())
    motor.reconocer(_png((8000, 6000)), "imagen")
    assert max(tamanos[0]) <= MotorTesseract.LADO_MAXIMO_PX


def test_texto_por_lineas_y_parrafos_como_image_to_string():
    from app.extraccion.posiciones import pagina_desde_tesseract

    datos = {"text": ["", "Cargador:", "ACME", "", "NIF", "B00345678", "Destino"],
             "block_num": [1, 1, 1, 1, 1, 1, 2], "par_num": [1, 1, 1, 1, 1, 1, 1], "line_num": [0, 1, 1, 2, 2, 2, 1],
             "left": [0] * 7, "top": [0] * 7, "width": [1] * 7, "height": [1] * 7}
    pagina, texto = pagina_desde_tesseract(datos, 100, 100)
    assert texto == "Cargador: ACME\nNIF B00345678\n\nDestino"
    assert [p[4] for p in pagina["palabras"]] == ["Cargador:", "ACME", "NIF", "B00345678", "Destino"]
