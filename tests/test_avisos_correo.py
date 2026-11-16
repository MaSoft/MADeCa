"""Resumen diario de avisos por correo."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import avisos_correo


class _Redis:
    def __init__(self, roto=False):
        self.claves, self.roto = {}, roto

    def exists(self, k):
        if self.roto:
            raise ConnectionError("sin redis")
        return k in self.claves

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.claves:
            return None
        self.claves[k] = v
        return True

    def delete(self, k):
        self.claves.pop(k, None)


class _Res:
    def __init__(self, v):
        self.v = v

    def scalars(self):
        return self

    def all(self):
        return self.v


class _BD:
    def __init__(self, tenants, admins):
        self.tenants, self.admins = tenants, admins

    def execute(self, consulta):
        t = str(consulta)
        if "FROM tenants" in t:
            return _Res(self.tenants)
        if "usuarios.email" in t:
            return _Res(self.admins)
        return _Res([])


AVISO = [{"nivel": "grave", "texto": "El certificado «Sello» caducó el 01/10/2026.", "enlace": "/dashboard/certificados", "accion": "Ver"}]
# 10:00 en Madrid (verano): pasada la hora de envío por defecto (8).
DIEZ = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def entorno(monkeypatch):
    s = SimpleNamespace(smtp_host="smtp.ejemplo.es", smtp_usuario="avisos@ejemplo.es", smtp_remitente=None,
                        avisos_correo_hora=8, app_base_url="https://madeca.ejemplo.es", smtp_seguridad="starttls",
                        smtp_puerto=587, smtp_clave="x")
    monkeypatch.setattr(avisos_correo, "get_settings", lambda: s)
    enviados = []
    monkeypatch.setattr(avisos_correo, "enviar", lambda m: enviados.append(m))
    monkeypatch.setattr(avisos_correo, "avisos_empresa", lambda db, tid, ahora: AVISO)
    return s, enviados


def test_un_correo_al_dia_a_los_administradores(entorno):
    s, enviados = entorno
    empresa = SimpleNamespace(id=uuid.uuid4(), nombre="Frutas SL")
    bd, redis = _BD([empresa], ["ana@frutas.es", "luis@frutas.es"]), _Redis()
    assert avisos_correo.pasada(bd, redis, DIEZ) == 1
    m = enviados[0]
    assert m["To"] == "ana@frutas.es, luis@frutas.es" and m["From"] == "avisos@ejemplo.es"
    assert "1 aviso de Frutas SL (12/10/2026)" in m["Subject"]
    texto = m.get_body(("plain",)).get_content()
    assert "URGENTE: El certificado «Sello»" in texto and "https://madeca.ejemplo.es/dashboard/certificados" in texto
    assert "Masoft.es" in texto
    # Mismo día: no se repite.
    assert avisos_correo.pasada(bd, redis, DIEZ + timedelta(hours=2)) == 0 and len(enviados) == 1
    # Al día siguiente, sí.
    assert avisos_correo.pasada(bd, redis, DIEZ + timedelta(days=1)) == 1


def test_no_envia_antes_de_la_hora_ni_sin_smtp_ni_sin_redis(entorno):
    s, enviados = entorno
    bd = _BD([SimpleNamespace(id=uuid.uuid4(), nombre="X")], ["a@x.es"])
    assert avisos_correo.pasada(bd, _Redis(), DIEZ.replace(hour=4)) == 0  # 06:00 en Madrid
    assert avisos_correo.pasada(bd, _Redis(roto=True), DIEZ) == 0
    s.smtp_host = None
    assert avisos_correo.pasada(bd, _Redis(), DIEZ) == 0
    assert enviados == []


def test_fallo_de_smtp_se_reintenta(entorno, monkeypatch):
    s, enviados = entorno
    import smtplib

    def falla(m):
        raise smtplib.SMTPServerDisconnected("caído")

    monkeypatch.setattr(avisos_correo, "enviar", falla)
    bd, redis = _BD([SimpleNamespace(id=uuid.uuid4(), nombre="X")], ["a@x.es"]), _Redis()
    assert avisos_correo.pasada(bd, redis, DIEZ) == 0
    assert redis.claves == {}  # sin marca: se reintenta en la siguiente pasada


def test_html_escapa_los_nombres(entorno):
    m = avisos_correo.componer("<b>Empresa</b>", [{"nivel": "aviso", "texto": "Certificado «<script>»", "enlace": "/x"}],
                               "12/10/2026", "https://m.es", "a@m.es", ["b@m.es"])
    html = m.get_body(("html",)).get_content()
    assert "<script>" not in html and "&lt;script&gt;" in html
