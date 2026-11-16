import uuid

import pytest

from app.services.qr_service import construir_url_publica, generar_qr_data_uri, generar_qr_png_bytes


def test_construir_url_publica_https():
    exp_id = uuid.uuid4()
    url = construir_url_publica("https://deca.ejemplo.es/", exp_id)
    assert url == f"https://deca.ejemplo.es/inspect/{exp_id}"


def test_construir_url_publica_rechaza_http_en_produccion():
    exp_id = uuid.uuid4()
    with pytest.raises(ValueError):
        construir_url_publica("http://deca.ejemplo.es", exp_id)


def test_construir_url_publica_permite_http_localhost_para_desarrollo():
    exp_id = uuid.uuid4()
    url = construir_url_publica("http://localhost:8000", exp_id)
    assert url.startswith("http://localhost:8000/inspect/")


def test_generar_qr_png_bytes_produce_png_valido():
    png = generar_qr_png_bytes("https://deca.ejemplo.es/inspect/abc")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_generar_qr_data_uri():
    data_uri = generar_qr_data_uri("https://deca.ejemplo.es/inspect/abc")
    assert data_uri.startswith("data:image/png;base64,")
