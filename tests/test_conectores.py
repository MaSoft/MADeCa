"""Conectores de fuentes de datos con los servicios externos simulados
(httpx.MockTransport): sin red real."""

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import get_settings
from app.conectores.base import ConectorError, validar_url_externa
from app.conectores.documentos import CarpetaServidor, Dropbox, GoogleDrive, SharePointOneDrive
from app.conectores.erp import BusinessCentral, Holded, Odoo, SapBusinessOne
from app.domain.models import FuenteDatos
from app.services import fuentes as servicio_fuentes


def _cliente(manejador):
    return httpx.Client(transport=httpx.MockTransport(manejador))


def _token(request):
    return httpx.Response(200, json={"access_token": "tk"})


# ------------------------------ SSRF ------------------------------

@pytest.mark.parametrize("url", ["https://127.0.0.1/x", "https://10.0.0.5", "https://169.254.169.254/latest", "https://localhost:8069"])
def test_ssrf_rechaza_redes_internas(url):
    with pytest.raises(ConectorError):
        validar_url_externa(url)


def test_ssrf_exige_https():
    with pytest.raises(ConectorError, match="https"):
        validar_url_externa("http://ejemplo.com")


def test_red_privada_permitida_solo_si_el_operador_lo_activa(monkeypatch):
    monkeypatch.setattr(get_settings(), "fuentes_permitir_red_privada", True)
    assert validar_url_externa("https://10.0.0.5/b1s/v1") == "https://10.0.0.5/b1s/v1"


# --------------------------- SharePoint ---------------------------

def test_sharepoint_delta_filtra_carpeta_y_guarda_deltalink():
    def manejar(req: httpx.Request):
        u = str(req.url)
        if "login.microsoftonline.com" in u:
            assert req.content and b"graph.microsoft.com%2F.default" in req.content
            return _token(req)
        assert req.headers["Authorization"] == "Bearer tk"
        if "/root:/" in u:
            return httpx.Response(200, json={"id": "CARPETA", "folder": {}})
        if u.endswith("/root/delta"):
            return httpx.Response(200, json={"value": [
                {"id": "A", "name": "alb1.pdf", "file": {}, "parentReference": {"id": "CARPETA"}, "lastModifiedDateTime": "t1"},
                {"id": "B", "name": "otro.pdf", "file": {}, "parentReference": {"id": "OTRA"}},
                {"id": "C", "name": "borrado.pdf", "deleted": {}, "parentReference": {"id": "CARPETA"}},
                {"id": "D", "name": "hoja.xlsx", "file": {}, "parentReference": {"id": "CARPETA"}},
            ], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/X/root/delta?token=Z"})
        if u.endswith("/items/A/content"):
            return httpx.Response(200, content=b"%PDF-1")
        raise AssertionError(u)

    c = SharePointOneDrive({"tenant_azure": "t", "client_id": "c", "client_secret": "s", "drive_id": "X", "carpeta": "Albaranes"},
                           _cliente(manejar))
    elementos, estado = c.sincronizar({})
    assert [e.nombre for e in elementos] == ["alb1.pdf"]
    assert elementos[0].contenido == b"%PDF-1" and elementos[0].referencia == "A@t1"
    assert estado == {"carpeta_id": "CARPETA", "delta": "https://graph.microsoft.com/v1.0/drives/X/root/delta?token=Z"}


# ---------------------------- Google ----------------------------

def _cuenta_servicio() -> str:
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return json.dumps({"client_email": "lector@proyecto.iam.gserviceaccount.com", "private_key": pem})


def test_google_drive_lista_descarga_y_avanza_fecha():
    def manejar(req: httpx.Request):
        u = str(req.url)
        if "oauth2.googleapis.com" in u:
            return _token(req)
        if req.url.path.endswith("/files") and "alt" not in u:
            assert "modifiedTime" in req.url.params["q"]
            return httpx.Response(200, json={"files": [
                {"id": "f1", "name": "alb.pdf", "mimeType": "application/pdf", "modifiedTime": "2026-10-09T08:00:00Z"},
                {"id": "f2", "name": "Nota", "mimeType": "application/vnd.google-apps.document", "modifiedTime": "2026-10-09T09:00:00Z"},
            ]})
        if req.url.path.endswith("/files/f1"):
            return httpx.Response(200, content=b"%PDF")
        raise AssertionError(u)

    c = GoogleDrive({"credenciales_json": _cuenta_servicio(), "carpeta_id": "1AbCdEfGhIjKlMn"}, _cliente(manejar))
    elementos, estado = c.sincronizar({})
    assert [e.nombre for e in elementos] == ["alb.pdf"]
    assert estado["desde"] == "2026-10-09T09:00:00Z"


def test_google_drive_rechaza_id_que_altera_la_consulta():
    with pytest.raises(ConectorError):
        GoogleDrive({"credenciales_json": "{}", "carpeta_id": "x' or name contains '"})


# ---------------------------- Dropbox ----------------------------

def test_dropbox_cursor():
    def manejar(req: httpx.Request):
        u = str(req.url)
        if u.endswith("/oauth2/token"):
            return _token(req)
        if u.endswith("/files/list_folder"):
            return httpx.Response(200, json={"entries": [
                {".tag": "file", "id": "id:1", "name": "a.jpg", "rev": "r1"}, {".tag": "folder", "id": "id:2", "name": "sub"}],
                "cursor": "CUR"})
        if u.endswith("/files/download"):
            assert json.loads(req.headers["Dropbox-API-Arg"]) == {"path": "id:1"}
            return httpx.Response(200, content=b"JPEG")
        raise AssertionError(u)

    c = Dropbox({"app_key": "k", "app_secret": "s", "refresh_token": "r", "carpeta": "/Albaranes"}, _cliente(manejar))
    elementos, estado = c.sincronizar({})
    assert [e.referencia for e in elementos] == ["id:1@r1"] and estado == {"cursor": "CUR"}


# ------------------------ Carpeta servidor ------------------------

def test_carpeta_servidor_no_sale_de_la_carpeta_de_la_empresa(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "fuentes_carpeta_base", tmp_path)
    tenant = str(uuid.uuid4())
    (tmp_path / tenant).mkdir()
    (tmp_path / tenant / "alb.pdf").write_bytes(b"%PDF")
    c = CarpetaServidor({"subcarpeta": ""}, tenant_id=tenant)
    elementos, estado = c.sincronizar({})
    assert [e.nombre for e in elementos] == ["alb.pdf"] and estado["desde"] > 0
    assert c.sincronizar(estado)[0] == []
    with pytest.raises(ConectorError):
        CarpetaServidor({"subcarpeta": "../otra_empresa"}, tenant_id=tenant)


def test_carpeta_servidor_desactivada_por_defecto():
    with pytest.raises(ConectorError):
        CarpetaServidor({"subcarpeta": ""}, tenant_id="x")


# ------------------------- Business Central -------------------------

def test_business_central_mapeo_de_albaran():
    def manejar(req: httpx.Request):
        u = str(req.url)
        if "login.microsoftonline.com" in u:
            assert b"api.businesscentral.dynamics.com%2F.default" in req.content
            return _token(req)
        assert u.startswith("https://api.businesscentral.dynamics.com/v2.0/t/production/api/v2.0/companies(E)/")
        if "companyInformation" in u:
            return httpx.Response(200, json={"value": [{"displayName": "Mi Empresa SL", "taxRegistrationNumber": "B00345678",
                                                        "addressLine1": "Calle Mayor 1", "postalCode": "28001", "city": "Madrid"}]})
        if "salesShipments" in u:
            assert "lastModifiedDateTime ge" in req.url.params["$filter"]
            return httpx.Response(200, json={"value": [{
                "id": "S1", "number": "102033", "postingDate": "2026-10-10", "lastModifiedDateTime": "2026-10-09T10:00:00Z",
                "customerName": "Cliente SA", "shipToName": "Cliente SA", "shipToAddressLine1": "Pol. Sur 4",
                "shipToPostCode": "41001", "shipToCity": "Sevilla",
                "customer": {"taxRegistrationNumber": "A00818500"},
                "salesShipmentLines": [{"lineType": "Item", "description": "Palé cable", "quantity": 3, "unitOfMeasureCode": "UD"},
                                       {"lineType": "Comment", "description": "nota"}],
            }]})
        raise AssertionError(u)

    c = BusinessCentral({"tenant_azure": "t", "client_id": "c", "client_secret": "s", "entorno": "production", "empresa_id": "E",
                         "transportista_nombre": "Trans SL", "transportista_nif": "B00345678"}, _cliente(manejar))
    # Marca de partida fija: sin ella se usa «hace 2 días» y la prueba
    # dependía del día en que se ejecutaba.
    elementos, estado = c.sincronizar({"desde": "2026-10-01T00:00:00Z"})
    d = elementos[0].datos
    assert d["referencia_externa"] == "102033"
    assert d["cargador"] == {"nombre_razon_social": "Mi Empresa SL", "nif_cif": "B00345678", "domicilio": "Calle Mayor 1, 28001, Madrid"}
    assert d["destinatario"]["nif_cif"] == "A00818500"
    assert d["destino_direccion"] == "Pol. Sur 4, 41001, Sevilla"
    assert d["mercancia"]["naturaleza"] == "3 UD, Palé cable"
    assert d["transportista"]["nombre_razon_social"] == "Trans SL"
    assert any("peso" in a for a in elementos[0].avisos) and any("matrícula" in a for a in elementos[0].avisos)
    assert estado["desde"] == "2026-10-09T10:00:00Z"


# ------------------------------ Odoo ------------------------------

def test_odoo_json2(monkeypatch):
    monkeypatch.setattr("app.conectores.erp.validar_url_externa", lambda url: url)

    def manejar(req: httpx.Request):
        assert req.headers["Authorization"] == "bearer KEY" and req.headers["X-Odoo-Database"] == "bd"
        cuerpo = json.loads(req.content)
        ruta = req.url.path
        if ruta == "/json/2/stock.picking/search_read":
            assert ["picking_type_code", "=", "outgoing"] in cuerpo["domain"]
            return httpx.Response(200, json=[{"id": 7, "name": "WH/OUT/0007", "partner_id": [3, "Cliente SA"],
                                              "scheduled_date": "2026-10-10 06:00:00", "company_id": [1, "Mi Empresa"],
                                              "write_date": "2026-10-09 10:00:00"}])
        if ruta == "/json/2/res.partner/read":
            return httpx.Response(200, json=[{"id": 3, "name": "Cliente SA", "vat": "ESA00818500", "street": "Pol. Sur 4", "zip": "41001", "city": "Sevilla"}])
        if ruta == "/json/2/res.company/read":
            return httpx.Response(200, json=[{"id": 1, "name": "Mi Empresa SL", "vat": "ESB00345678", "street": "Calle Mayor 1", "zip": "28001", "city": "Madrid"}])
        if ruta == "/json/2/stock.move/search_read":
            return httpx.Response(200, json=[{"picking_id": [7, "WH/OUT/0007"], "product_id": [9, "Cable"], "product_uom_qty": 12.0}])
        raise AssertionError(ruta)

    c = Odoo({"url": "https://erp.ejemplo.com", "base_datos": "bd", "api_key": "KEY"}, _cliente(manejar))
    elementos, estado = c.sincronizar({})
    d = elementos[0].datos
    assert d["cargador"]["nif_cif"] == "B00345678" and d["destinatario"]["nif_cif"] == "A00818500"
    assert d["mercancia"]["naturaleza"] == "12.0 x Cable"
    assert d["fecha_transporte"] == "2026-10-10T06:00:00+00:00"  # Odoo da UTC
    assert estado == {"desde": "2026-10-09 10:00:00", "desde_id": 7}


def test_odoo_sigue_por_fecha_e_id_sin_perder_empates(monkeypatch):
    monkeypatch.setattr("app.conectores.erp.validar_url_externa", lambda url: url)
    pedidos = []

    def manejar(req: httpx.Request):
        cuerpo = json.loads(req.content)
        if req.url.path == "/json/2/stock.picking/search_read":
            pedidos.append(cuerpo)
            return httpx.Response(200, json=[])
        raise AssertionError(req.url.path)

    c = Odoo({"url": "https://erp.ejemplo.com", "api_key": "KEY"}, _cliente(manejar))
    _, estado = c.sincronizar({"desde": "2026-10-09 10:00:00", "desde_id": 7})
    dominio = pedidos[0]["domain"]
    assert dominio[2:] == ["|", ["write_date", ">", "2026-10-09 10:00:00"],
                           "&", ["write_date", "=", "2026-10-09 10:00:00"], ["id", ">", 7]]
    assert pedidos[0]["order"] == "write_date asc, id asc" and pedidos[0]["limit"] == 50
    assert estado == {"desde": "2026-10-09 10:00:00", "desde_id": 7}


def test_carpeta_servidor_no_pierde_ficheros_del_mismo_segundo(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(get_settings(), "fuentes_carpeta_base", tmp_path)
    tenant = str(uuid.uuid4())
    carpeta = tmp_path / tenant
    carpeta.mkdir()
    for i in range(120):
        f = carpeta / f"alb{i:03d}.pdf"
        f.write_bytes(b"%PDF")
        os.utime(f, (1_760_000_000, 1_760_000_000))  # copia masiva: mismo segundo
    c = CarpetaServidor({"subcarpeta": ""}, tenant_id=tenant)
    leidos, estado = [], {}
    for _ in range(4):
        elementos, estado = c.sincronizar(estado)
        assert len(elementos) <= 50
        leidos += [e.nombre for e in elementos]
    assert sorted(leidos) == [f"alb{i:03d}.pdf" for i in range(120)]


# ---------------------------- SAP / Holded ----------------------------


def test_sap_b1_sigue_todas_las_paginas(monkeypatch):
    monkeypatch.setattr("app.conectores.erp.validar_url_externa", lambda url: url)
    paginas = []

    def manejar(req: httpx.Request):
        if req.url.path.endswith("/Login"):
            return httpx.Response(200, json={"SessionId": "x"})
        if req.url.path.endswith("/DeliveryNotes"):
            paginas.append(str(req.url))
            assert req.headers["Prefer"] == "odata.maxpagesize=50"
            salto = int(req.url.params.get("$skip", 0))
            lote = [{"DocEntry": n, "DocNum": n, "UpdateDate": "2026-10-09"} for n in range(salto, min(salto + 20, 45))]
            datos = {"value": lote}
            if salto + 20 < 45:
                datos["odata.nextLink"] = f"DeliveryNotes?$filter=x&$skip={salto + 20}"
            return httpx.Response(200, json=datos)
        return httpx.Response(204)

    c = SapBusinessOne({"url": "https://sap.ejemplo.com/b1s/v1", "company_db": "DB", "usuario": "u", "password": "p"},
                       _cliente(manejar))
    elementos, _ = c.sincronizar({"desde": "2026-10-08"})
    assert len(elementos) == 45 and len(paginas) == 3
    assert paginas[1].startswith("https://sap.ejemplo.com/b1s/v1/DeliveryNotes?")


def test_holded_lee_toda_la_ventana_con_margen():
    pedidos = []

    def manejar(req: httpx.Request):
        pedidos.append(dict(req.url.params))
        return httpx.Response(200, json=[{"id": str(i), "docNumber": f"A{i}", "date": 1_760_000_000} for i in range(80)])

    c = Holded({"api_key": "k"}, _cliente(manejar))
    elementos, estado = c.sincronizar({"desde": 1_760_100_000})
    assert len(elementos) == 80
    assert int(pedidos[0]["starttmp"]) == 1_760_100_000 - 2 * 86400
    assert estado["desde"] >= int(pedidos[0]["endtmp"])

def test_sap_b1_sesion_y_mapeo_editable(monkeypatch):
    monkeypatch.setattr("app.conectores.erp.validar_url_externa", lambda url: url)
    llamadas = []

    def manejar(req: httpx.Request):
        llamadas.append(req.url.path)
        if req.url.path.endswith("/Login"):
            return httpx.Response(200, json={"SessionId": "x"}, headers={"Set-Cookie": "B1SESSION=x"})
        if req.url.path.endswith("/DeliveryNotes"):
            return httpx.Response(200, json={"value": [{"DocEntry": 5, "DocNum": 1005, "CardName": "Cliente SA", "MiNif": "A00818500"}]})
        return httpx.Response(204)

    c = SapBusinessOne({"url": "https://sap.ejemplo.com/b1s/v1", "company_db": "DB", "usuario": "u", "password": "p",
                        "mapeo": json.dumps({"referencia_externa": "DocNum", "destinatario.nif_cif": "MiNif"}),
                        "cargador_nombre_razon_social": "Mi Empresa SL"}, _cliente(manejar))
    elementos, _ = c.sincronizar({})
    d = elementos[0].datos
    assert d["referencia_externa"] == "1005" and d["destinatario"]["nif_cif"] == "A00818500"
    assert d["cargador"]["nombre_razon_social"] == "Mi Empresa SL"
    assert llamadas[-1].endswith("/Logout")
    assert any("por verificar" in a for a in elementos[0].avisos)


def test_nif_iva_del_erp_se_convierte_en_nif():
    from app.conectores.erp import _nif

    assert _nif("ESB00345678") == "B00345678"
    assert _nif("es b-00345678") == "B00345678"
    assert _nif("B00345678") == "B00345678"
    assert _nif("ES12345678Z") == "12345678Z"
    assert _nif("FR12345678901") == "FR12345678901"  # extranjero: tal cual, lo revisa la bandeja
    assert _nif(None) is None and _nif("") is None


def test_mapeo_json_invalido():
    c = Holded({"api_key": "k", "mapeo": "{no es json"})
    with pytest.raises(ConectorError):
        c._mapeo()


# ------------------------- Servicio de fuentes -------------------------

def test_secretos_cifrados_y_conservados_si_se_dejan_vacios():
    f = servicio_fuentes.nueva_fuente(tenant_id=uuid.uuid4(), tipo="dropbox", nombre="Dbx",
                                      config={"app_key": "k", "app_secret": "SECRETO", "refresh_token": "R", "carpeta": "/a"})
    assert "SECRETO" not in f.config_cifrada
    servicio_fuentes.guardar_config(f, {"app_key": "k2", "app_secret": "", "refresh_token": "", "carpeta": "/b"})
    config = servicio_fuentes.leer_config(f)
    assert config["app_secret"] == "SECRETO" and config["app_key"] == "k2"
    visible = servicio_fuentes.config_visible(f)
    assert visible["app_secret"] == "" and visible["app_secret__guardado"] is True


def test_ejecutar_fuente_deja_borradores_y_anota_errores(monkeypatch):
    from app.conectores.base import Elemento

    tenant = SimpleNamespace(id=uuid.uuid4(), activo=True)
    creados = []

    class _BD:
        def get(self, _m, _i): return tenant
        def add(self, _o): pass
        def commit(self): pass
        def rollback(self): pass

    fuente = FuenteDatos(id=uuid.uuid4(), tenant_id=tenant.id, tipo="odoo", estado_sync={}, activo=True)

    class _Conector:
        def __init__(self, falla=False): self.falla = falla
        def sincronizar(self, estado):
            if self.falla:
                raise ConectorError("Odoo: error 401 — Invalid apikey")
            return [Elemento(referencia="odoo:7", nombre="Entrega 7", datos={"referencia_externa": "7"})], {"desde": "x"}

    def crear_borrador_falso(db, **kw):
        creados.append(kw)
        return None, True

    monkeypatch.setattr(servicio_fuentes, "crear_borrador", crear_borrador_falso)
    monkeypatch.setattr(servicio_fuentes, "conector_de", lambda f, cliente=None: _Conector())
    servicio_fuentes.ejecutar_fuente(_BD(), fuente)
    assert creados[0]["origen"] == f"fuente:{fuente.id}" and fuente.estado_sync == {"desde": "x"}
    assert fuente.ultimo_error is None and "1 nuevo" in fuente.ultimo_resultado

    monkeypatch.setattr(servicio_fuentes, "conector_de", lambda f, cliente=None: _Conector(falla=True))
    servicio_fuentes.ejecutar_fuente(_BD(), fuente)
    assert "Invalid apikey" in fuente.ultimo_error and fuente.estado_sync == {"desde": "x"}


def test_pantallas_de_fuentes_solo_admin_y_sin_secretos():
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.security.dependencies import SesionUsuario, obtener_usuario_actual

    tenant = SimpleNamespace(id=uuid.uuid4(), ocr_tercero_autorizado=False)
    fuente = servicio_fuentes.nueva_fuente(tenant_id=tenant.id, tipo="dropbox", nombre="Mi Dropbox",
                                           config={"app_key": "k", "app_secret": "SECRETO", "refresh_token": "R", "carpeta": "/a"})

    class _BD:
        def execute(self, _c):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [fuente]))
        def get(self, modelo, _i):
            return fuente if modelo is FuenteDatos else tenant

    app.dependency_overrides[get_db] = lambda: _BD()
    try:
        app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), tenant.id, "operador")
        assert TestClient(app).get("/dashboard/fuentes").status_code == 403
        app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), tenant.id, "admin")
        cliente = TestClient(app)
        lista = cliente.get("/dashboard/fuentes")
        assert lista.status_code == 200 and "Mi Dropbox" in lista.text and "SharePoint" in lista.text
        edicion = cliente.get(f"/dashboard/fuentes/{fuente.id}")
        assert edicion.status_code == 200 and "SECRETO" not in edicion.text and "Guardado" in edicion.text
        assert cliente.get("/dashboard/fuentes/nueva?tipo=business_central").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_autorizar_ocr_tercero_funciona_y_queda_registrado():
    # Antes la ruta /{fuente_id} capturaba "ocr-tercero" y respondía 422.
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.security.dependencies import SesionUsuario, obtener_usuario_actual

    tenant = SimpleNamespace(id=uuid.uuid4(), ocr_tercero_autorizado=False, ocr_tercero_autorizado_en=None, ocr_tercero_autorizado_por=None)

    class _BD:
        def get(self, modelo, _i):
            return SimpleNamespace(email="admin@demo.es") if modelo.__name__ == "Usuario" else tenant
        def add(self, _o): pass
        def commit(self): pass

    app.dependency_overrides[get_db] = lambda: _BD()
    app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), tenant.id, "admin")
    try:
        cliente = TestClient(app, follow_redirects=False)
        sin_casilla = cliente.post("/dashboard/fuentes/ocr-tercero", data={"autorizar": "si"})
        assert sin_casilla.status_code == 303 and tenant.ocr_tercero_autorizado is False
        r = cliente.post("/dashboard/fuentes/ocr-tercero", data={"autorizar": "si", "confirmo": "si"})
        assert r.status_code == 303
        assert tenant.ocr_tercero_autorizado is True and tenant.ocr_tercero_autorizado_por == "admin@demo.es"
    finally:
        app.dependency_overrides.clear()
