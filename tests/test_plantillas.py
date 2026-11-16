"""Filtros de fecha del panel: hora peninsular, fecha y hora separadas."""

from datetime import datetime, timezone

from app.plantillas import crear_plantillas, fecha, hora


def test_utc_se_muestra_en_hora_peninsular():
    verano = datetime(2026, 8, 10, 7, 30, tzinfo=timezone.utc)   # CEST, +2
    invierno = datetime(2026, 12, 10, 7, 30, tzinfo=timezone.utc)  # CET, +1
    assert (fecha(verano), hora(verano)) == ("10/08/2026", "09:30")
    assert hora(invierno) == "08:30"
    assert fecha(None) == "—" and fecha(None, "nunca") == "nunca" and hora(None) == ""


def test_formularios_de_rol_sin_javascript_en_linea():
    plantillas = crear_plantillas()
    for nombre in ("dashboard/usuarios.html", "superadmin/empresa_usuarios.html"):
        fuente = plantillas.env.loader.get_source(plantillas.env, nombre)[0]
        assert "onchange" not in fuente and "Cambiar</button>" in fuente


def test_decimal_con_coma_y_sin_ceros_sobrantes():
    from app.plantillas import decimal_es

    assert decimal_es(1250.5) == "1250,5"
    assert decimal_es(1250.0) == "1250"
    assert decimal_es("0.125") == "0,125"
    assert decimal_es(None) == ""


def test_estaticos_con_huella_de_contenido():
    """Página nueva + estilos viejos en caché = pantalla rota: las URL de
    CSS y JS llevan la huella de su contenido."""
    from app.plantillas import estatico

    url = estatico("css/dashboard.css")
    assert url.startswith("/static/css/dashboard.css?v=") and len(url.split("v=")[1]) == 12
    plantillas = crear_plantillas()
    for nombre in ("base.html", "superadmin/base.html"):
        fuente = plantillas.env.loader.get_source(plantillas.env, nombre)[0]
        assert '"/static/' not in fuente
