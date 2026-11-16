"""Fuera de desarrollo MADeCa no arranca sin HTTPS (Apartado Tercero.1
BOE-A-2026-12784): antes arrancaba y fallaba cada DeCA al emitirlo."""

import secrets

import pytest
from pydantic import ValidationError

from app.config import Settings


def _produccion(**cambios):
    datos = dict(
        app_env="production",
        app_secret_key=secrets.token_urlsafe(48),
        jwt_secret_key=secrets.token_urlsafe(48),
        app_base_url="https://deca.ejemplo.es",
        database_url="postgresql+psycopg://u:p@localhost/db",
    )
    datos.update(cambios)
    return Settings(_env_file=None, **datos)


def test_produccion_con_https_arranca():
    assert _produccion().app_base_url == "https://deca.ejemplo.es"


@pytest.mark.parametrize("url", ["http://192.168.1.10:8100", "http://localhost:8000", "deca.ejemplo.es"])
def test_produccion_sin_https_no_arranca(url):
    with pytest.raises(ValidationError, match="Tercero.1"):
        _produccion(app_base_url=url)


def test_desarrollo_admite_http_local():
    assert _produccion(app_env="development", app_base_url="http://localhost:8000").app_env == "development"


def test_zona_horaria_no_admitida():
    with pytest.raises(ValidationError):
        _produccion(zona_horaria="Europe/Lisbon")
