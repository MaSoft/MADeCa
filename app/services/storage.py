"""Abstracción de almacenamiento de PDFs firmados.

Backend "local" para desarrollo/instalación on-premise; "s3" (o
compatible, ej. MinIO) para despliegue en la nube. El resto de la
aplicación no debe conocer cuál está activo.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import get_settings


class StorageBackend:
    def guardar(self, tenant_id: uuid.UUID, expedicion_id: uuid.UUID, pdf_bytes: bytes, sufijo: str = "") -> str:
        """`sufijo` distingue versiones anteriores de un mismo DeCA (p. ej.
        ".v1") cuando se modifica durante el servicio (Apartado Quinto)."""
        raise NotImplementedError

    def guardar_archivo(self, tenant_id: uuid.UUID, clave: str, contenido: bytes) -> str:
        """Fichero auxiliar (p. ej. documento de origen de un borrador)
        bajo <tenant>/<clave>. `clave` la genera MADeCa, nunca el usuario."""
        raise NotImplementedError

    def leer(self, ruta: str) -> bytes:
        raise NotImplementedError

    def eliminar(self, ruta: str) -> None:
        raise NotImplementedError


class LocalStorage(StorageBackend):
    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _ruta(self, tenant_id: uuid.UUID, expedicion_id: uuid.UUID, sufijo: str = "") -> Path:
        carpeta = self.base_path / str(tenant_id)
        carpeta.mkdir(parents=True, exist_ok=True)
        return carpeta / f"{expedicion_id}{sufijo}.pdf"

    def guardar(self, tenant_id: uuid.UUID, expedicion_id: uuid.UUID, pdf_bytes: bytes, sufijo: str = "") -> str:
        ruta = self._ruta(tenant_id, expedicion_id, sufijo)
        ruta.write_bytes(pdf_bytes)
        return str(ruta)

    def guardar_archivo(self, tenant_id: uuid.UUID, clave: str, contenido: bytes) -> str:
        ruta = self.base_path / str(tenant_id) / clave
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
        return str(ruta)

    def leer(self, ruta: str) -> bytes:
        return Path(ruta).read_bytes()

    def eliminar(self, ruta: str) -> None:
        p = Path(ruta)
        if p.exists():
            p.unlink()


class S3Storage(StorageBackend):
    def __init__(self, endpoint_url: str, bucket: str, access_key: str, secret_key: str):
        import boto3  # import diferido: solo se necesita boto3 si se usa S3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def _key(self, tenant_id: uuid.UUID, expedicion_id: uuid.UUID, sufijo: str = "") -> str:
        return f"{tenant_id}/{expedicion_id}{sufijo}.pdf"

    def guardar(self, tenant_id: uuid.UUID, expedicion_id: uuid.UUID, pdf_bytes: bytes, sufijo: str = "") -> str:
        key = self._key(tenant_id, expedicion_id, sufijo)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=pdf_bytes, ContentType="application/pdf")
        return key

    def guardar_archivo(self, tenant_id: uuid.UUID, clave: str, contenido: bytes) -> str:
        key = f"{tenant_id}/{clave}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=contenido)
        return key

    def leer(self, ruta: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=ruta)
        return obj["Body"].read()

    def eliminar(self, ruta: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=ruta)


def get_storage() -> StorageBackend:
    settings = get_settings()
    if settings.storage_backend == "s3":
        if not (settings.s3_endpoint_url and settings.s3_bucket and settings.s3_access_key and settings.s3_secret_key):
            raise RuntimeError("STORAGE_BACKEND=s3 requiere S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY")
        return S3Storage(
            settings.s3_endpoint_url, settings.s3_bucket, settings.s3_access_key, settings.s3_secret_key
        )
    return LocalStorage(settings.storage_local_path)
