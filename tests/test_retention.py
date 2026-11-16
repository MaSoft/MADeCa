import uuid
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.domain.models import Expedicion
from app.services.retention import puede_purgarse, url_publica_activa


def _settings() -> Settings:
    return Settings(
        app_secret_key="x",
        app_base_url="https://deca.test.local",
        database_url="postgresql+psycopg://test:test@localhost/test",
        jwt_secret_key="y",
        retention_days=365,
        public_url_deactivation_days=7,
    )


def _expedicion(**overrides) -> Expedicion:
    base = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        referencia_externa="REF-1",
        erp_origen="generico",
        estado="en_transito",
        datos_deca={},
        fecha_transporte=datetime.now(timezone.utc),
        fecha_emision=datetime.now(timezone.utc),
        fecha_finalizacion_servicio=None,
        matricula_vehiculo="1234BBC",
        storage_path="/tmp/x.pdf",
        url_activa=True,
    )
    base.update(overrides)
    return Expedicion(**base)


def test_url_activa_mientras_el_servicio_esta_en_curso():
    exp = _expedicion(fecha_finalizacion_servicio=None)
    assert url_publica_activa(exp, _settings()) is True


def test_url_activa_dentro_de_los_7_dias_tras_finalizar():
    exp = _expedicion(
        estado="entregado",
        fecha_finalizacion_servicio=datetime.now(timezone.utc) - timedelta(days=3),
    )
    assert url_publica_activa(exp, _settings()) is True


def test_url_inactiva_pasados_7_dias_tras_finalizar():
    exp = _expedicion(
        estado="entregado",
        fecha_finalizacion_servicio=datetime.now(timezone.utc) - timedelta(days=8),
    )
    assert url_publica_activa(exp, _settings()) is False


def test_url_inactiva_si_anulada_aunque_no_haya_pasado_el_plazo():
    exp = _expedicion(estado="anulado", fecha_finalizacion_servicio=None)
    assert url_publica_activa(exp, _settings()) is False


def test_url_inactiva_si_desactivada_manualmente():
    exp = _expedicion(url_activa=False)
    assert url_publica_activa(exp, _settings()) is False


def test_no_puede_purgarse_antes_del_anio_de_retencion():
    exp = _expedicion(fecha_emision=datetime.now(timezone.utc) - timedelta(days=200))
    assert puede_purgarse(exp, _settings()) is False


def test_puede_purgarse_pasado_el_anio_de_retencion():
    exp = _expedicion(fecha_emision=datetime.now(timezone.utc) - timedelta(days=400))
    assert puede_purgarse(exp, _settings()) is True
