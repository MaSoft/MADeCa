"""Conector local: extrae en el equipo del cliente y solo envía datos."""

import io
import json
from pathlib import Path

import httpx
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from conector_local.conector import Configuracion, Estado, una_pasada
from tests.test_extraccion import ALBARAN


def _pdf(texto: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for linea in texto.splitlines():
        c.drawString(40, y, linea)
        y -= 14
    c.save()
    return buf.getvalue()


def _config(tmp_path: Path, monkeypatch, mover: bool) -> Configuracion:
    (tmp_path / "entrada").mkdir()
    ini = tmp_path / "conector.ini"
    ini.write_text(
        "[madeca]\nurl = https://deca.ejemplo.es\n"
        f"[carpetas]\nrutas = {tmp_path / 'entrada'}\n"
        + (f"mover_procesados_a = {tmp_path / 'enviados'}\n" if mover else "")
        + f"[estado]\nfichero = {tmp_path / 'estado.json'}\nregistro = {tmp_path / 'c.log'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MADECA_API_KEY", "mad_live_prueba")
    return Configuracion(ini)


def test_envia_solo_datos_y_no_repite(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, mover=False)
    (tmp_path / "entrada" / "alb.pdf").write_bytes(_pdf(ALBARAN))
    (tmp_path / "entrada" / "notas.docx").write_bytes(b"ignorar")
    recibidos = []

    def madeca(req: httpx.Request):
        assert req.url.path == "/api/v1/borradores" and req.headers["X-API-Key"] == "mad_live_prueba"
        recibidos.append(json.loads(req.content))
        return httpx.Response(201, json={"id": "B1", "estado": "pendiente", "creado": True})

    cliente = httpx.Client(transport=httpx.MockTransport(madeca))
    estado = Estado(config.estado)
    assert una_pasada(config, estado, cliente, motor=None) == 1
    cuerpo = recibidos[0]
    assert cuerpo["origen"] == "conector_local" and cuerpo["nombre_documento"] == "alb.pdf"
    assert cuerpo["datos"]["cargador"]["nif_cif"] == "B00345678"
    # Solo datos: ningún campo con el fichero.
    assert set(cuerpo) == {"referencia_fuente", "origen", "nombre_documento", "datos", "confianza", "avisos"}
    # Segunda pasada: ya enviado, no se repite (también tras reiniciar).
    assert una_pasada(config, Estado(config.estado), cliente, motor=None) == 0


def test_mueve_los_enviados_y_para_si_la_clave_es_rechazada(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, mover=True)
    (tmp_path / "entrada" / "a.pdf").write_bytes(_pdf(ALBARAN))
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(201, json={"id": "B"})))
    una_pasada(config, Estado(config.estado), ok, motor=None)
    assert (tmp_path / "enviados" / "a.pdf").exists() and not (tmp_path / "entrada" / "a.pdf").exists()

    (tmp_path / "entrada" / "b.pdf").write_bytes(_pdf(ALBARAN))
    rechazo = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "x"})))
    assert una_pasada(config, Estado(config.estado), rechazo, motor=None) == 0
    assert (tmp_path / "entrada" / "b.pdf").exists()  # no se pierde ni se mueve


def test_solo_https_salvo_el_propio_equipo(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setenv("MADECA_API_KEY", "mad_live_prueba")
    for url, valida in (("http://localhost:8000", True), ("http://127.0.0.1:8000", True),
                        ("http://deca.ejemplo.es", False), ("https://deca.ejemplo.es", True)):
        ini = tmp_path / "c.ini"
        ini.write_text(f"[madeca]\nurl = {url}\n[carpetas]\nrutas = {tmp_path}\n", encoding="utf-8")
        if valida:
            Configuracion(ini)
        else:
            with pytest.raises(SystemExit):
                Configuracion(ini)
