"""Enlace de descarga para la otra parte — Apartado Segundo.4,
BOE-A-2026-12784. Sin BD real (sesión falsa) y con almacenamiento local
en una carpeta temporal."""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import get_db
from app.domain.models import Expedicion
from app.main import app
from app.services.storage import get_storage


class _Resultado:
    def __init__(self, valor):
        self._valor = valor

    def scalar_one_or_none(self):
        return self._valor


class _SesionFalsa:
    def __init__(self, expedicion):
        self.expedicion = expedicion
        self.anadidos = []

    def execute(self, consulta):
        token = consulta.compile().params.get("token_contraparte_1")
        return _Resultado(self.expedicion if token == self.expedicion.token_contraparte else None)

    def add(self, objeto):
        self.anadidos.append(objeto)

    def commit(self):
        pass


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_backend", "local")
    monkeypatch.setattr(get_settings(), "storage_local_path", tmp_path)
    exp = Expedicion(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        fecha_emision=datetime.now(timezone.utc),
        token_contraparte=secrets.token_hex(32),
    )
    exp.storage_path = get_storage().guardar(exp.tenant_id, exp.id, b"%PDF-1.7 prueba")
    db = _SesionFalsa(exp)
    app.dependency_overrides[get_db] = lambda: db
    yield exp, db, TestClient(app)
    app.dependency_overrides.clear()


def test_descarga_valida_y_registrada(entorno):
    exp, db, cliente = entorno
    r = cliente.get(f"/descarga/{exp.token_contraparte}")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.7 prueba"
    assert db.anadidos[0].resultado == "contraparte"


def test_token_desconocido_404(entorno):
    _exp, _db, cliente = entorno
    assert cliente.get(f"/descarga/{secrets.token_hex(32)}").status_code == 404
    assert cliente.get("/descarga/corto").status_code == 404


def test_fuera_de_plazo_410(entorno):
    exp, _db, cliente = entorno
    exp.fecha_emision = datetime.now(timezone.utc) - timedelta(days=get_settings().retention_days + 1)
    assert cliente.get(f"/descarga/{exp.token_contraparte}").status_code == 410
