"""Carga una empresa de demostración para probar MADeCa.

Crea (si no existen) la empresa ficticia «Distribuciones Demo SL», un
usuario por rol (admin, operador, lectura) y una clave de API, y
opcionalmente un operador de plataforma (superadmin). Las contraseñas y la
clave se generan al azar y se muestran UNA sola vez.

    python -m scripts.cargar_demo
    python -m scripts.cargar_demo --con-operador
    python -m scripts.cargar_demo --nuevas-claves   # regenera contraseñas y clave

En Docker:
    docker compose exec app python -m scripts.cargar_demo --con-operador

Solo para entornos de prueba: no ejecutar en producción.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.domain.models import ErpApiKey, SuperAdmin, Tenant, Usuario
from app.security.auth import generar_api_key, generar_password_temporal, hash_password, inicio_sesiones_validas

NIF_DEMO = "B00345678"
NOMBRE_DEMO = "Distribuciones Demo SL"
USUARIOS_DEMO = (
    ("admin@demo.madeca.local", "admin"),
    ("operador@demo.madeca.local", "operador"),
    ("lectura@demo.madeca.local", "lectura"),
)
OPERADOR_DEMO = "operador-plataforma@demo.madeca.local"


def main() -> None:
    parser = argparse.ArgumentParser(description="Cargar la empresa de demostración")
    parser.add_argument("--con-operador", action="store_true", help="Crear también un operador de plataforma (superadmin)")
    parser.add_argument("--nuevas-claves", action="store_true", help="Regenerar contraseñas de la demo y crear una clave de API nueva")
    args = parser.parse_args()

    if get_settings().app_env == "production":
        raise SystemExit("APP_ENV=production: la demo no se carga en producción.")

    db = SessionLocal()
    credenciales: list[tuple[str, str, str]] = []
    try:
        tenant = db.execute(select(Tenant).where(Tenant.nif_cif == NIF_DEMO)).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(nombre=NOMBRE_DEMO, nif_cif=NIF_DEMO)
            db.add(tenant)
            db.flush()

        for email, rol in USUARIOS_DEMO:
            usuario = db.execute(
                select(Usuario).where(Usuario.tenant_id == tenant.id, Usuario.email == email)
            ).scalar_one_or_none()
            if usuario is None or args.nuevas_claves:
                password = generar_password_temporal()
                if usuario is None:
                    usuario = Usuario(tenant_id=tenant.id, email=email, rol=rol, password_hash=hash_password(password))
                else:
                    usuario.password_hash = hash_password(password)
                    usuario.sesiones_validas_desde = inicio_sesiones_validas()
                db.add(usuario)
                credenciales.append((f"Panel de empresa ({rol})", email, password))

        clave_api = None
        tiene_clave = db.execute(
            select(ErpApiKey).where(ErpApiKey.tenant_id == tenant.id, ErpApiKey.activo.is_(True))
        ).scalars().first()
        if tiene_clave is None or args.nuevas_claves:
            clave_api, prefijo, key_hash = generar_api_key()
            db.add(ErpApiKey(tenant_id=tenant.id, nombre="Demo", prefijo=prefijo, key_hash=key_hash, erp_origen="generico"))

        if args.con_operador:
            operador = db.execute(select(SuperAdmin).where(SuperAdmin.email == OPERADOR_DEMO)).scalar_one_or_none()
            if operador is None or args.nuevas_claves:
                password = generar_password_temporal()
                if operador is None:
                    operador = SuperAdmin(email=OPERADOR_DEMO, password_hash=hash_password(password))
                else:
                    operador.password_hash = hash_password(password)
                db.add(operador)
                credenciales.append(("Panel de operador (/superadmin)", OPERADOR_DEMO, password))

        db.commit()

        print(f"\nEmpresa demo: {tenant.nombre} ({tenant.nif_cif})")
        print(f"Id de la empresa: {tenant.id}")
        print(f"Carpeta del hot-folder: {get_settings().hotfolder_incoming_path.as_posix()}/{tenant.id}/generico/pdf  y  .../datos\n")
        if credenciales:
            print("Credenciales (se muestran solo ahora; guárdalas):")
            for donde, email, password in credenciales:
                print(f"  {donde:35} {email:40} {password}")
        else:
            print("Usuarios ya existentes: usa --nuevas-claves para generar contraseñas nuevas.")
        if clave_api:
            print(f"\nClave de API (solo ahora): {clave_api}")
            print('  PowerShell:  $env:MADECA_API_KEY = "' + clave_api + '"')
        else:
            print("\nYa hay una clave de API activa; --nuevas-claves crea otra.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
