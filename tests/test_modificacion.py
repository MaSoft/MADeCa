"""Modificación durante el servicio — Apartado Quinto, método 1,
BOE-A-2026-12784. Sin BD real: sesión falsa + almacenamiento local en una
carpeta temporal."""

import io
import uuid
from datetime import datetime, timezone

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import get_settings
from app.domain.models import Expedicion
from app.domain.schemas import ModificacionCreate
from app.services.expedicion_service import ModificacionNoPermitidaError, modificar_expedicion
from app.services.pdf_generator import anadir_pagina_modificacion
from app.services.storage import get_storage

CREACION = "D:20261009070000+00'00'"


def _pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(50, 800, "DeCA de prueba - matricula 1234BBC")
    c.save()
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    writer.add_metadata({"/CreationDate": CREACION})
    salida = io.BytesIO()
    writer.write(salida)
    return salida.getvalue()


def _texto(pdf_bytes: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def test_pagina_de_modificacion():
    ahora = datetime(2026, 10, 9, 9, 15, tzinfo=timezone.utc)
    resultado = anadir_pagina_modificacion(
        pdf_bytes=_pdf(),
        numero=1,
        motivo="Avería de la tractora",
        cambios=[{"campo": "Matrícula del vehículo", "anterior": "1234BBC", "nuevo": "5678CDF"}],
        ahora=ahora,
    )
    lector = PdfReader(io.BytesIO(resultado))
    assert len(lector.pages) == 2
    texto = _texto(resultado)
    for esperado in ("MODIFICACIÓN", "Avería de la tractora", "NO VÁLIDO", "1234BBC", "5678CDF", "11:15"):
        assert esperado in texto
    # Primero.2 / Segundo.1: se conserva la creación y se fija la modificación.
    assert lector.metadata["/CreationDate"] == CREACION
    assert lector.metadata["/ModDate"] == "D:20261009091500+00'00'"


class _Resultado:
    def __init__(self, valor):
        self._valor = valor

    def scalar_one(self):
        return self._valor


class _SesionFalsa:
    def __init__(self):
        self.anadidos = []
        self.commits = 0

    def execute(self, _consulta):
        return _Resultado(0)  # ninguna modificación previa

    def add(self, objeto):
        self.anadidos.append(objeto)

    def commit(self):
        self.commits += 1

    def refresh(self, _objeto):
        pass

    def rollback(self):
        pass


@pytest.fixture
def expedicion(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_backend", "local")
    monkeypatch.setattr(get_settings(), "storage_local_path", tmp_path)
    exp = Expedicion(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        referencia_externa="ALB-1",
        erp_origen="generico",
        estado="en_transito",
        datos_deca={"matricula_vehiculo": "1234BBC"},
        fecha_transporte=datetime.now(timezone.utc),
        matricula_vehiculo="1234BBC",
        firmado=False,
        finalidad_contractual=False,
    )
    exp.storage_path = get_storage().guardar(exp.tenant_id, exp.id, _pdf())
    return exp


def test_modificar_cambio_de_vehiculo(expedicion):
    original = get_storage().leer(expedicion.storage_path)
    db = _SesionFalsa()
    registro = modificar_expedicion(
        db,
        expedicion=expedicion,
        modificacion=ModificacionCreate.model_validate(
            {"motivo": "Cambio de vehículo por avería", "cambios": [
                {"campo": "matricula_vehiculo", "anterior": "1234BBC", "nuevo": "5678 cdf"}
            ]}
        ),
        origen="test",
    )
    assert registro.numero == 1
    assert expedicion.matricula_vehiculo == "5678CDF"  # validada y normalizada
    assert registro.cambios[0]["campo"] == "Matrícula del vehículo"
    # Misma ruta (misma URL/QR) con la página añadida; anterior conservada.
    vigente = get_storage().leer(expedicion.storage_path)
    assert len(PdfReader(io.BytesIO(vigente)).pages) == 2
    assert get_storage().leer(registro.ruta_pdf_anterior) == original
    assert db.commits == 1


def test_matricula_nueva_invalida_no_toca_nada(expedicion):
    original = get_storage().leer(expedicion.storage_path)
    with pytest.raises(ModificacionNoPermitidaError):
        modificar_expedicion(
            _SesionFalsa(),
            expedicion=expedicion,
            modificacion=ModificacionCreate.model_validate(
                {"motivo": "Prueba", "cambios": [{"campo": "matricula_vehiculo", "anterior": "1234BBC", "nuevo": "MAL"}]}
            ),
            origen="test",
        )
    assert get_storage().leer(expedicion.storage_path) == original


def test_no_se_modifica_fuera_de_servicio(expedicion):
    expedicion.estado = "entregado"
    with pytest.raises(ModificacionNoPermitidaError):
        modificar_expedicion(
            _SesionFalsa(),
            expedicion=expedicion,
            modificacion=ModificacionCreate.model_validate(
                {"motivo": "Tarde", "cambios": [{"campo": "Peso", "anterior": "800 kg", "nuevo": "850 kg"}]}
            ),
            origen="test",
        )


def test_finalizar_solo_una_vez_y_con_estado_final_valido(expedicion):
    from app.services.expedicion_service import finalizar_servicio

    db = _SesionFalsa()
    with pytest.raises(ModificacionNoPermitidaError):
        finalizar_servicio(db, expedicion=expedicion, estado_final="en_transito")
    finalizar_servicio(db, expedicion=expedicion, estado_final="entregado")
    primera = expedicion.fecha_finalizacion_servicio
    with pytest.raises(ModificacionNoPermitidaError):
        finalizar_servicio(db, expedicion=expedicion, estado_final="anulado")
    assert expedicion.estado == "entregado" and expedicion.fecha_finalizacion_servicio == primera
