from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_secret_key: str
    app_base_url: str

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str
    jwt_expire_minutes: int = 480

    storage_backend: str = "local"
    storage_local_path: Path = Path("./storage/pdfs")
    certs_local_path: Path = Path("./certs")
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    # Apartado Segundo.4 BOE-A-2026-12784: conservación mínima 1 año.
    retention_days: int = 365
    # Apartado Tercero.5 BOE-A-2026-12784: la URL puede desactivarse
    # transcurridos 7 días desde la finalización del servicio.
    public_url_deactivation_days: int = 7

    # Zona de las horas que llegan sin desfase y de lo que se muestra en el
    # panel: Europe/Madrid (península, Baleares, Ceuta, Melilla) o
    # Atlantic/Canary para una instalación canaria.
    zona_horaria: str = "Europe/Madrid"

    hotfolder_incoming_path: Path = Path("./hotfolder_watch/incoming")
    hotfolder_processed_path: Path = Path("./hotfolder_watch/processed")
    hotfolder_error_path: Path = Path("./hotfolder_watch/errores")

    # --- Obtención de datos de documentos (bandeja de revisión) ---
    # OCR local en el servidor de MADeCa (los datos no salen de él).
    tesseract_ruta: str | None = None
    # Tiempo máximo de OCR por página y lecturas simultáneas por proceso.
    # Sin límite, una sola foto difícil dejaba el servidor sin responder.
    ocr_limite_segundos: int = 60
    ocr_simultaneos: int = 2
    # OCR de un tercero (Azure AI Document Intelligence, prebuilt-read).
    # Solo se usa para las empresas que lo autoricen expresamente
    # (subencargado, art. 28.2 RGPD). Usar un recurso en región UE.
    azure_di_endpoint: str | None = None
    azure_di_clave: str | None = None
    # Tamaño máximo de un documento de origen.
    documento_origen_max_bytes: int = 20 * 1024 * 1024

    # --- Fuentes de datos automáticas (app/conectores) ---
    # Solo instalaciones propias: permite que las fuentes apunten a redes
    # privadas. En la nube debe quedar en False (protección SSRF).
    fuentes_permitir_red_privada: bool = False
    # Solo instalaciones propias: carpeta montada en el servidor (p. ej. un
    # NAS); cada empresa solo ve <base>/<id_empresa>/. Vacío = desactivado.
    fuentes_carpeta_base: Path | None = None
    # Cada cuánto revisa el proceso de conectores si toca sincronizar.
    conectores_ciclo_segundos: int = 60
    # Resumen diario de avisos por correo a los administradores (certificados
    # por caducar, borradores atascados, fuentes con error). Sin SMTP_HOST
    # queda desactivado. La clave solo en .env, nunca en el código.
    smtp_host: str | None = None
    smtp_puerto: int = 587
    smtp_usuario: str | None = None
    smtp_clave: str | None = None
    smtp_remitente: str | None = None
    smtp_seguridad: str = "starttls"  # starttls | ssl | ninguna
    avisos_correo_hora: int = 8  # hora local a partir de la que se envía

    @model_validator(mode="after")
    def _correo_valido(self) -> "Settings":
        if self.smtp_seguridad not in ("starttls", "ssl", "ninguna"):
            raise ValueError("SMTP_SEGURIDAD debe ser starttls, ssl o ninguna")
        if not 0 <= self.avisos_correo_hora <= 23:
            raise ValueError("AVISOS_CORREO_HORA debe estar entre 0 y 23")
        return self

    @model_validator(mode="after")
    def _zona_horaria_valida(self) -> "Settings":
        from app.zonas import validar_zona

        self.zona_horaria = validar_zona(self.zona_horaria)
        return self

    @model_validator(mode="after")
    def _claves_robustas_fuera_de_desarrollo(self) -> "Settings":
        # Con las claves de ejemplo de .env.example cualquiera podría
        # firmar sesiones válidas (JWT) o descifrar los certificados .p12
        # (Fernet derivado de APP_SECRET_KEY). Fuera de desarrollo, no se
        # arranca con claves cortas o de ejemplo.
        if self.app_env == "development":
            return self
        for nombre in ("app_secret_key", "jwt_secret_key"):
            valor = getattr(self, nombre)
            if len(valor) < 32 or valor.startswith("cambia-esto"):
                raise ValueError(
                    f"{nombre.upper()} debe ser un valor aleatorio propio de al menos 32 caracteres "
                    "(p. ej. python -c \"import secrets; print(secrets.token_urlsafe(48))\")"
                )
        if self.app_secret_key == self.jwt_secret_key:
            raise ValueError("APP_SECRET_KEY y JWT_SECRET_KEY deben ser distintas")
        # Apartado Tercero.1 BOE-A-2026-12784: la URL del QR debe empezar
        # necesariamente por https://. Antes solo se comprobaba al emitir:
        # MADeCa arrancaba y luego fallaba cada DeCA con un error 500.
        if not self.app_base_url.lower().startswith("https://"):
            raise ValueError(
                "APP_BASE_URL debe empezar por https:// fuera de desarrollo "
                "(Apartado Tercero.1 BOE-A-2026-12784)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
