"""Generación del código QR de inspección.

Apartado Tercero, BOE-A-2026-12784:
  1. La URL debe usar HTTPS bajo TLS 1.2 o superior.
  2. Puede incorporar tokens/claves de expiración, pero no debe caducar
     antes de finalizar el servicio.
  3. Debe permitir la descarga directa del PDF sin autenticación.
  4. No es válida ninguna URL que exija credenciales.
  5. Transcurridos 7 días desde la finalización del servicio, la
     descarga puede desactivarse.

El identificador de la expedición es un UUIDv4 (ver Expedicion.id) —
no correlativo, no enumerable — por lo que la propia URL ya cumple el
requisito de opacidad sin necesidad de un token adicional.
"""

from __future__ import annotations

import base64
import io
import uuid

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def construir_url_publica(base_url: str, expedicion_id: uuid.UUID) -> str:
    base = base_url.rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://localhost"):
        raise ValueError(
            "APP_BASE_URL debe usar HTTPS en producción (Apartado Tercero.1 BOE-A-2026-12784)"
        )
    return f"{base}/inspect/{expedicion_id}"


def generar_qr_png_bytes(url: str) -> bytes:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generar_qr_data_uri(url: str) -> str:
    png_bytes = generar_qr_png_bytes(url)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"
