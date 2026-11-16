"""Fecha y hora del transporte (art. 6.f Orden FOM/2861/2012) con su zona:
en Canarias es una hora menos. Antes una hora sin desfase (formulario de la
bandeja) se guardaba como UTC y el PDF imprimía siempre la peninsular."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.domain.schemas import DecaCreate, ExpedicionMetadata
from app.plantillas import fecha, hora
from app.routers.formulario_deca import deca_desde_valores, valores_desde_datos
from app.zonas import CANARIAS, PENINSULA, texto, zona_de


def _meta(**cambios):
    datos = {"referencia_externa": "A1", "matricula_vehiculo": "1234BCD", "fecha_transporte": "2026-10-10T06:31"}
    datos.update(cambios)
    return ExpedicionMetadata.model_validate(datos)


def test_hora_sin_desfase_toma_la_zona_indicada():
    m = _meta(zona_horaria=CANARIAS)
    assert m.zona_horaria == CANARIAS
    assert m.fecha_transporte.isoformat() == "2026-10-10T06:31:00+01:00"


def test_hora_sin_desfase_ni_zona_toma_la_predeterminada():
    m = _meta()
    assert m.zona_horaria == PENINSULA
    assert m.fecha_transporte.isoformat() == "2026-10-10T06:31:00+02:00"


def test_la_zona_se_deduce_del_desfase():
    # 10/10: Canarias UTC+1, península UTC+2.
    assert _meta(fecha_transporte="2026-10-10T06:31:00+01:00").zona_horaria == CANARIAS
    assert _meta(fecha_transporte="2026-10-10T06:31:00+02:00").zona_horaria == PENINSULA
    # En enero: Canarias UTC+0, península UTC+1.
    assert zona_de(datetime.fromisoformat("2026-01-15T08:00:00+00:00")) == CANARIAS


def test_zona_no_admitida():
    with pytest.raises(ValidationError):
        _meta(zona_horaria="America/New_York")


def test_texto_indica_la_zona():
    m = _meta(fecha_transporte="2026-10-10T06:31:00+01:00")
    assert texto(m.fecha_transporte, m.zona_horaria) == "10/10/2026 06:31 (hora de Canarias)"
    assert texto(m.fecha_transporte, PENINSULA) == "10/10/2026 07:31 (hora peninsular)"


def test_panel_en_la_zona_del_transporte():
    m = _meta(fecha_transporte="2026-10-10T06:31:00+01:00")
    assert hora(m.fecha_transporte, zona=CANARIAS) == "06:31"
    assert fecha(m.fecha_transporte, zona=CANARIAS) == "10/10/2026"


def test_formulario_de_la_bandeja_conserva_la_hora_local():
    """El caso del fallo: borrador con hora canaria, se confirma tal cual."""
    datos = {
        "referencia_externa": "TC-1", "matricula_vehiculo": "1234BCD",
        "cargador": {"nombre_razon_social": "C SL", "nif_cif": "B00345678", "domicilio": "Calle 1"},
        "transportista": {"nombre_razon_social": "T SL", "nif_cif": "B00654327"},
        "origen_direccion": "O", "destino_direccion": "D",
        "mercancia": {"naturaleza": "Palés", "peso_kg": 100},
        "fecha_transporte": "2026-10-10T06:28:00+01:00",
    }
    valores = valores_desde_datos(datos)
    assert valores["fecha_transporte"] == "2026-10-10T06:28"
    assert valores["zona_horaria"] == CANARIAS
    deca, error = deca_desde_valores(valores)
    assert error is None
    assert deca.fecha_transporte.isoformat() == "2026-10-10T06:28:00+01:00"
