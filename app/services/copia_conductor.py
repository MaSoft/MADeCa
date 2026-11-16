"""Copia del DeCA para el conductor — Apartado Séptimo, BOE-A-2026-12784.

Antes del inicio del servicio hay que entregar al conductor una copia
electrónica (móvil) o impresa, en ambos casos con el QR. El QR puede
existir también como fichero independiente. Estas descargas son para el
emisor (panel o API), no para el público: el acceso del agente sigue
siendo /inspect/{id}.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.responses import Response

from app.config import get_settings
from app.domain.models import Expedicion
from app.services.qr_service import construir_url_publica, generar_qr_png_bytes
from app.services.storage import get_storage


def _nombre_seguro(texto: str) -> str:
    """Solo caracteres seguros en Content-Disposition (la referencia
    externa la manda el ERP y podría traer comillas o saltos de línea)."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in texto)[:80]


def disposicion(tipo: str, nombre: str) -> str:
    """Cabecera Content-Disposition con nombre ASCII y, aparte, el nombre
    completo en UTF-8 (RFC 6266). Con letras fuera de latin-1 en el nombre
    (p. ej. «ł» o «€» en una referencia) la respuesta daba error 500."""
    ascii_ = "".join(c if c.isascii() and (c.isalnum() or c in "-_.") else "_" for c in nombre)[:120] or "documento"
    return f"{tipo}; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nombre[:120])}"


def respuesta_pdf(exp: Expedicion) -> Response:
    return Response(
        content=get_storage().leer(exp.storage_path),
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposicion("attachment", f"DeCA_{_nombre_seguro(exp.referencia_externa)}.pdf"),
            "Cache-Control": "no-store",
        },
    )


def respuesta_qr(exp: Expedicion) -> Response:
    url = construir_url_publica(get_settings().app_base_url, exp.id)
    return Response(
        content=generar_qr_png_bytes(url),
        media_type="image/png",
        headers={
            "Content-Disposition": disposicion("attachment", f"QR_DeCA_{_nombre_seguro(exp.referencia_externa)}.png"),
            "Cache-Control": "no-store",
        },
    )
