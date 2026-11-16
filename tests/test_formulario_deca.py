"""Formulario común de datos del DeCA (alta manual y bandeja)."""

import uuid

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.routers.formulario_deca import deca_desde_valores, valores_desde_datos
from app.security.dependencies import SesionUsuario, obtener_usuario_actual

DATOS = {
    "referencia_externa": "ALB-1",
    "cargador": {"nombre_razon_social": "Cargador SL", "nif_cif": "B00345678", "domicilio": "Calle Mayor 1, Madrid"},
    "transportista": {"nombre_razon_social": "Transportes SL", "nif_cif": "B00345678"},
    "origen_direccion": "Madrid",
    "destino_direccion": "Sevilla",
    "mercancia": {"naturaleza": "Palés", "peso_kg": 850.5},
    "fecha_transporte": "2026-10-10T08:00:00",
    "matricula_vehiculo": "1234BCD",
}


def test_ida_y_vuelta_datos_formulario():
    valores = valores_desde_datos(DATOS)
    assert valores["fecha_transporte"] == "2026-10-10T08:00"
    deca, error = deca_desde_valores(valores)
    assert error is None
    assert deca.cargador.domicilio == "Calle Mayor 1, Madrid"
    assert deca.mercancia.peso_kg == 850.5


def test_peso_con_coma_decimal():
    valores = valores_desde_datos(DATOS)
    valores["mercancia_peso_kg"] = "850,5"
    deca, error = deca_desde_valores(valores)
    assert error is None and deca.mercancia.peso_kg == 850.5


def test_alta_manual_con_dato_invalido_devuelve_el_formulario_con_error():
    app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), uuid.uuid4(), "operador")
    app.dependency_overrides[get_db] = lambda: None
    try:
        valores = valores_desde_datos(DATOS)
        valores["cargador_domicilio"] = ""
        r = TestClient(app).post("/dashboard/expediciones/nueva-datos", data=valores)
        assert r.status_code == 422
        assert "domicilio del cargador" in r.text
        assert 'value="Cargador SL"' in r.text  # conserva lo escrito
    finally:
        app.dependency_overrides.clear()
