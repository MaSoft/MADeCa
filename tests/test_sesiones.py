"""Anulación de sesiones al restablecer la contraseña — sin BD real."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.domain.models import Tenant, Usuario
from app.security.dependencies import obtener_usuario_actual


def _token(usuario_id: uuid.UUID, tenant_id: uuid.UUID, emitido: datetime) -> str:
    payload = {
        "tipo": "tenant",
        "sub": str(usuario_id),
        "tenant_id": str(tenant_id),
        "rol": "admin",
        "iat": emitido,
        "exp": emitido + timedelta(hours=1),
    }
    return jwt.encode(payload, get_settings().jwt_secret_key, algorithm="HS256")


class _BdFalsa:
    def __init__(self, usuario, tenant):
        self._objetos = {Usuario: usuario, Tenant: tenant}

    def get(self, modelo, _id):
        return self._objetos[modelo]


def _sesion(sesiones_validas_desde, emitido):
    tenant = SimpleNamespace(id=uuid.uuid4(), activo=True)
    usuario = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=tenant.id, rol="admin", activo=True,
        sesiones_validas_desde=sesiones_validas_desde,
    )
    request = SimpleNamespace(cookies={"madeca_session": _token(usuario.id, tenant.id, emitido)})
    return obtener_usuario_actual(request, _BdFalsa(usuario, tenant))


def test_sin_corte_la_sesion_es_valida():
    assert _sesion(None, datetime.now(timezone.utc)).rol == "admin"


def test_token_anterior_al_corte_se_rechaza():
    corte = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(HTTPException) as exc:
        _sesion(corte, corte - timedelta(minutes=5))
    assert exc.value.status_code == 401


def test_login_en_el_mismo_segundo_del_corte_es_valido():
    corte = datetime.now(timezone.utc).replace(microsecond=0)
    assert _sesion(corte, corte).rol == "admin"
