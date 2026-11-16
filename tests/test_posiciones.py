"""Dónde se leyó cada dato: posiciones, localización y páginas."""

from pathlib import Path

import pytest

from app.extraccion import obtener_texto
from app.extraccion.posiciones import imagen_pagina, marcas, paginas_desde_azure, ubicar, variantes

PDF = Path("demo/documentos/01_albaran_erp.pdf").read_bytes()
pdfium = pytest.importorskip("pypdfium2")


def _pagina(*palabras):
    return [{"palabras": [[0.1 * i, 0.1, 0.1 * i + 0.08, 0.12, p] for i, p in enumerate(palabras)]}]


def test_variantes_de_como_se_escribe_un_dato():
    assert variantes(1250.5)[0] == "12505"  # «1.250,5»
    assert variantes("2026-10-10T08:00:00+02:00") == ["10102026", "101026"]
    assert variantes("R-5678-BBC") == ["r5678bbc"]
    assert variantes(None) == [] and variantes(True) == []


def test_ubicar_palabras_seguidas_unidad_y_etiqueta_pegadas():
    paginas = _pagina("Peso:", "1.250,5kg", "Matrícula:", "R-5678-BBC", "CIF:B00345678", "Distribuciones", "Demo", "SL")
    assert ubicar(paginas, 1250.5)[0]["x0"] == pytest.approx(0.1)
    assert ubicar(paginas, "R5678BBC")[0]["x0"] == pytest.approx(0.3)
    assert ubicar(paginas, "B00345678")[0]["x0"] == pytest.approx(0.4)
    caja = ubicar(paginas, "Distribuciones Demo SL")[0]
    assert caja["x0"] == pytest.approx(0.5) and caja["x1"] == pytest.approx(0.78)
    assert ubicar(paginas, "No está") == [] and ubicar(None, "x") == []


def test_azure_poligonos_a_cajas_relativas():
    paginas = paginas_desde_azure({"pages": [{"width": 8.5, "height": 11, "words": [
        {"content": "ALB-1", "polygon": [0.85, 1.1, 1.7, 1.1, 1.7, 2.2, 0.85, 2.2]}]}]})
    assert paginas == [{"palabras": [[0.1, 0.1, 0.2, 0.2, "ALB-1"]]}]


def test_pdf_con_texto_guarda_posiciones_y_localiza_los_datos():
    texto = obtener_texto(PDF, "albaran.pdf")
    assert texto.origen_paginas == "pdf" and len(texto.paginas) == 1
    datos = {"cargador": {"nif_cif": "B00345678"}, "matricula_remolque": "R5678BBC", "mercancia": {"peso_kg": 1250.5}}
    m = marcas({"origen": "pdf", "paginas": texto.paginas}, datos,
               {"cargador_nif": "cargador.nif_cif", "matricula_remolque": "matricula_remolque", "mercancia_peso_kg": "mercancia.peso_kg",
                "destinatario_nif": "destinatario.nif_cif"})
    assert set(m) == {"cargador_nif", "matricula_remolque", "mercancia_peso_kg"}
    assert all(0 <= c["x0"] < c["x1"] <= 1 and 0 <= c["y0"] < c["y1"] <= 1 for cajas in m.values() for c in cajas)


def test_imagen_de_cada_pagina():
    png = imagen_pagina(PDF, "albaran.pdf", "pdf", 0)
    assert png.startswith(b"\x89PNG")
    assert imagen_pagina(PDF, "albaran.pdf", "pdf", 3) is None
