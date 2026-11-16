"""Pantalla de revisión: datos que faltan y errores por campo;
comprobación al escribir; agenda de partes; «revisar el siguiente»."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.revision import OBLIGATORIOS, errores_por_campo, faltan, texto_articulo, validar_campo


def test_obligatorios_del_art_6():
    # 6.e (autorización) y el remolque de 6.g no son siempre exigibles.
    assert "autorizacion_especial_circulacion" not in OBLIGATORIOS and "matricula_remolque" not in OBLIGATORIOS
    todo_vacio = faltan({})
    assert todo_vacio == list(OBLIGATORIOS)
    # 6.d: otra magnitud cubre el peso cuando no se puede pesar.
    assert "mercancia_peso_kg" not in faltan({"mercancia_magnitud_alternativa": "3 palets"})
    assert "cargador_nif" not in faltan({"cargador_nif": " B00345678 "})
    assert texto_articulo("matricula_vehiculo") == "Art. 6.g · responde el transportista"
    assert texto_articulo("transportista_nif") == "Art. 6.b · responde el cargador"  # art. 7.2


def test_comprobacion_por_campo_igual_que_al_emitir():
    assert validar_campo("cargador_nif", "B00345678") is None
    assert "se esperaba '4'" in validar_campo("cargador_nif", "B12345678")
    assert "se esperaba 'Z'" in validar_campo("transportista_nif", "12345678A")
    assert validar_campo("transportista_nif", "PT509871236") is None  # cabotaje
    assert validar_campo("matricula_vehiculo", "AB12CD", "ES")
    assert validar_campo("matricula_vehiculo", "AB12CD", "PT") is None
    assert validar_campo("mercancia_peso_kg", "1250,5") is None
    assert validar_campo("mercancia_peso_kg", "abc") and validar_campo("mercancia_peso_kg", "0")
    assert validar_campo("fecha_transporte", "mañana")
    assert validar_campo("cargador_nif", "") is None  # vacío: lo señala «faltan»
    errores = errores_por_campo({"cargador_nif": "B12345678", "matricula_vehiculo": "1234BCD", "pais_matricula_vehiculo": "ES"})
    assert list(errores) == ["cargador_nif"]


# ---------- Rutas ----------

class _Res:
    def __init__(self, v):
        self.v = v

    def all(self):
        return self.v if isinstance(self.v, list) else []

    def first(self):
        return self.v[0] if isinstance(self.v, list) and self.v else (self.v if not isinstance(self.v, list) else None)

    def scalars(self):
        return self

    def scalar_one(self):
        return self.v


class _BD:
    def __init__(self, borradores, expediciones=None, filas_partes=None):
        self.borradores = {b.id: b for b in borradores}
        self.expediciones = expediciones or []
        self.filas_partes = filas_partes or []
        self.commits = 0

    def execute(self, consulta):
        t = str(consulta)
        if "count(" in t:
            return _Res(sum(1 for b in self.borradores.values() if b.estado == "pendiente") - 1)
        if "#>>" in t and "FROM expediciones" in t:
            return _Res(self.filas_partes)
        if "FROM expediciones" in t:
            return _Res(self.expediciones)
        if "borradores_deca.id != " in t:
            otros = [b for b in self.borradores.values() if b.estado == "pendiente"]
            return _Res(otros[-1:])
        return _Res([])

    def get(self, modelo, id_):
        from app.domain.models import BorradorDeca

        if modelo is BorradorDeca:
            return self.borradores.get(id_)
        return SimpleNamespace(email="revisor@empresa.es", ocr_tercero_autorizado=False)

    def add(self, o):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def _borrador(tenant_id, **datos):
    from app.domain.models import BorradorDeca

    return BorradorDeca(id=uuid.uuid4(), tenant_id=tenant_id, estado="pendiente", origen="panel", referencia_fuente="x",
                        nombre_documento="albaran.pdf", datos=datos, confianza={"cargador.nif_cif": "media"},
                        avisos=["No encontrado: NIF del transportista (Art. 6.b)", "Documento escaneado con poca calidad"],
                        fecha_alta=datetime.now(timezone.utc), ruta_documento=None)


@pytest.fixture
def cliente():
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.security.dependencies import SesionUsuario, obtener_usuario_actual

    tenant = uuid.uuid4()
    estado = {}

    def preparar(*borradores, **kw):
        estado["bd"] = _BD(list(borradores), **kw)
        return estado["bd"]

    app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), tenant, "operador")
    app.dependency_overrides[get_db] = lambda: estado["bd"]
    try:
        yield TestClient(app), tenant, preparar
    finally:
        app.dependency_overrides.clear()


def test_revision_enlaza_y_marca_cada_dato_que_falta(cliente):
    c, tenant, preparar = cliente
    b = _borrador(tenant, cargador={"nombre_razon_social": "Frutas SL", "nif_cif": "B00345678"}, referencia_externa="ALB-1")
    preparar(b, _borrador(tenant))
    html = c.get(f"/dashboard/bandeja/{b.id}").text
    # Panel: enlace al campo y artículo.
    assert 'href="#campo-transportista_nif" data-ir="transportista_nif"' in html
    assert "Art. 6.b · responde el cargador" in html
    assert 'class="falta" data-para="transportista_nif"' in html and 'class="oculto" data-para="cargador_nif"' in html
    # Campo en rojo con su nota; el que se leyó está bien.
    assert 'class="campo c2 falta" id="fila-transportista_nif"' in html
    assert 'id="fila-cargador_nombre"' in html and 'class="campo c4" id="fila-cargador_nombre"' in html
    # Los datos leídos no se marcan como dudosos (solo lo que falta).
    assert 'class="campo c2" id="fila-cargador_nif"' in html and "dudoso" not in html.lower()
    # «No encontrado» va en los campos; los demás avisos se ven tal cual.
    assert "No encontrado:" not in html and "Documento escaneado con poca calidad" in html
    assert "y revisar el siguiente" in html and "1 pendiente más" in html


def test_emitir_con_datos_mal_marca_el_campo(cliente):
    c, tenant, preparar = cliente
    b = _borrador(tenant)
    preparar(b)
    r = c.post(f"/dashboard/bandeja/{b.id}/emitir", data={"referencia_externa": "A", "cargador_nif": "B12345678"})
    assert r.status_code == 422
    assert "corrige los datos señalados" in r.text
    assert "con-error" in r.text and "se esperaba &#39;4&#39;" in r.text
    assert 'data-error-para="cargador_nif"' in r.text


def test_aviso_de_posible_duplicado(cliente):
    from app.domain.models import Expedicion

    c, tenant, preparar = cliente
    b = _borrador(tenant, referencia_externa="ALB-7")
    previa = Expedicion(id=uuid.uuid4(), referencia_externa="ALB-7", estado="entregado", fecha_emision=datetime(2026, 10, 1, tzinfo=timezone.utc))
    preparar(b, expediciones=[previa])
    html = c.get(f"/dashboard/bandeja/{b.id}").text
    assert "Ya existe una expedición con la referencia <strong>ALB-7</strong>" in html and f"/dashboard/expediciones/{previa.id}" in html


def test_validar_campo_por_post(cliente):
    from app.domain.models import Expedicion

    c, tenant, preparar = cliente
    preparar(expediciones=[Expedicion(id=uuid.uuid4(), fecha_emision=datetime(2026, 10, 1, tzinfo=timezone.utc))])
    r = c.post("/dashboard/formulario/validar", data={"campo": "cargador_nif", "valor": "B12345678"}).json()
    assert r["ok"] is False and "se esperaba '4'" in r["mensaje"]
    r = c.post("/dashboard/formulario/validar", data={"campo": "matricula_vehiculo", "valor": "AB12CD", "pais": "PT"}).json()
    assert r["ok"] is True
    r = c.post("/dashboard/formulario/validar", data={"campo": "referencia_externa", "valor": "ALB-7"}).json()
    assert r["ok"] is True and "Ya hay una expedición" in r["aviso"]["texto"]
    # Solo por POST: el NIF no viaja en la dirección.
    assert c.get("/dashboard/formulario/validar?campo=cargador_nif&valor=x").status_code == 405


def test_agenda_de_partes_una_por_nif(cliente):
    c, tenant, preparar = cliente
    # Columnas: cargador (nombre, nif, dom), transportista (...), destinatario (...), de la más reciente a la más antigua.
    filas = [
        ("Frutas del Sur SL", "B00345678", "C/ Mayor 1, Sevilla", "Trans Sur SL", "B87654321", None, None, None, None),
        ("Frutas del Sur S.L.", "b-00345678", "", "Otra", "A00000000", None, None, None, None),
        ("Fruteria Ana", "12345678Z", "Plaza 3", "Trans Sur SL", "B87654321", None, None, None, None),
    ]
    preparar(filas_partes=filas)
    partes = c.post("/dashboard/formulario/partes", data={"texto": "frut"}).json()["partes"]
    assert [p["nif"] for p in partes] == ["B00345678", "12345678Z"]
    assert partes[0] == {"nombre": "Frutas del Sur SL", "nif": "B00345678", "domicilio": "C/ Mayor 1, Sevilla", "usos": 2}
    assert c.post("/dashboard/formulario/partes", data={"texto": "f"}).json() == {"partes": []}


def test_descartar_y_revisar_el_siguiente(cliente):
    c, tenant, preparar = cliente
    a, siguiente = _borrador(tenant), _borrador(tenant)
    preparar(a, siguiente)
    r = c.post(f"/dashboard/bandeja/{a.id}/descartar", data={"despues": "siguiente"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/dashboard/bandeja/{siguiente.id}?aviso=")
    preparar(a)
    a.estado = "pendiente"
    r = c.post(f"/dashboard/bandeja/{a.id}/descartar", data={"despues": "siguiente"}, follow_redirects=False)
    assert r.headers["location"].startswith("/dashboard/bandeja?aviso=")


def test_formulario_sin_javascript_en_linea():
    from app.plantillas import crear_plantillas

    p = crear_plantillas()
    for nombre in ("dashboard/_form_datos.html", "dashboard/bandeja_revision.html"):
        fuente = p.env.loader.get_source(p.env, nombre)[0]
        assert "<script" not in fuente and "onclick" not in fuente and "onchange" not in fuente


COMPLETO = {
    "referencia_externa": "ALB-9",
    "cargador": {"nombre_razon_social": "Frutas SL", "nif_cif": "B00345678", "domicilio": "C/ Mayor 1, Sevilla"},
    "transportista": {"nombre_razon_social": "Trans SL", "nif_cif": "A00818500"},
    "origen_direccion": "Sevilla", "destino_direccion": "Madrid",
    "mercancia": {"naturaleza": "Fruta", "peso_kg": 1000},
    "fecha_transporte": "2026-10-12T08:00:00+02:00", "matricula_vehiculo": "1234BCD",
}


def test_lote_descartar_y_emitir_solo_los_completos(cliente, monkeypatch):
    import app.routers.bandeja as bandeja

    c, tenant, preparar = cliente
    completo = _borrador(tenant, **COMPLETO)
    completo.confianza = {}
    dudoso = _borrador(tenant, **COMPLETO)  # confianza «media» en el NIF: es un dato correcto
    incompleto = _borrador(tenant, referencia_externa="X")
    incompleto.confianza = {}
    ajeno = _borrador(uuid.uuid4(), **COMPLETO)
    ajeno.confianza = {}
    preparar(completo, dudoso, incompleto, ajeno)
    emitidos = []

    def emitir(db, *, borrador, deca, usuario):
        emitidos.append(borrador.id)
        borrador.estado = "emitido"
        return SimpleNamespace(id=uuid.uuid4(), referencia_externa=deca.referencia_externa)

    monkeypatch.setattr(bandeja, "emitir_borrador", emitir)
    ids = [str(b.id) for b in (completo, dudoso, incompleto, ajeno)]

    # Sin la confirmación no se emite nada.
    r = c.post("/dashboard/bandeja/lote", data={"accion": "emitir", "ids": ids}, follow_redirects=False)
    assert emitidos == [] and "confirmaci" in r.headers["location"]

    r = c.post("/dashboard/bandeja/lote", data={"accion": "emitir", "ids": ids, "confirmo": "1"}, follow_redirects=False)
    # Completos y válidos se emiten (también con confianza «media»); el incompleto no.
    assert set(emitidos) == {completo.id, dudoso.id}
    from urllib.parse import unquote
    aviso = unquote(r.headers["location"])
    assert "2 DeCA emitidos." in aviso and "1 siguen pendientes" in aviso
    assert ajeno.estado == "pendiente"

    r = c.post("/dashboard/bandeja/lote", data={"accion": "descartar", "ids": [str(incompleto.id), str(ajeno.id)]},
               follow_redirects=False)
    assert incompleto.estado == "descartado" and ajeno.estado == "pendiente"
    assert "1 borrador descartado" in unquote(r.headers["location"])


def test_lote_no_permitido_a_lectura():
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.security.dependencies import SesionUsuario, obtener_usuario_actual

    app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), uuid.uuid4(), "lectura")
    app.dependency_overrides[get_db] = lambda: _BD([])
    try:
        assert TestClient(app).post("/dashboard/bandeja/lote", data={"accion": "descartar", "ids": [str(uuid.uuid4())]}).status_code == 403
    finally:
        app.dependency_overrides.clear()
