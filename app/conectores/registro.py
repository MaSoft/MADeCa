"""Tipos de fuente disponibles, en el orden en que se ofrecen."""

from __future__ import annotations

from app.conectores.base import Conector, ConectorError
from app.conectores.documentos import CarpetaServidor, Dropbox, GoogleDrive, SharePointOneDrive, Sftp
from app.conectores.erp import BusinessCentral, Holded, Odoo, SapBusinessOne

TIPOS: dict[str, type[Conector]] = {
    c.tipo: c
    for c in (SharePointOneDrive, GoogleDrive, Dropbox, Sftp, CarpetaServidor, BusinessCentral, Odoo, SapBusinessOne, Holded)
}


def crear_conector(tipo: str, config: dict, *, tenant_id: str | None = None, cliente=None) -> Conector:
    clase = TIPOS.get(tipo)
    if clase is None:
        raise ConectorError(f"Tipo de fuente desconocido: {tipo}")
    if clase is CarpetaServidor:
        return CarpetaServidor(config, cliente, tenant_id=tenant_id)
    return clase(config, cliente)
