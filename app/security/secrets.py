"""Cifrado simétrico (Fernet/AES) de secretos que deben poder recuperarse
en claro para su uso (p. ej. la contraseña de un .p12 de firma).

No usar para contraseñas de usuario (esas van con bcrypt, one-way).
La clave deriva de APP_SECRET_KEY — en producción, sustituir por un KMS
gestionado (AWS KMS / Azure Key Vault / HashiCorp Vault) cuando el
volumen de certificados custodiados lo justifique.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    clave_derivada = hashlib.sha256(settings.app_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(clave_derivada))


def cifrar(valor_claro: str) -> str:
    return _fernet().encrypt(valor_claro.encode("utf-8")).decode("ascii")


def descifrar(valor_cifrado: str) -> str:
    return _fernet().decrypt(valor_cifrado.encode("ascii")).decode("utf-8")


def cifrar_bytes(datos: bytes) -> bytes:
    """Para ficheros binarios en reposo (p. ej. el propio .p12)."""
    return _fernet().encrypt(datos)


def descifrar_bytes(datos_cifrados: bytes) -> bytes:
    return _fernet().decrypt(datos_cifrados)
