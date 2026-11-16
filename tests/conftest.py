"""Fija variables de entorno mínimas antes de que se importe app.config,
para que los tests puramente unitarios (que no tocan Postgres/Redis)
puedan importar el paquete `app` sin necesitar un .env real.
"""

import os

os.environ.setdefault("APP_SECRET_KEY", "clave-de-test-no-usar-en-produccion")
os.environ.setdefault("APP_BASE_URL", "https://deca.test.local")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET_KEY", "clave-jwt-de-test-no-usar-en-produccion")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
