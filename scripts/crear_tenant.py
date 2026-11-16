"""Utilidad de línea de comandos para dar de alta un tenant y su primer
usuario administrador. Uso:

    python -m scripts.crear_tenant --nombre "Transportes Ejemplo SL" \
        --nif B00345678 --email admin@ejemplo.es --password "una-clave-de-12-o-mas"
"""

from __future__ import annotations

import argparse

from app.database import SessionLocal
from app.domain.models import Tenant, Usuario
from app.domain.validation import validar_nif_cif
from app.security.auth import hash_password, validar_password_nueva


def main() -> None:
    parser = argparse.ArgumentParser(description="Crear un tenant y su usuario administrador inicial")
    parser.add_argument("--nombre", required=True)
    parser.add_argument("--nif", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    nif_normalizado = validar_nif_cif(args.nif)

    db = SessionLocal()
    try:
        tenant = Tenant(nombre=args.nombre, nif_cif=nif_normalizado)
        db.add(tenant)
        db.flush()

        usuario = Usuario(
            tenant_id=tenant.id,
            email=args.email.strip().lower(),
            password_hash=hash_password(validar_password_nueva(args.password)),
            rol="admin",
        )
        db.add(usuario)
        db.commit()
        print(f"Tenant creado: {tenant.id} ({tenant.nombre})")
        print(f"Usuario admin: {usuario.email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
