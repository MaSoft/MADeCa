from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.domain.models import ErpApiKey, SuperAdmin, Tenant, Usuario
from app.security.auth import decodificar_access_token, extraer_prefijo, verificar_api_key


@dataclass
class SesionUsuario:
    usuario_id: uuid.UUID
    tenant_id: uuid.UUID
    rol: str


@dataclass
class SesionSuperAdmin:
    superadmin_id: uuid.UUID


def obtener_usuario_actual(request: Request, db: Session = Depends(get_db)) -> SesionUsuario:
    token = request.cookies.get("madeca_session")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado")
    try:
        payload = decodificar_access_token(token)
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida o caducada") from exc

    if payload.get("tipo") != "tenant":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida")

    usuario_id = uuid.UUID(payload["sub"])
    usuario = db.get(Usuario, usuario_id)
    if usuario is None or not usuario.activo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario no válido")

    if usuario.sesiones_validas_desde is not None:
        emitido = datetime.fromtimestamp(int(payload.get("iat", 0)), tz=timezone.utc)
        if emitido < usuario.sesiones_validas_desde:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión anulada; vuelve a iniciar sesión")

    tenant = db.get(Tenant, usuario.tenant_id)
    if tenant is None or not tenant.activo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "La empresa asociada a este usuario está desactivada")

    return SesionUsuario(usuario_id=usuario.id, tenant_id=usuario.tenant_id, rol=usuario.rol)


def requerir_rol(*roles_permitidos: str):
    def _dependencia(sesion: SesionUsuario = Depends(obtener_usuario_actual)) -> SesionUsuario:
        if sesion.rol not in roles_permitidos:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No tiene permisos para esta acción")
        return sesion

    return _dependencia


def obtener_tenant_por_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> uuid.UUID:
    """Autentica los endpoints de ingesta ERP. Cada API key pertenece a un
    único tenant; nunca se deriva el tenant de un parámetro de la petición.
    """
    if not x_api_key.startswith("mad_live_"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key inválida")

    prefijo = extraer_prefijo(x_api_key)
    candidatas = db.execute(
        select(ErpApiKey).where(ErpApiKey.activo.is_(True), ErpApiKey.prefijo == prefijo)
    ).scalars().all()
    for candidata in candidatas:
        if verificar_api_key(x_api_key, candidata.key_hash):
            tenant = db.get(Tenant, candidata.tenant_id)
            if tenant is None or not tenant.activo:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "La empresa asociada a esta API key está desactivada")

            candidata.fecha_ultimo_uso = datetime.now(timezone.utc)
            db.add(candidata)
            db.commit()
            return candidata.tenant_id

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key inválida o revocada")


def obtener_superadmin_actual(request: Request, db: Session = Depends(get_db)) -> SesionSuperAdmin:
    token = request.cookies.get("madeca_superadmin_session")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado")
    try:
        payload = decodificar_access_token(token)
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida o caducada") from exc

    if payload.get("tipo") != "superadmin":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida")

    superadmin_id = uuid.UUID(payload["sub"])
    superadmin = db.get(SuperAdmin, superadmin_id)
    if superadmin is None or not superadmin.activo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Superadmin no válido")

    return SesionSuperAdmin(superadmin_id=superadmin.id)
