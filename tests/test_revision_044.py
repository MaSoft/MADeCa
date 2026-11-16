"""Correcciones de la versión 0.4.4 (revisión completa del 16/11/2026)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.validation import validar_nif_cif, validar_nif_nie


# --- NIF: longitud y NIF K/L/M (art. 19.2 RD 1065/2007) ---

@pytest.mark.parametrize("valor", ["0T", "123P", "1234567890Z", "X123L"])
def test_nif_de_longitud_incorrecta_se_rechaza(valor):
    with pytest.raises(ValueError):
        validar_nif_nie(valor)


def test_dni_sin_cero_inicial_se_completa():
    # 1234567 % 23 = 19 -> L
    assert validar_nif_nie("1234567L") == "01234567L"
    assert validar_nif_nie("12345678Z") == "12345678Z"


def test_nie_valido():
    assert validar_nif_nie("X0000000T") == "X0000000T"


@pytest.mark.parametrize("valor", ["K1234567A", "L7654321B", "M0000001C"])
def test_nif_klm_se_admite_por_su_forma(valor):
    assert validar_nif_cif(valor) == valor


def test_cif_sigue_validandose():
    assert validar_nif_cif("B00345678") == "B00345678"
    with pytest.raises(ValueError):
        validar_nif_cif("B12345678")


# --- Límite de tamaño por ruta ---

def test_limite_mayor_en_rutas_de_subida():
    from app.config import get_settings
    from app.main import TAMANO_MAXIMO_PETICION_BYTES, tamano_maximo_peticion

    assert tamano_maximo_peticion("/dashboard/certificados") == TAMANO_MAXIMO_PETICION_BYTES
    assert tamano_maximo_peticion("/dashboard/bandeja/subir") > get_settings().documento_origen_max_bytes


def test_subida_de_15_mb_no_se_corta_en_el_middleware():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    r = c.post("/dashboard/bandeja/subir", headers={"content-length": str(15 * 1024 * 1024)}, content=b"")
    assert r.status_code != 413
    r = c.post("/dashboard/certificados", headers={"content-length": str(15 * 1024 * 1024)}, content=b"")
    assert r.status_code == 413


# --- Purga: también los eventos ---

def test_purga_borra_los_eventos(monkeypatch):
    from datetime import datetime, timedelta, timezone

    import scripts.purgar_expirados as purga
    from app.domain.models import EventoExpedicion

    exp = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), storage_path="x.pdf",
                          fecha_emision=datetime.now(timezone.utc) - timedelta(days=800))
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.side_effect = [[exp], []]
    monkeypatch.setattr(purga, "SessionLocal", lambda: db)
    monkeypatch.setattr(purga, "get_storage", lambda: MagicMock())
    monkeypatch.setattr("sys.argv", ["purgar", "--confirmar"])
    purga.main()
    tablas = [str(getattr(c.args[0], "table", "")) for c in db.execute.call_args_list if c.args]
    assert EventoExpedicion.__tablename__ in tablas
    db.commit.assert_called_once()


# --- Visor público: user-agent largo ---

def test_user_agent_largo_se_recorta():
    from app.routers.public_inspector import _registrar_acceso

    db = MagicMock()
    peticion = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"), headers={"user-agent": "A" * 2000})
    _registrar_acceso(db, uuid.uuid4(), peticion, "concedido")
    assert len(db.add.call_args.args[0].user_agent) == 500


# --- Content-Disposition con letras fuera de latin-1 ---

def test_disposicion_sin_error_con_caracteres_no_latin1():
    from fastapi.responses import Response

    from app.services.copia_conductor import disposicion

    cabecera = disposicion("attachment", "DeCA_Łódź_€.pdf")
    Response(content=b"x", headers={"Content-Disposition": cabecera})  # no debe lanzar
    assert 'filename="DeCA_' in cabecera and "filename*=UTF-8''" in cabecera


# --- Contraseña mínima ---

def test_password_corta_rechazada():
    from app.security.auth import validar_password_nueva

    with pytest.raises(ValueError):
        validar_password_nueva("")
    with pytest.raises(ValueError):
        validar_password_nueva("corta")
    assert validar_password_nueva("una-clave-larga") == "una-clave-larga"


# --- Carpeta compartida: CSV anidado y celdas vacías ---

def test_csv_de_datos_valida(tmp_path):
    from app.adapters.base import normalizar_e_validar_datos, normalizar_e_validar_metadata
    from app.adapters.hotfolder_watcher import _leer_filas

    csv_datos = tmp_path / "p.csv"
    csv_datos.write_text(
        "referencia_externa,cargador.nombre_razon_social,cargador.nif_cif,cargador.domicilio,"
        "transportista.nombre_razon_social,transportista.nif_cif,origen_direccion,destino_direccion,"
        "mercancia.naturaleza,mercancia.peso_kg,fecha_transporte,matricula_vehiculo,matricula_remolque\n"
        "R-1,Cargador SL,B00345678,C/ Uno 1,Transportes SL,B00654327,Madrid,Sevilla,Cajas,1200,2026-11-20T08:00,1234BCD,\n",
        encoding="utf-8",
    )
    fila = _leer_filas(csv_datos)[0]
    assert fila["cargador"]["nif_cif"] == "B00345678" and "matricula_remolque" not in fila
    resultado = normalizar_e_validar_datos(fila)
    assert resultado.exito, resultado.errores

    csv_meta = tmp_path / "m.csv"
    csv_meta.write_text("referencia_externa,matricula_vehiculo,matricula_remolque,fecha_transporte\nA-1,1234BCD,,2026-11-20T08:00\n",
                        encoding="utf-8")
    assert normalizar_e_validar_metadata(_leer_filas(csv_meta)[0]).exito


# --- OCR: un fallo inesperado no pierde el documento ---

def test_fallo_inesperado_del_ocr_es_extraccion_error():
    from app.extraccion.texto import ExtraccionError, obtener_texto

    class MotorRoto:
        nombre = "roto"

        def reconocer_con_posiciones(self, contenido, tipo):
            raise RuntimeError("fallo interno")

    with pytest.raises(ExtraccionError):
        obtener_texto(b"no es una imagen", "foto.jpg", MotorRoto())


# --- Odoo: fecha en UTC ---

def test_odoo_fecha_con_desfase():
    from app.conectores.erp import Odoo

    o = Odoo({"url": "https://x.odoo.com", "api_key": "k"}, cliente=MagicMock())
    respuestas = {
        "stock.picking": [{"id": 1, "name": "WH/OUT/1", "partner_id": None, "company_id": [1, "E"],
                           "scheduled_date": "2026-11-20 07:00:00", "write_date": "2026-11-20 07:00:00"}],
        "res.company": [{"id": 1, "name": "E", "vat": None, "street": None, "zip": None, "city": None}],
        "stock.move": [],
    }
    o._llamar = lambda modelo, metodo, cuerpo: respuestas[modelo]
    elementos, _ = o.sincronizar({})
    assert elementos[0].datos["fecha_transporte"] == "2026-11-20T07:00:00+00:00"
