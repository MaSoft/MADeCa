"""Firma electrónica avanzada (AdES) del PDF del DeCA.

Apartado Cuarto, BOE-A-2026-12784: la firma electrónica NO es obligatoria
para el DeCA en general. Es exigible únicamente cuando el documento tenga
finalidad contractual, y en ese caso basta con una firma electrónica
avanzada (AdES) conforme al Reglamento (UE) 910/2014 — no se exige sello
de tiempo (TSA) ni validación a largo plazo (LTV/PAdES-LTV), y por tanto
no se implementan aquí: añadirlos sin que la norma o el cliente los pida
sería complejidad no justificada.

Se usa pyHanko para producir una firma PAdES-B (baseline AdES) con el
certificado .p12/.pfx que el tenant ha subido para su empresa.
"""

from __future__ import annotations

import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pyhanko.sign import PdfSignatureMetadata, signers
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

from app.security.secrets import descifrar, descifrar_bytes


class FirmaError(Exception):
    pass


def firmar_pdf(
    *,
    pdf_bytes: bytes,
    ruta_certificado_p12: str,
    password_certificado_cifrada: str,
    nombre_campo_firma: str = "FirmaDeCA",
) -> bytes:
    """Aplica una firma AdES-B sobre el PDF y devuelve los bytes firmados."""
    try:
        password_claro = descifrar(password_certificado_cifrada)
        contenido = Path(ruta_certificado_p12).read_bytes()
        # Los certificados subidos antes de cifrar en reposo siguen en
        # claro (.p12); los nuevos van cifrados (.p12.enc).
        if ruta_certificado_p12.endswith(".enc"):
            contenido = descifrar_bytes(contenido)
        signer = signers.SimpleSigner.load_pkcs12_data(
            contenido,
            other_certs=[],
            passphrase=password_claro.encode("utf-8"),
        )
    except Exception as exc:  # certificado inválido, password incorrecta, etc.
        raise FirmaError(f"No se pudo cargar el certificado de firma: {exc}") from exc

    if signer is None:
        raise FirmaError("El certificado .p12 no contiene una clave/certificado válido")

    def _firmar():
        writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes))
        return signers.sign_pdf(
            writer,
            signature_meta=PdfSignatureMetadata(
                field_name=nombre_campo_firma,
                reason="Documento Electrónico de Control Administrativo (DeCA)",
            ),
            signer=signer,
        )

    try:
        # pyHanko firma con asyncio.run(), que falla si este hilo ya tiene
        # un bucle de eventos en marcha (rutas async de FastAPI). En ese
        # caso se firma en un hilo propio.
        try:
            asyncio.get_running_loop()
            en_bucle = True
        except RuntimeError:
            en_bucle = False
        if en_bucle:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="firma") as hilo:
                salida = hilo.submit(_firmar).result()
        else:
            salida = _firmar()
    except Exception as exc:
        raise FirmaError(f"Fallo al firmar el PDF: {exc}") from exc

    return salida.getvalue()
