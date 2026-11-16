from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.schemas import DecaCreate, ExpedicionMetadata


def _payload_base(**overrides):
    base = {
        "referencia_externa": "ALB-2026-001",
        "erp_origen": "generico",
        "matricula_vehiculo": "1234BBC",
        "fecha_transporte": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def test_metadata_minima_valida():
    metadata = ExpedicionMetadata.model_validate(_payload_base())
    assert metadata.matricula_vehiculo == "1234BBC"
    assert metadata.finalidad_contractual is False
    assert metadata.matricula_remolque is None


def test_metadata_con_remolque_y_finalidad_contractual():
    metadata = ExpedicionMetadata.model_validate(
        _payload_base(matricula_remolque="M-1234-AB", finalidad_contractual=True)
    )
    assert metadata.matricula_remolque == "M1234AB"
    assert metadata.finalidad_contractual is True


def test_metadata_rechaza_matricula_invalida():
    with pytest.raises(ValidationError):
        ExpedicionMetadata.model_validate(_payload_base(matricula_vehiculo="INVALIDA"))


def test_metadata_requiere_referencia_externa():
    payload = _payload_base()
    del payload["referencia_externa"]
    with pytest.raises(ValidationError):
        ExpedicionMetadata.model_validate(payload)


def test_metadata_requiere_fecha_transporte():
    payload = _payload_base()
    del payload["fecha_transporte"]
    with pytest.raises(ValidationError):
        ExpedicionMetadata.model_validate(payload)


def test_metadata_erp_origen_por_defecto_generico():
    payload = _payload_base()
    del payload["erp_origen"]
    metadata = ExpedicionMetadata.model_validate(payload)
    assert metadata.erp_origen.value == "generico"


# --- Camino 2: MADeCa redacta el PDF (DecaCreate) ---


def _payload_deca_base(**overrides):
    base = {
        "referencia_externa": "ALB-2026-001",
        "erp_origen": "generico",
        "cargador": {
            "nombre_razon_social": "Transportes Ejemplo SL",
            "nif_cif": "B00345678",
            "domicilio": "Calle Industria 14, Madrid",
        },
        "transportista": {
            "nombre_razon_social": "Transportista Efectivo SL",
            "nif_cif": "B00345678",
        },
        "origen_direccion": "Calle Industria 14, Madrid",
        "destino_direccion": "Polígono Sur, Sevilla",
        "mercancia": {"naturaleza": "Palés de material eléctrico", "peso_kg": 850.5},
        "fecha_transporte": datetime.now(timezone.utc).isoformat(),
        "matricula_vehiculo": "1234BBC",
    }
    base.update(overrides)
    return base


def test_deca_create_payload_minimo_valido():
    deca = DecaCreate.model_validate(_payload_deca_base())
    assert deca.matricula_vehiculo == "1234BBC"
    assert deca.finalidad_contractual is False


def test_deca_create_requiere_peso_o_magnitud_alternativa():
    payload = _payload_deca_base()
    payload["mercancia"] = {"naturaleza": "Mercancía a granel"}
    with pytest.raises(ValidationError):
        DecaCreate.model_validate(payload)


def test_deca_create_acepta_magnitud_alternativa_sin_peso():
    payload = _payload_deca_base()
    payload["mercancia"] = {
        "naturaleza": "Mercancía a granel de difícil pesaje",
        "magnitud_alternativa": "3 contenedores de 20 pies",
    }
    deca = DecaCreate.model_validate(payload)
    assert deca.mercancia.peso_kg is None


def test_deca_create_adr_requiere_ambos_campos():
    payload = _payload_deca_base()
    payload["mercancia"] = {"naturaleza": "Disolventes", "peso_kg": 500, "adr_numero_onu": "1993"}
    with pytest.raises(ValidationError):
        DecaCreate.model_validate(payload)


def test_deca_create_rechaza_nif_invalido():
    payload = _payload_deca_base()
    payload["cargador"]["nif_cif"] = "B00000001"
    with pytest.raises(ValidationError):
        DecaCreate.model_validate(payload)


def test_deca_create_destinatario_opcional():
    deca = DecaCreate.model_validate(_payload_deca_base())
    assert deca.destinatario is None


def test_deca_create_requiere_domicilio_del_cargador():
    # Art. 6.a Orden FOM/2861/2012: nombre, NIF y domicilio del cargador.
    payload = _payload_deca_base()
    payload["cargador"]["domicilio"] = "   "
    with pytest.raises(ValidationError):
        DecaCreate.model_validate(payload)


def test_deca_create_autorizacion_especial_opcional():
    # Art. 6.e: solo cuando el vehículo circula amparado por una.
    assert DecaCreate.model_validate(_payload_deca_base()).autorizacion_especial_circulacion is None
    deca = DecaCreate.model_validate(_payload_deca_base(autorizacion_especial_circulacion="AEC-2026-0001"))
    assert deca.autorizacion_especial_circulacion == "AEC-2026-0001"


def test_cabotaje_acepta_matricula_extranjera_con_pais():
    # Cabotaje: transporte interior con vehículo de otro Estado (incluido en
    # el DeCA según la Resolución de 5 de junio de 2026).
    deca = DecaCreate.model_validate(
        _payload_deca_base(matricula_vehiculo="AB-12-CD", pais_matricula_vehiculo="pt")
    )
    assert deca.matricula_vehiculo == "AB12CD"
    assert deca.pais_matricula_vehiculo == "PT"


def test_matricula_extranjera_sin_pais_se_rechaza():
    with pytest.raises(ValidationError):
        DecaCreate.model_validate(_payload_deca_base(matricula_vehiculo="AB-12-CD"))


def test_remolque_espanol_con_tractora_extranjera():
    metadata = ExpedicionMetadata.model_validate(
        _payload_base(matricula_vehiculo="FR123AB", pais_matricula_vehiculo="FR", matricula_remolque="R1234BCD")
    )
    assert metadata.matricula_remolque == "R1234BCD"


def test_pais_invalido_se_rechaza():
    with pytest.raises(ValidationError):
        ExpedicionMetadata.model_validate(_payload_base(pais_matricula_vehiculo="ESP"))
