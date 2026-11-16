from app.adapters.base import normalizar_e_validar_datos, normalizar_e_validar_metadata
from app.adapters.mapping import aplicar_mapeo, construir_deca_payload, construir_metadata_payload


def test_aplicar_mapeo_resuelve_rutas_anidadas():
    datos = {"Cabecera": {"Matricula": "1234BBC"}}
    mapeo = {"matricula_vehiculo": "Cabecera.Matricula", "inexistente": "X.Y.Z"}
    resultado = aplicar_mapeo(datos, mapeo)
    assert resultado == {"matricula_vehiculo": "1234BBC", "inexistente": None}


def test_construir_metadata_payload_generico_es_identidad_mas_erp_origen():
    datos = {"referencia_externa": "REF-1", "matricula_vehiculo": "1234BBC"}
    payload = construir_metadata_payload(datos, "generico")
    assert payload["referencia_externa"] == "REF-1"
    assert payload["erp_origen"] == "generico"


def test_construir_metadata_payload_generico_ignora_erp_origen_del_payload():
    # erp_origen es un parámetro de confianza (ruta REST / carpeta del
    # hot-folder), nunca se hereda del propio payload.
    datos = {"referencia_externa": "REF-1", "matricula_vehiculo": "1234BBC", "erp_origen": "sap"}
    payload = construir_metadata_payload(datos, "generico")
    assert payload["erp_origen"] == "generico"


def test_construir_metadata_payload_a3erp_traduce_campos():
    datos_crudos = {
        "Cabecera": {
            "NumeroAlbaran": "ALB-999",
            "Matricula": "1234BBC",
            "MatriculaRemolque": "M1234AB",
            "FechaAlbaran": "2026-08-01T10:00:00",
        }
    }
    payload = construir_metadata_payload(datos_crudos, "a3erp")
    assert payload["referencia_externa"] == "ALB-999"
    assert payload["matricula_vehiculo"] == "1234BBC"
    assert payload["matricula_remolque"] == "M1234AB"
    assert payload["erp_origen"] == "a3erp"


def test_normalizar_e_validar_metadata_erp_desconocido_falla_con_errores_legibles():
    resultado = normalizar_e_validar_metadata({"foo": "bar"}, erp_origen="generico")
    assert resultado.exito is False
    assert resultado.errores
    assert any("referencia_externa" in e for e in resultado.errores)


def test_normalizar_e_validar_metadata_a3erp_extremo_a_extremo():
    datos_crudos = {
        "Cabecera": {
            "NumeroAlbaran": "ALB-1000",
            "Matricula": "1234 BBC",
            "FechaAlbaran": "2026-08-01T10:00:00Z",
        }
    }
    resultado = normalizar_e_validar_metadata(datos_crudos, erp_origen="a3erp")
    assert resultado.exito, resultado.errores
    assert resultado.metadata.matricula_vehiculo == "1234BBC"
    assert resultado.metadata.referencia_externa == "ALB-1000"


# --- Camino 2: datos completos (MADeCa redacta el PDF) ---


def test_construir_deca_payload_generico_es_identidad():
    datos = {"referencia_externa": "REF-1", "matricula_vehiculo": "1234BBC"}
    payload = construir_deca_payload(datos, "generico")
    assert payload["referencia_externa"] == "REF-1"
    assert payload["erp_origen"] == "generico"


def test_construir_deca_payload_a3erp_reconstruye_anidados():
    datos_crudos = {
        "Cabecera": {
            "NumeroAlbaran": "ALB-999",
            "DireccionOrigen": "Calle A, 1",
            "DireccionEnvio": "Calle B, 2",
            "DescripcionMercancia": "Palés",
            "PesoKilos": 300,
            "FechaAlbaran": "2026-08-01T10:00:00",
            "Matricula": "1234BBC",
        },
        "Empresa": {"RazonSocial": "Cargador SL", "CIF": "B00345678", "Domicilio": "Calle A, 1"},
        "A3_TRANSPORTISTAS": {"RazonSocial": "Transportes SL", "CIF": "B00345678"},
    }
    payload = construir_deca_payload(datos_crudos, "a3erp")
    assert payload["referencia_externa"] == "ALB-999"
    assert payload["cargador"]["nombre_razon_social"] == "Cargador SL"
    assert payload["cargador"]["nif_cif"] == "B00345678"
    assert payload["transportista"]["nombre_razon_social"] == "Transportes SL"
    assert payload["mercancia"]["peso_kg"] == 300


def test_normalizar_e_validar_datos_a3erp_extremo_a_extremo():
    datos_crudos = {
        "Cabecera": {
            "NumeroAlbaran": "ALB-1000",
            "DireccionOrigen": "Calle A, 1, Madrid",
            "DireccionEnvio": "Calle B, 2, Sevilla",
            "DescripcionMercancia": "Material eléctrico",
            "PesoKilos": 620,
            "FechaAlbaran": "2026-08-01T10:00:00Z",
            "Matricula": "1234 BBC",
        },
        "Empresa": {"RazonSocial": "Cargador SL", "CIF": "B00345678", "Domicilio": "Calle A, 1"},
        "A3_TRANSPORTISTAS": {"RazonSocial": "Transportes SL", "CIF": "B00345678"},
    }
    resultado = normalizar_e_validar_datos(datos_crudos, erp_origen="a3erp")
    assert resultado.exito, resultado.errores
    assert resultado.deca.matricula_vehiculo == "1234BBC"


def test_normalizar_e_validar_datos_erp_desconocido_falla_con_errores_legibles():
    resultado = normalizar_e_validar_datos({"foo": "bar"}, erp_origen="generico")
    assert resultado.exito is False
    assert resultado.errores
