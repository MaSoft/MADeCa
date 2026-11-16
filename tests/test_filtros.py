"""Filtros de las listas del panel: periodos, búsqueda y plantillas."""

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.models import Expedicion
from app.filtros import PERIODOS, buscar_en, rango_fechas, texto_busqueda
from app.plantillas import crear_plantillas

MADRID = ZoneInfo("Europe/Madrid")
HOY = date(2026, 3, 15)


def _dia(anio, mes, dia):
    return datetime(anio, mes, dia, tzinfo=MADRID)


def test_periodos_en_hora_local():
    assert rango_fechas("hoy", None, None, HOY)[:2] == (_dia(2026, 3, 15), _dia(2026, 3, 16))
    assert rango_fechas("7d", None, None, HOY)[:2] == (_dia(2026, 3, 9), _dia(2026, 3, 16))
    assert rango_fechas("mes", None, None, HOY)[:2] == (_dia(2026, 3, 1), _dia(2026, 4, 1))
    assert rango_fechas("mes_anterior", None, None, HOY)[:2] == (_dia(2026, 2, 1), _dia(2026, 3, 1))
    assert rango_fechas("mes", None, None, date(2026, 12, 31))[:2] == (_dia(2026, 12, 1), _dia(2027, 1, 1))
    assert rango_fechas("anio", None, None, HOY)[:2] == (_dia(2026, 1, 1), _dia(2027, 1, 1))
    assert rango_fechas("", None, None, HOY) == (None, None, None)
    assert rango_fechas("cualquier_cosa", None, None, HOY) == (None, None, None)


def test_hasta_incluye_el_dia_entero_y_las_fechas_mandan():
    inicio, fin, error = rango_fechas("hoy", "2026-01-10", "2026-01-20", HOY)
    assert (inicio, fin, error) == (_dia(2026, 1, 10), _dia(2026, 1, 21), None)
    # Las 23:30 del día «hasta» entran; las 00:00 del siguiente no.
    assert datetime(2026, 1, 20, 22, 30, tzinfo=timezone.utc) < fin
    assert rango_fechas("", "", "2026-01-20", HOY)[:2] == (None, _dia(2026, 1, 21))


def test_fechas_no_validas_o_al_reves_avisan():
    assert rango_fechas("", "2026-02-30", None, HOY)[2]
    assert rango_fechas("", "basura", None, HOY)[2]
    inicio, fin, error = rango_fechas("", "2026-05-01", "2026-04-01", HOY)
    assert inicio is None and fin is None and "posterior" in error


def test_texto_busqueda_limpio_y_acotado():
    assert texto_busqueda("  ABC   123 ") == "ABC 123"
    assert texto_busqueda(None) == ""
    assert len(texto_busqueda("x" * 500)) == 100


def test_busqueda_sin_comodines_y_en_datos_del_deca():
    condicion = buscar_en("50%_x", [Expedicion.referencia_externa], Expedicion.datos_deca)
    sql = str(condicion.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "ILIKE" in sql and "ESCAPE" in sql
    assert "ESCAPE '/'" in sql and "/_x" in sql  # % y _ escapados: se buscan tal cual
    assert "nombre_razon_social" in sql and "destino_direccion" in sql
    assert buscar_en("", [Expedicion.referencia_externa]) is None


class _Lista(list):
    def all(self):
        return list(self)


class _Res:
    def __init__(self, valor):
        self.valor = valor

    def all(self):
        return self.valor

    def scalars(self):
        return _Lista(self.valor if isinstance(self.valor, list) else [])

    def scalar_one(self):
        return self.valor


class _BDListas:
    """Doble de BD para las pantallas de lista: responde según la consulta."""

    def __init__(self):
        ahora = datetime.now(timezone.utc)
        self.expediciones = [
            Expedicion(id=uuid.uuid4(), referencia_externa=f"ALB-{i:04d}", erp_origen="odoo", estado=estado,
                       matricula_vehiculo="1234BCD", fecha_transporte=ahora, fecha_emision=ahora,
                       url_activa=True, firmado=i % 2 == 0, fecha_finalizacion_servicio=None)
            for i, estado in enumerate(("en_transito", "entregado", "anulado"))
        ]
        self.consultas: list[str] = []

    def execute(self, consulta):
        texto = str(consulta)
        self.consultas.append(texto)
        if "FROM accesos_log" in texto:
            return _Res([])
        if "GROUP BY expediciones.estado" in texto:
            return _Res([("en_transito", 1), ("entregado", 1), ("anulado", 1)])
        if "GROUP BY borradores_deca.estado" in texto:
            return _Res([("pendiente", 0), ("emitido", 0)])
        if "count(" in texto:
            return _Res(0)
        if "DISTINCT" in texto:
            return _Res(["odoo"])
        if texto.startswith("SELECT expediciones.id, expediciones.tenant_id"):
            return _Res(list(self.expediciones))
        return _Res([])

    def get(self, modelo, id_):
        return SimpleNamespace(ocr_tercero_autorizado=False)


@pytest.fixture
def cliente_listas():
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.security.dependencies import SesionUsuario, obtener_usuario_actual

    bd = _BDListas()
    app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), uuid.uuid4(), "operador")
    app.dependency_overrides[get_db] = lambda: bd
    try:
        yield TestClient(app), bd
    finally:
        app.dependency_overrides.clear()


def test_expediciones_casillas_de_estado_y_fichas(cliente_listas):
    cliente, bd = cliente_listas
    html = cliente.get("/dashboard/expediciones").text
    for estado in ("en_transito", "entregado", "anulado"):
        assert f'name="estado" value="{estado}" checked' in html
    assert "ALB-0001" in html and "Limpiar filtros" not in html

    # Desmarcar «Entregados» (el formulario manda f=1 y los estados marcados).
    html = cliente.get("/dashboard/expediciones?f=1&estado=en_transito&estado=anulado&buscar=50%25&periodo=mes").text
    assert 'name="estado" value="entregado" checked' not in html
    assert any("expediciones.estado IN" in c for c in bd.consultas)
    assert 'data-quitar="buscar"' in html and 'data-quitar="periodo desde hasta"' in html
    assert "Transporte: Este mes" in html and "Limpiar filtros" in html

    # Ninguno marcado: aviso propio, sin consultar la lista.
    html = cliente.get("/dashboard/expediciones?f=1").text
    assert "No hay ningún estado marcado" in html
    # Enlaces anteriores (?ocultar_entregados=1) siguen funcionando.
    html = cliente.get("/dashboard/expediciones?ocultar_entregados=1").text
    assert 'name="estado" value="entregado" checked' not in html


def test_expediciones_orden_por_cabecera(cliente_listas):
    cliente, _ = cliente_listas
    html = cliente.get("/dashboard/expediciones?orden=referencia").text
    assert 'class="ordenar activo asc" data-orden="referencia_desc"' in html
    html = cliente.get("/dashboard/expediciones?orden=referencia_desc").text
    assert 'class="ordenar activo desc" data-orden="emision_desc"' in html
    assert cliente.get("/dashboard/expediciones?orden=inventado").status_code == 200


def test_bandeja_pestanas_y_filtros_por_estado(cliente_listas):
    cliente, _ = cliente_listas
    html = cliente.get("/dashboard/bandeja").text
    assert 'name="estado" value="pendiente" checked' in html
    assert "No hay nada pendiente de revisar" in html
    html = cliente.get("/dashboard/bandeja?estado=emitido&ocultar_entregados=1&con_avisos=1").text
    assert "Sin entregados" in html and "Con datos que faltan" in html
    # En pendientes «ocultar entregados» no aplica y no se muestra como filtro.
    html = cliente.get("/dashboard/bandeja?estado=pendiente&ocultar_entregados=1").text
    assert "Sin entregados" not in html


def test_filtros_sin_javascript_en_linea():
    """La CSP del panel no admite JavaScript en línea ni eval (hx-on)."""
    plantillas = crear_plantillas()
    for nombre in ("dashboard/expediciones_list.html", "dashboard/_tabla_expediciones.html", "dashboard/bandeja.html",
                   "dashboard/_filtros.html", "dashboard/certificados.html", "dashboard/usuarios.html"):
        fuente = plantillas.env.loader.get_source(plantillas.env, nombre)[0]
        assert "hx-on" not in fuente and "onchange" not in fuente and "onclick" not in fuente and "<script" not in fuente


def test_certificados_y_usuarios_se_dibujan():
    sesion = SimpleNamespace(rol="admin", usuario_id=1)
    plantillas = crear_plantillas()
    html = plantillas.env.get_template("dashboard/certificados.html").render(
        sesion=sesion, certs=[], error=None, ahora=datetime.now(timezone.utc),
        filtros={"buscar": "", "ocultar_desactivados": False, "caducidad": "caducados"})
    assert "No hay certificados que coincidan" in html
    html = plantillas.env.get_template("dashboard/usuarios.html").render(
        sesion=sesion, usuarios=[], error=None, roles_validos=("admin",),
        filtros={"buscar": "x", "rol": "", "ocultar_desactivados": False})
    assert "No hay usuarios que coincidan" in html


def test_resumen_y_orden_de_cabecera():
    from app.filtros import resumen_periodo, siguiente_orden

    assert resumen_periodo("mes", "", "") == "Este mes"
    assert resumen_periodo("rango", "2026-10-01", "2026-10-11") == "01/10/2026 – 11/10/2026"
    assert resumen_periodo("", "2026-10-01", "") == "Desde 01/10/2026"
    assert resumen_periodo("hoy", "", "", "error") == "Cualquier fecha"
    assert siguiente_orden("x", "transporte_desc", "transporte_asc", "x") == {"activo": False, "sentido": "", "siguiente": "transporte_desc"}
    assert siguiente_orden("transporte_desc", "transporte_desc", "transporte_asc", "x")["sentido"] == "desc"


def test_exportar_excel_con_los_mismos_filtros(cliente_listas):
    import io

    from openpyxl import load_workbook

    cliente, bd = cliente_listas
    html = cliente.get("/dashboard/expediciones?f=1&estado=entregado&buscar=frutas").text
    assert 'href="/dashboard/expediciones/exportar?f=1&amp;estado=entregado&amp;buscar=frutas"' in html
    assert "Exportar a Excel" in html
    bd.expediciones[0].datos_deca = {"cargador": {"nombre_razon_social": "=HYPERLINK(\"x\")", "nif_cif": "B00345678"},
                                     "mercancia": {"naturaleza": "Fruta", "peso_kg": 1250.5, "bultos": 3}}
    r = cliente.get("/dashboard/expediciones/exportar?f=1&estado=entregado&buscar=frutas")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert ".xlsx" in r.headers["content-disposition"]
    libro = load_workbook(io.BytesIO(r.content))
    assert libro.sheetnames == ["Resumen", "Expediciones"]
    resumen = [[c.value for c in fila] for fila in libro["Resumen"].iter_rows()]
    assert ["Buscar", "frutas"] in resumen and ["Estados", "Entregado"] in resumen
    hoja = libro["Expediciones"]
    filas = [[c.value for c in fila] for fila in hoja.iter_rows()]
    assert filas[0][:3] == ["Referencia", "ERP", "Estado"]
    assert filas[1][9] == '=HYPERLINK("x")' and hoja.cell(2, 10).data_type == "s"  # texto, no fórmula
    assert filas[1][18] == 1250.5 and filas[1][2] == "En tránsito"
    assert filas[-1][0] == "Total" and filas[-1][18].startswith("=SUBTOTAL(109,S2:S")
    assert hoja.freeze_panes == "A2" and len(hoja.tables) == 1


def test_exportar_solo_admin_y_operador():
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.security.dependencies import SesionUsuario, obtener_usuario_actual

    app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), uuid.uuid4(), "lectura")
    app.dependency_overrides[get_db] = lambda: _BDListas()
    try:
        cliente = TestClient(app)
        assert cliente.get("/dashboard/expediciones/exportar").status_code == 403
        assert "Exportar</a>" not in cliente.get("/dashboard/expediciones").text
    finally:
        app.dependency_overrides.clear()


def test_paginar():
    from app.filtros import paginar

    p = paginar(656, 1)
    assert (p["pagina"], p["paginas"], p["offset"], p["desde"], p["hasta"]) == (1, 7, 0, 1, 100)
    assert p["ventana"] == [1, 2, 3, None, 7]
    p = paginar(656, 7)
    assert (p["offset"], p["desde"], p["hasta"]) == (600, 601, 656) and p["ventana"] == [1, None, 5, 6, 7]
    assert paginar(656, 99)["pagina"] == 7 and paginar(656, -3)["pagina"] == 1  # fuera de rango: se ajusta
    assert paginar(0, 1) == {"pagina": 1, "paginas": 1, "offset": 0, "tamano": 100, "desde": 0, "hasta": 0, "ventana": [1]}
    assert paginar(250, 4)["ventana"] == [1, 2, 3] and paginar(250, 4)["pagina"] == 3


def test_lista_pagina_con_desplazamiento(cliente_listas):
    cliente, bd = cliente_listas
    cliente.get("/dashboard/expediciones?pagina=2")
    consulta = next(c for c in bd.consultas if c.startswith("SELECT expediciones.id, expediciones.tenant_id"))
    assert "LIMIT" in consulta and "OFFSET" in consulta
    html = cliente.get("/dashboard/expediciones").text
    assert 'name="pagina" value="1"' in html
