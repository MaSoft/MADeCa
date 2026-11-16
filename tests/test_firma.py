"""Firma AdES con certificado .p12 cifrado en reposo — sin red ni BD."""

import io
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import get_settings
from app.security.secrets import cifrar
from app.services.certificado_service import EXTENSION_CIFRADO, guardar_certificado
from app.services.signing import FirmaError, firmar_pdf

PASSWORD = "clave-de-prueba"


def _p12_autofirmado() -> bytes:
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Pruebas MADeCa")])
    ahora = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=1))
        .not_valid_after(ahora + timedelta(days=30))
        .sign(clave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"prueba", clave, cert, None, serialization.BestAvailableEncryption(PASSWORD.encode())
    )


def _pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(50, 800, "DeCA de prueba")
    c.save()
    return buf.getvalue()


@pytest.fixture
def carpeta_certs(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "certs_local_path", tmp_path)
    return tmp_path


def test_certificado_se_guarda_cifrado_y_firma(carpeta_certs):
    p12 = _p12_autofirmado()
    datos = guardar_certificado(tenant_id=uuid.uuid4(), nombre="prueba", archivo_bytes=p12, password=PASSWORD)

    ruta = Path(datos["ruta_archivo"])
    assert ruta.name.endswith(EXTENSION_CIFRADO)
    assert ruta.read_bytes() != p12  # no queda en claro en disco

    firmado = firmar_pdf(
        pdf_bytes=_pdf(), ruta_certificado_p12=str(ruta), password_certificado_cifrada=datos["password_cifrado"]
    )
    assert b"/ByteRange" in firmado


def test_certificado_antiguo_en_claro_sigue_firmando(carpeta_certs):
    ruta = carpeta_certs / "antiguo.p12"
    ruta.write_bytes(_p12_autofirmado())
    firmado = firmar_pdf(
        pdf_bytes=_pdf(), ruta_certificado_p12=str(ruta), password_certificado_cifrada=cifrar(PASSWORD)
    )
    assert b"/ByteRange" in firmado


def test_password_incorrecta_da_error_de_firma(carpeta_certs):
    ruta = carpeta_certs / "antiguo.p12"
    ruta.write_bytes(_p12_autofirmado())
    with pytest.raises(FirmaError):
        firmar_pdf(pdf_bytes=_pdf(), ruta_certificado_p12=str(ruta), password_certificado_cifrada=cifrar("otra"))


def test_firma_dentro_de_un_bucle_asincrono(carpeta_certs):
    """Las rutas async de FastAPI llaman a firmar_pdf con un bucle de
    eventos en marcha; pyHanko usa asyncio.run por dentro y fallaba con
    «asyncio.run() cannot be called from a running event loop» (API de PDF,
    emitir desde la bandeja, panel)."""
    import asyncio

    ruta = carpeta_certs / "antiguo.p12"
    ruta.write_bytes(_p12_autofirmado())

    async def ruta_async():
        return firmar_pdf(pdf_bytes=_pdf(), ruta_certificado_p12=str(ruta), password_certificado_cifrada=cifrar(PASSWORD))

    assert b"/ByteRange" in asyncio.run(ruta_async())
