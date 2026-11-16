"""Bandeja de revisión. Sin BD real: sesión falsa en memoria y
almacenamiento local en carpeta temporal. La emisión real del PDF
(WeasyPrint) se sustituye por una expedición simulada."""

import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import get_settings
from app.database import get_db
from app.domain.models import BorradorDeca, Tenant, Usuario
from app.main import app
from app.routers.formulario_deca import deca_desde_valores, valores_desde_datos
from app.security.dependencies import SesionUsuario, obtener_tenant_por_api_key, obtener_usuario_actual
from app.services import borradores as servicio
from app.services.storage import get_storage
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


class _Resultado:
    def __init__(self, valor):
        self.valor = valor

    def scalar_one_or_none(self):
        return self.valor

    def scalar_one(self):
        return self.valor

    def scalars(self):
        lista = self.valor if isinstance(self.valor, list) else []
        return SimpleNamespace(all=lambda: lista, first=lambda: lista[0] if lista else None)

    def all(self):
        return self.valor if isinstance(self.valor, list) else []


class _BD:
    """Lo justo para el servicio y las rutas de la bandeja."""

    def __init__(self, tenant):
        self.objetos = {}
        self.tenant = tenant
        self.commits = 0

    def execute(self, consulta):
        texto = str(consulta)
        if "GROUP BY borradores_deca.estado" in texto:
            cuentas: dict = {}
            for o in self.objetos.values():
                if isinstance(o, BorradorDeca):
                    cuentas[o.estado] = cuentas.get(o.estado, 0) + 1
            return _Resultado(list(cuentas.items()))
        if "count(" in texto:
            return _Resultado(sum(1 for o in self.objetos.values() if isinstance(o, BorradorDeca)))
        if "FROM expediciones" in texto and "borradores_deca" not in texto:
            return _Resultado([])
        if "borradores_deca.estado = :estado_1" in texto and "borradores_deca.id != " in texto:
            pendientes = [o for o in self.objetos.values() if isinstance(o, BorradorDeca) and o.estado == "pendiente"]
            return _Resultado(pendientes[:1])
        if "FROM fuentes_datos" in texto:
            return _Resultado([])
        if "SELECT DISTINCT borradores_deca.origen" in texto:
            return _Resultado(sorted({o.origen for o in self.objetos.values() if isinstance(o, BorradorDeca)}))
        if "borradores_deca.referencia_fuente = :" in texto:
            p = consulta.compile().params
            for o in self.objetos.values():
                if isinstance(o, BorradorDeca) and o.referencia_fuente == p.get("referencia_fuente_1") and o.origen == p.get("origen_1"):
                    return _Resultado(o)
            return _Resultado(None)
        return _Resultado([o for o in self.objetos.values() if isinstance(o, BorradorDeca)])

    def get(self, modelo, id_):
        if modelo is Tenant:
            return self.tenant
        if modelo is Usuario:
            return SimpleNamespace(email="revisor@empresa.es")
        return self.objetos.get(id_)

    def add(self, objeto):
        self.objetos[objeto.id] = objeto

    def commit(self):
        self.commits += 1

    def refresh(self, _o):
        pass

    def rollback(self):
        pass


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_backend", "local")
    monkeypatch.setattr(get_settings(), "storage_local_path", tmp_path)
    tenant = SimpleNamespace(id=uuid.uuid4(), activo=True, ocr_tercero_autorizado=False)
    return tenant, _BD(tenant)


def test_documento_nativo_crea_borrador_con_datos_y_guarda_original(entorno):
    tenant, db = entorno
    b, creado = servicio.procesar_documento(
        db, tenant=tenant, origen="panel", referencia_fuente="alb-1", nombre="alb.pdf", contenido=_pdf(ALBARAN)
    )
    assert creado and b.estado == "pendiente"
    assert b.datos["cargador"]["nif_cif"] == "B00345678"
    assert get_storage().leer(b.ruta_documento).startswith(b"%PDF")
    # Idempotente: misma referencia, mismo borrador.
    b2, creado2 = servicio.procesar_documento(
        db, tenant=tenant, origen="panel", referencia_fuente="alb-1", nombre="alb.pdf", contenido=_pdf(ALBARAN)
    )
    assert b2.id == b.id and not creado2


def test_escaneo_sin_ocr_crea_borrador_vacio_con_aviso(entorno, monkeypatch):
    tenant, db = entorno
    monkeypatch.setattr(servicio, "motor_ocr_para", lambda _t: None)
    b, _ = servicio.procesar_documento(
        db, tenant=tenant, origen="panel", referencia_fuente="foto-1", nombre="foto.jpg", contenido=b"\xff\xd8 no es texto"
    )
    assert b.datos == {}
    assert "No se pudo leer" in b.avisos[0]


def test_emitir_borra_el_original_y_enlaza_la_expedicion(entorno, monkeypatch):
    tenant, db = entorno
    b, _ = servicio.procesar_documento(
        db, tenant=tenant, origen="panel", referencia_fuente="alb-2", nombre="alb.pdf", contenido=_pdf(ALBARAN)
    )
    ruta = b.ruta_documento
    exp_id = uuid.uuid4()
    llamadas = []
    monkeypatch.setattr(servicio, "crear_expedicion_desde_datos",
                        lambda db, tenant_id, deca, **kw: llamadas.append(kw) or SimpleNamespace(id=exp_id))
    deca, error = deca_desde_valores(valores_desde_datos(b.datos))
    assert error is None
    servicio.emitir_borrador(db, borrador=b, deca=deca, usuario="revisor@empresa.es")
    assert b.estado == "emitido" and b.expedicion_id == exp_id
    # Actividad: quién emitió y desde qué documento.
    assert llamadas == [{"quien": "revisor@empresa.es", "detalle": "Bandeja de revisión: alb.pdf"}]
    assert b.ruta_documento is None
    with pytest.raises(FileNotFoundError):
        get_storage().leer(ruta)
    with pytest.raises(servicio.BorradorNoPendienteError):
        servicio.descartar_borrador(db, borrador=b, usuario="x")


def test_azure_solo_con_autorizacion_de_la_empresa(monkeypatch):
    monkeypatch.setattr(get_settings(), "azure_di_endpoint", "https://x.cognitiveservices.azure.com")
    monkeypatch.setattr(get_settings(), "azure_di_clave", "clave")
    sin = servicio.motor_ocr_para(SimpleNamespace(ocr_tercero_autorizado=False))
    con = servicio.motor_ocr_para(SimpleNamespace(ocr_tercero_autorizado=True))
    assert sin is None or sin.nombre == "tesseract"
    assert con.nombre == "azure"


def test_api_del_conector_local_solo_datos(entorno):
    tenant, db = entorno
    app.dependency_overrides[obtener_tenant_por_api_key] = lambda: tenant.id
    app.dependency_overrides[get_db] = lambda: db
    try:
        cliente = TestClient(app)
        cuerpo = {"referencia_fuente": "\\\\NAS\\albaranes\\a1.pdf", "datos": {"matricula_vehiculo": "1234BCD"}, "avisos": ["x"]}
        r = cliente.post("/api/v1/borradores", json=cuerpo)
        assert r.status_code == 201 and r.json()["creado"] is True
        r2 = cliente.post("/api/v1/borradores", json=cuerpo)
        assert r2.json()["creado"] is False and r2.json()["id"] == r.json()["id"]
        borrador = next(iter(db.objetos.values()))
        assert borrador.ruta_documento is None  # el documento no salió del cliente
        assert borrador.origen == "api:conector_local"
    finally:
        app.dependency_overrides.clear()


def test_pantalla_de_revision_precarga_los_datos(entorno):
    tenant, db = entorno
    b, _ = servicio.procesar_documento(
        db, tenant=tenant, origen="panel", referencia_fuente="alb-3", nombre="alb.pdf", contenido=_pdf(ALBARAN)
    )
    app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), tenant.id, "operador")
    app.dependency_overrides[get_db] = lambda: db
    try:
        cliente = TestClient(app)
        r = cliente.get(f"/dashboard/bandeja/{b.id}")
        assert r.status_code == 200
        assert 'value="B00345678"' in r.text
        assert f'action="/dashboard/bandeja/{b.id}/emitir"' in r.text
        doc = cliente.get(f"/dashboard/bandeja/{b.id}/documento")
        assert doc.status_code == 200 and doc.headers["x-frame-options"] == "SAMEORIGIN"
        assert cliente.get("/dashboard/expediciones/nueva").headers.get("x-frame-options") in (None, "DENY")
        lista = cliente.get("/dashboard/bandeja")
        assert lista.status_code == 200 and "alb.pdf" in lista.text
    finally:
        app.dependency_overrides.clear()
