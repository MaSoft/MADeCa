"""Alta de certificados digitales (.p12/.pfx) para firma AdES.

Valida el certificado en el momento de la subida (falla rápido si la
contraseña es incorrecta o el fichero no es un PKCS#12 válido) en lugar
de descubrirlo la primera vez que se intenta firmar un DeCA real.
"""

from __future__ import annotations

import uuid

from cryptography.hazmat.primitives.serialization import pkcs12

from app.config import get_settings
from app.security.secrets import cifrar, cifrar_bytes

EXTENSION_CIFRADO = ".p12.enc"


class CertificadoInvalidoError(Exception):
    pass


def validar_y_extraer_metadata(archivo_bytes: bytes, password: str) -> tuple[str | None, object | None]:
    try:
        _clave, certificado, _cadena = pkcs12.load_key_and_certificates(
            archivo_bytes, password.encode("utf-8")
        )
    except Exception as exc:
        raise CertificadoInvalidoError(
            f"No se pudo abrir el certificado: contraseña incorrecta o "
            f"fichero .p12/.pfx no válido ({exc})"
        ) from exc

    if certificado is None:
        raise CertificadoInvalidoError("El fichero no contiene un certificado X.509")

    titular_cn = None
    try:
        titular_cn = certificado.subject.rfc4514_string()
    except Exception:
        pass

    return titular_cn, certificado.not_valid_after_utc


def guardar_certificado(
    *, tenant_id: uuid.UUID, nombre: str, archivo_bytes: bytes, password: str
) -> dict:
    titular_cn, fecha_caducidad = validar_y_extraer_metadata(archivo_bytes, password)

    settings = get_settings()
    carpeta = settings.certs_local_path / str(tenant_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    cert_id = uuid.uuid4()
    # El .p12 se guardaba en claro: quien leyera el volumen de certificados
    # tenía la clave privada a falta solo de la contraseña. Ahora se cifra
    # (Fernet) con la misma clave de aplicación que protege la contraseña.
    ruta = carpeta / f"{cert_id}{EXTENSION_CIFRADO}"
    ruta.write_bytes(cifrar_bytes(archivo_bytes))

    return {
        "id": cert_id,
        "nombre": nombre,
        "ruta_archivo": str(ruta),
        "password_cifrado": cifrar(password),
        "titular_cn": titular_cn,
        "fecha_caducidad": fecha_caducidad,
    }
