"""Los errores de validación de la API deben llegar al ERP como 422 con
motivo legible, no como 500."""

import json

import pytest
from pydantic import ValidationError

from app.domain.schemas import DecaCreate
from app.routers.erp_ingestion import errores_validacion


def _datos(**cambios):
    datos = {
        "referencia_externa": "ALB-1",
        "matricula_vehiculo": "1234BCD",
        "cargador": {"nombre_razon_social": "Cargador SL", "nif_cif": "B00345678", "domicilio": "Calle 1"},
        "transportista": {"nombre_razon_social": "Transportes SL", "nif_cif": "B00654327"},
        "origen_direccion": "Origen",
        "destino_direccion": "Destino",
        "mercancia": {"naturaleza": "Palés", "peso_kg": 100},
        "fecha_transporte": "2026-10-10T08:00:00+01:00",
    }
    datos.update(cambios)
    return datos


@pytest.mark.parametrize("cambios", [
    {"cargador": {"nombre_razon_social": "X", "nif_cif": "B12345670", "domicilio": "Calle 1"}},  # control erróneo
    {"mercancia": {"naturaleza": "Palés"}},  # sin peso ni magnitud
    {"matricula_vehiculo": "1234BC"},
])
def test_errores_validacion_serializables(cambios):
    with pytest.raises(ValidationError) as info:
        DecaCreate.model_validate(_datos(**cambios))
    errores = errores_validacion(info.value)
    texto = json.dumps(errores, ensure_ascii=False)  # no debe lanzar TypeError
    assert errores and all("msg" in e for e in errores)
    assert "ctx" not in texto and "input" not in texto
