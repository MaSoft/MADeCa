from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings

# --- Contraseñas de usuario del dashboard (bcrypt, lento a propósito) ---
#
# Se usa la librería `bcrypt` directamente en vez de passlib: passlib está
# sin mantenimiento desde 2020 y rompe con bcrypt>=4.1 (referencia
# `_bcrypt.__about__` eliminada). bcrypt trunca internamente a 72 bytes de
# entrada; se valida aquí para no aceptar en silencio una contraseña más
# larga cuyo resto nunca se comprobaría.

_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    datos = password.encode("utf-8")
    if len(datos) > _BCRYPT_MAX_BYTES:
        raise ValueError(f"La contraseña no puede superar {_BCRYPT_MAX_BYTES} bytes")
    return bcrypt.hashpw(datos, bcrypt.gensalt()).decode("ascii")


LONGITUD_MINIMA_PASSWORD = 12


def validar_password_nueva(password: str) -> str:
    """Contraseña que escribe una persona (alta de usuario o de empresa).
    Antes se aceptaba cualquiera, incluso vacía. Las temporales que genera
    MADeCa (generar_password_temporal) ya tienen 16 caracteres."""
    if len(password or "") < LONGITUD_MINIMA_PASSWORD:
        raise ValueError(f"La contraseña debe tener al menos {LONGITUD_MINIMA_PASSWORD} caracteres")
    return password


def verificar_password(password: str, password_hash: str) -> bool:
    datos = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(datos, password_hash.encode("ascii"))
    except ValueError:
        return False


_ALFABETO_PASSWORD_TEMPORAL = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generar_password_temporal(longitud: int = 16) -> str:
    """Contraseña aleatoria para reseteos por superadmin. Excluye
    caracteres ambiguos (0/O, 1/l/I) para que se pueda transcribir a mano
    sin errores si hace falta comunicarla por teléfono."""
    return "".join(secrets.choice(_ALFABETO_PASSWORD_TEMPORAL) for _ in range(longitud))


# --- JWT de sesión del dashboard (por tenant) ---
#
# Todo token lleva un claim "tipo" ("tenant" o "superadmin"). Además de
# usarse en cookies con nombres distintos, cada dependencia de FastAPI
# comprueba explícitamente ese claim antes de confiar en el resto del
# payload — así un token de tenant nunca puede colarse como sesión de
# superadmin ni viceversa, aunque alguien copie la cookie a mano.


def crear_access_token(*, usuario_id: uuid.UUID, tenant_id: uuid.UUID, rol: str) -> str:
    settings = get_settings()
    ahora = datetime.now(timezone.utc)
    payload = {
        "tipo": "tenant",
        "sub": str(usuario_id),
        "tenant_id": str(tenant_id),
        "rol": rol,
        "iat": ahora,
        "exp": ahora + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def inicio_sesiones_validas() -> datetime:
    """Momento a partir del cual se aceptan los tokens de un usuario.

    Se trunca al segundo porque el claim "iat" del JWT va en segundos
    enteros: así un login hecho en el mismo segundo del corte sigue siendo
    válido y los tokens emitidos antes del corte no."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def decodificar_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])


# --- JWT de sesión del superadmin (operador de la plataforma) ---


def crear_access_token_superadmin(*, superadmin_id: uuid.UUID) -> str:
    settings = get_settings()
    ahora = datetime.now(timezone.utc)
    payload = {
        "tipo": "superadmin",
        "sub": str(superadmin_id),
        "iat": ahora,
        "exp": ahora + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


# --- API keys de ingesta ERP (alta entropía, hash rápido SHA-256 basta) ---

_PREFIJO_API_KEY = "mad_live_"


def generar_api_key() -> tuple[str, str, str]:
    """Devuelve (clave_en_claro, prefijo_visible, hash_para_guardar).

    La clave en claro solo se muestra una vez al crearla; en BD solo se
    guarda el hash. El prefijo permite identificar la key en la UI sin
    exponerla entera.
    """
    secreto = secrets.token_urlsafe(32)
    clave_completa = f"{_PREFIJO_API_KEY}{secreto}"
    prefijo_visible = clave_completa[: len(_PREFIJO_API_KEY) + 6]
    hash_guardado = hashlib.sha256(clave_completa.encode("utf-8")).hexdigest()
    return clave_completa, prefijo_visible, hash_guardado


def hash_api_key(clave_en_claro: str) -> str:
    return hashlib.sha256(clave_en_claro.encode("utf-8")).hexdigest()


def verificar_api_key(clave_en_claro: str, hash_guardado: str) -> bool:
    return secrets.compare_digest(hash_api_key(clave_en_claro), hash_guardado)


def extraer_prefijo(clave_en_claro: str) -> str:
    """Mismo criterio de truncado usado en generar_api_key(), para poder
    filtrar candidatas en BD por prefijo antes de verificar el hash
    (evita un escaneo O(n) de todas las API keys activas en cada petición)."""
    return clave_en_claro[: len(_PREFIJO_API_KEY) + 6]
