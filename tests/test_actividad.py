"""Actividad de la expedición: eventos registrados y reconstrucción de
las anteriores al registro de actividad."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domain.models import BorradorDeca, EventoExpedicion, Expedicion, ModificacionDeca
from app.services.expedicion_service import registrar_evento
from app.services.actividad import NO_REGISTRADO, actividad

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


class _Res:
    def __init__(self, v):
        self.v = v

    def scalars(self):
        return self

    def all(self):
        return self.v

    def first(self):
        return self.v[0] if self.v else None


class _BD:
    def __init__(self, eventos=(), borradores=()):
        self.eventos, self.borradores, self.anadidos = list(eventos), list(borradores), []

    def execute(self, consulta):
        t = str(consulta)
        if "FROM eventos_expedicion" in t:
            return _Res(self.eventos)
        if "FROM borradores_deca" in t:
            return _Res(self.borradores)
        return _Res([])

    def add(self, o):
        self.anadidos.append(o)


def _exp(**kw):
    base = dict(id=uuid.uuid4(), tenant_id=uuid.uuid4(), estado="en_transito", fecha_emision=T0, erp_origen="odoo",
                fecha_finalizacion_servicio=None)
    base.update(kw)
    return Expedicion(**base)


def test_registrar_evento_va_en_la_transaccion():
    bd, exp = _BD(), _exp()
    registrar_evento(bd, exp, "entregado", "ana@empresa.es")
    registrar_evento(bd, exp, "emitida", None)
    assert [(e.accion, e.quien) for e in bd.anadidos] == [("entregado", "ana@empresa.es"), ("emitida", "Sistema")]
    assert all(e.expedicion_id == exp.id and e.tenant_id == exp.tenant_id for e in bd.anadidos)


def test_actividad_con_eventos():
    exp = _exp(estado="entregado", fecha_finalizacion_servicio=T0 + timedelta(days=1))
    eventos = [
        EventoExpedicion(fecha=T0, accion="emitida", quien="API", detalle="API: datos del ERP"),
        EventoExpedicion(fecha=T0 + timedelta(days=1), accion="entregado", quien="ana@empresa.es"),
    ]
    pasos = actividad(_BD(eventos), exp, modificaciones=[])
    assert [(p["texto"], p["quien"]) for p in pasos] == [("DeCA emitido", "API"), ("Marcado como entregado", "ana@empresa.es")]


def test_actividad_reconstruido_de_expediciones_anteriores():
    exp = _exp(estado="entregado", fecha_finalizacion_servicio=T0 + timedelta(days=2))
    borrador = BorradorDeca(resuelto_por="luis@empresa.es", nombre_documento="alb.pdf", referencia_fuente="x")
    modificacion = ModificacionDeca(fecha=T0 + timedelta(days=1), numero=1, motivo="Avería", origen="panel:ana@empresa.es")
    pasos = actividad(_BD(borradores=[borrador]), exp, modificaciones=[modificacion])
    assert [(p["accion"], p["quien"]) for p in pasos] == [
        ("emitida", "luis@empresa.es"), ("modificada", "ana@empresa.es"), ("entregado", NO_REGISTRADO),
    ]
    assert pasos[0]["detalle"] == "Bandeja de revisión: alb.pdf" and "Avería" in pasos[1]["detalle"]
