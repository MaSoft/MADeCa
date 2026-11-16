"""Motor de mapeo de campos ERP -> nuestros esquemas.

Dos familias de mapeo, una por cada camino de ingesta (ver
app/domain/schemas.py y app/services/expedicion_service.py):

- `MAPEOS_METADATA_*` + `construir_metadata_payload`: para el sidecar
  pequeño que acompaña a un PDF que ya genera el ERP (referencia,
  matrícula, fecha).
- `MAPEOS_DATOS_*` + `construir_deca_payload`: para cuando el ERP no
  genera PDF propio y manda los datos completos (cargador, transportista,
  mercancía...) para que MADeCa redacte el documento.

IMPORTANTE sobre a3ERP y Sigrid: ninguno de los dos publica un esquema
de exportación público y estable — los nombres de campo reales dependen
de la versión instalada y de cómo cada cliente haya configurado su
exportación. Las plantillas de abajo son puntos de partida orientativos,
NO verificadas contra una instalación real. Antes de poner en
producción un cliente con uno de estos ERPs, confirma los nombres de
campo reales de su export/sidecar y ajusta el mapeo correspondiente.
"""

from __future__ import annotations

from typing import Any


def _get_ruta(datos: dict[str, Any], ruta: str) -> Any:
    """Resuelve una ruta 'a.b.c' dentro de un dict anidado."""
    valor: Any = datos
    for parte in ruta.split("."):
        if not isinstance(valor, dict):
            return None
        valor = valor.get(parte)
    return valor


def aplicar_mapeo(datos_crudos: dict[str, Any], mapeo: dict[str, str]) -> dict[str, Any]:
    """Traduce un dict con nombres de campo del ERP origen a los nombres
    esperados por nuestro esquema, usando `mapeo` = {campo_destino: ruta_origen}."""
    resultado: dict[str, Any] = {}
    for campo_destino, ruta_origen in mapeo.items():
        if ruta_origen is None:
            continue
        resultado[campo_destino] = _get_ruta(datos_crudos, ruta_origen)
    return resultado


# ============================================================
# Camino 1: sidecar de metadatos (el PDF lo aporta el ERP)
# ============================================================

MAPEO_METADATA_GENERICO: dict[str, str] = {}

MAPEO_METADATA_A3ERP: dict[str, str] = {
    "referencia_externa": "Cabecera.NumeroAlbaran",
    "matricula_vehiculo": "Cabecera.Matricula",
    "matricula_remolque": "Cabecera.MatriculaRemolque",
    "fecha_transporte": "Cabecera.FechaAlbaran",
}

MAPEO_METADATA_SIGRID: dict[str, str] = {
    "referencia_externa": "Cabecera.NumeroExpedicion",
    "matricula_vehiculo": "Cabecera.Matricula",
    "matricula_remolque": None,
    "fecha_transporte": "Cabecera.Fecha",
}

MAPEOS_METADATA_DISPONIBLES: dict[str, dict[str, str]] = {
    "generico": MAPEO_METADATA_GENERICO,
    "a3erp": MAPEO_METADATA_A3ERP,
    "sigrid": MAPEO_METADATA_SIGRID,
}


def construir_metadata_payload(datos_crudos: dict[str, Any], erp_origen: str) -> dict[str, Any]:
    """Aplica el mapeo del ERP origen y añade erp_origen (parámetro de
    confianza, nunca tomado del propio payload)."""
    mapeo = MAPEOS_METADATA_DISPONIBLES.get(erp_origen, MAPEO_METADATA_GENERICO)
    if not mapeo:
        return {**datos_crudos, "erp_origen": erp_origen}
    payload = aplicar_mapeo(datos_crudos, mapeo)
    payload["erp_origen"] = erp_origen
    return payload


# ============================================================
# Camino 2: datos completos (MADeCa redacta el PDF)
# ============================================================

MAPEO_DATOS_GENERICO: dict[str, str] = {}

MAPEO_DATOS_A3ERP: dict[str, str] = {
    "referencia_externa": "Cabecera.NumeroAlbaran",
    "cargador.nombre_razon_social": "Empresa.RazonSocial",
    "cargador.nif_cif": "Empresa.CIF",
    "cargador.domicilio": "Empresa.Domicilio",
    "transportista.nombre_razon_social": "A3_TRANSPORTISTAS.RazonSocial",
    "transportista.nif_cif": "A3_TRANSPORTISTAS.CIF",
    "destinatario.nombre_razon_social": "A3_CLIENTES.RazonSocial",
    "destinatario.nif_cif": "A3_CLIENTES.CIF",
    "destinatario.domicilio": "A3_CLIENTES.Domicilio",
    "origen_direccion": "Cabecera.DireccionOrigen",
    "destino_direccion": "Cabecera.DireccionEnvio",
    "mercancia.naturaleza": "Cabecera.DescripcionMercancia",
    "mercancia.peso_kg": "Cabecera.PesoKilos",
    "mercancia.bultos": "Cabecera.Bultos",
    "fecha_transporte": "Cabecera.FechaAlbaran",
    "matricula_vehiculo": "Cabecera.Matricula",
    "matricula_remolque": "Cabecera.MatriculaRemolque",
    "observaciones": "Cabecera.Observaciones",
}

MAPEO_DATOS_SIGRID: dict[str, str] = {
    "referencia_externa": "Cabecera.NumeroExpedicion",
    "cargador.nombre_razon_social": "EmpresaActiva.Nombre",
    "cargador.nif_cif": "EmpresaActiva.NIF",
    "cargador.domicilio": "EmpresaActiva.Direccion",
    "transportista.nombre_razon_social": "AgenciasTransporte.Nombre",
    "transportista.nif_cif": "AgenciasTransporte.NIF",
    "destinatario.nombre_razon_social": "DireccionesEnvio.NombreCliente",
    "destinatario.nif_cif": "DireccionesEnvio.NIF",
    "destinatario.domicilio": "DireccionesEnvio.Direccion",
    "origen_direccion": "Cabecera.DireccionAlmacen",
    "destino_direccion": "DireccionesEnvio.Direccion",
    "mercancia.naturaleza": "Cabecera.DescripcionMercancia",
    "mercancia.peso_kg": "Cabecera.PesoKilos",
    "mercancia.bultos": "Cabecera.Bultos",
    "fecha_transporte": "Cabecera.Fecha",
    "matricula_vehiculo": "Cabecera.Matricula",
    "matricula_remolque": None,
    "observaciones": "Cabecera.Observaciones",
}

MAPEOS_DATOS_DISPONIBLES: dict[str, dict[str, str]] = {
    "generico": MAPEO_DATOS_GENERICO,
    "a3erp": MAPEO_DATOS_A3ERP,
    "sigrid": MAPEO_DATOS_SIGRID,
}


def construir_deca_payload(datos_crudos: dict[str, Any], erp_origen: str) -> dict[str, Any]:
    """Aplica el mapeo plano y reconstruye la estructura anidada que
    espera DecaCreate (cargador/transportista/destinatario/mercancia)."""
    mapeo = MAPEOS_DATOS_DISPONIBLES.get(erp_origen, MAPEO_DATOS_GENERICO)

    if not mapeo:
        return {**datos_crudos, "erp_origen": erp_origen}

    planos = aplicar_mapeo(datos_crudos, mapeo)

    payload: dict[str, Any] = {"erp_origen": erp_origen}
    anidados: dict[str, dict[str, Any]] = {}

    for clave, valor in planos.items():
        if "." in clave:
            grupo, sub = clave.split(".", 1)
            anidados.setdefault(grupo, {})[sub] = valor
        else:
            payload[clave] = valor

    for grupo, contenido in anidados.items():
        if all(v is None for v in contenido.values()):
            continue
        payload[grupo] = contenido

    return payload
