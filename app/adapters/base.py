from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.adapters.mapping import construir_deca_payload, construir_metadata_payload
from app.domain.schemas import DecaCreate, ExpedicionMetadata


@dataclass
class ResultadoIngesta:
    exito: bool
    metadata: ExpedicionMetadata | None = None
    errores: list[str] | None = None


@dataclass
class ResultadoIngestaDatos:
    exito: bool
    deca: DecaCreate | None = None
    errores: list[str] | None = None


def normalizar_e_validar_metadata(
    datos_crudos: dict[str, Any], erp_origen: str = "generico"
) -> ResultadoIngesta:
    """Camino 1 (PDF del ERP + sidecar): mapea los metadatos del ERP
    origen y los valida contra el esquema mínimo. El PDF en sí no se
    valida aquí (ver app.services.pdf_generator)."""
    try:
        payload = construir_metadata_payload(datos_crudos, erp_origen)
        metadata = ExpedicionMetadata.model_validate(payload)
        return ResultadoIngesta(exito=True, metadata=metadata)
    except ValidationError as exc:
        errores = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return ResultadoIngesta(exito=False, errores=errores)
    except Exception as exc:  # mapeo malformado, tipos inesperados, etc.
        return ResultadoIngesta(exito=False, errores=[str(exc)])


def normalizar_e_validar_datos(
    datos_crudos: dict[str, Any], erp_origen: str = "generico"
) -> ResultadoIngestaDatos:
    """Camino 2 (MADeCa redacta el PDF): mapea los datos completos del
    ERP origen y los valida contra el DTO estricto (datos mínimos Art. 6)."""
    try:
        payload = construir_deca_payload(datos_crudos, erp_origen)
        deca = DecaCreate.model_validate(payload)
        return ResultadoIngestaDatos(exito=True, deca=deca)
    except ValidationError as exc:
        errores = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return ResultadoIngestaDatos(exito=False, errores=errores)
    except Exception as exc:
        return ResultadoIngestaDatos(exito=False, errores=[str(exc)])
