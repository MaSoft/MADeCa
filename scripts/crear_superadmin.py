"""Alta del primer superadmin (operador de la plataforma). Necesario una
sola vez para poder entrar en /superadmin y dar de alta tenants desde el
navegador — es el huevo y la gallina de ese panel. Uso:

    python -m scripts.crear_superadmin --email operador@masoft.es --password "una-clave-de-12-o-mas"
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.database import SessionLocal
from app.domain.models import SuperAdmin
from app.security.auth import hash_password, validar_password_nueva


def main() -> None:
    parser = argparse.ArgumentParser(description="Crear el primer superadmin de la plataforma")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    email_normalizado = args.email.strip().lower()

    db = SessionLocal()
    try:
        existente = db.execute(
            select(SuperAdmin).where(SuperAdmin.email == email_normalizado)
        ).scalar_one_or_none()
        if existente is not None:
            print(f"Ya existe un superadmin con el correo {email_normalizado}")
            return

        superadmin = SuperAdmin(email=email_normalizado, password_hash=hash_password(validar_password_nueva(args.password)))
        db.add(superadmin)
        db.commit()
        print(f"Superadmin creado: {superadmin.id} ({superadmin.email})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
