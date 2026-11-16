"""Tareas de instalación desde la consola del servidor (las usa madeca.sh del
paquete de instalación). Va dentro del paquete `app` para que en la imagen
compilada no quede ningún .py.

    python -c "from app.instalacion import main; main()" alta --empresa "Transportes X SL" --nif B00345678 --email admin@x.es
    python -c "from app.instalacion import main; main()" clave --email admin@x.es
    python -c "from app.instalacion import main; main()" superadmin --email operador@x.es
    python -c "from app.instalacion import main; main()" estado

Las contraseñas se generan aquí y se muestran una sola vez: nunca se pasan
por la línea de órdenes (quedarían en el historial del servidor).
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from app.version import VERSION


def _sesion():
    from app.database import SessionLocal

    return SessionLocal()


def _correo_valido(email: str) -> bool:
    return "@" in email and "." in email.split("@")[-1] and " " not in email


def alta(empresa: str, nif: str, email: str) -> int:
    from app.domain.models import Tenant, Usuario
    from app.domain.validation import validar_nif_cif
    from app.security.auth import generar_password_temporal, hash_password

    empresa = (empresa or "").strip()
    email = (email or "").strip().lower()
    if not empresa:
        print("Falta el nombre de la empresa.", file=sys.stderr)
        return 2
    if not _correo_valido(email):
        print(f"Correo no válido: {email!r}", file=sys.stderr)
        return 2
    try:
        nif_normalizado = validar_nif_cif(nif)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    db = _sesion()
    try:
        if db.execute(select(Tenant).where(Tenant.nif_cif == nif_normalizado)).first():
            print(f"Ya hay una empresa con el NIF {nif_normalizado} en esta instalación.", file=sys.stderr)
            return 3
        clave = generar_password_temporal()
        tenant = Tenant(nombre=empresa, nif_cif=nif_normalizado)
        db.add(tenant)
        db.flush()
        db.add(Usuario(tenant_id=tenant.id, email=email, password_hash=hash_password(clave), rol="admin"))
        db.commit()
        tenant_id = tenant.id
    finally:
        db.close()
    print(f"Empresa:        {empresa} ({nif_normalizado})")
    print(f"Id de la empresa: {tenant_id}")
    print(f"Administrador:  {email}")
    print(f"Contraseña:     {clave}")
    print("Guárdala ahora: no se vuelve a mostrar.")
    print("Para cambiarla o si se pierde: sudo /opt/madeca/madeca.sh clave " + email)
    return 0


def clave(email: str) -> int:
    from app.domain.models import Usuario
    from app.security.auth import generar_password_temporal, hash_password, inicio_sesiones_validas

    email = (email or "").strip().lower()
    db = _sesion()
    try:
        usuarios = db.execute(select(Usuario).where(Usuario.email == email)).scalars().all()
        if not usuarios:
            print(f"No hay ningún usuario con el correo {email}.", file=sys.stderr)
            return 4
        if len(usuarios) > 1:
            print(f"El correo {email} es usuario de varias empresas: cámbiala desde el panel de su administrador.", file=sys.stderr)
            return 4
        nueva = generar_password_temporal()
        u = usuarios[0]
        u.password_hash = hash_password(nueva)
        u.activo = True
        # Las sesiones abiertas con la contraseña anterior dejan de valer.
        u.sesiones_validas_desde = inicio_sesiones_validas()
        db.commit()
    finally:
        db.close()
    print(f"Usuario:        {email}")
    print(f"Contraseña:     {nueva}")
    print("Se han cerrado sus sesiones abiertas. Guárdala ahora: no se vuelve a mostrar.")
    return 0


def superadmin(email: str) -> int:
    """Operador de la plataforma (/superadmin): solo hace falta si en esta
    instalación se van a gestionar varias empresas desde el navegador."""
    from app.domain.models import SuperAdmin
    from app.security.auth import generar_password_temporal, hash_password

    email = (email or "").strip().lower()
    if not _correo_valido(email):
        print(f"Correo no válido: {email!r}", file=sys.stderr)
        return 2
    db = _sesion()
    try:
        existente = db.execute(select(SuperAdmin).where(SuperAdmin.email == email)).scalar_one_or_none()
        nueva = generar_password_temporal()
        if existente is None:
            db.add(SuperAdmin(email=email, password_hash=hash_password(nueva)))
        else:
            existente.password_hash = hash_password(nueva)
            existente.activo = True
        db.commit()
    finally:
        db.close()
    print(f"Superadministrador: {email} ({'nueva contraseña' if existente else 'creado'})")
    print(f"Contraseña:         {nueva}")
    print("Panel: https://<dominio>/superadmin · Guárdala ahora: no se vuelve a mostrar.")
    return 0


def estado() -> int:
    from app.domain.models import Expedicion, Tenant, Usuario

    db = _sesion()
    try:
        print(f"MADeCa {VERSION}")
        tenants = db.execute(select(Tenant).order_by(Tenant.nombre)).scalars().all()
        if not tenants:
            print("Sin empresa: falta el alta (sudo /opt/madeca/madeca.sh alta).")
            return 0
        for t in tenants:
            usuarios = db.execute(select(func.count()).select_from(Usuario).where(Usuario.tenant_id == t.id)).scalar_one()
            total = db.execute(select(func.count()).select_from(Expedicion).where(Expedicion.tenant_id == t.id)).scalar_one()
            print(f"Empresa: {t.nombre} ({t.nif_cif}) · usuarios: {usuarios} · DeCA emitidos: {total}")
            # Para la carpeta compartida: incoming/<id>/<erp>/<pdf|datos>/
            print(f"Id de la empresa: {t.id}")
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="madeca", description="MADeCa: tareas de instalación")
    sub = parser.add_subparsers(dest="orden", required=True)
    p = sub.add_parser("alta", help="Dar de alta una empresa y su administrador")
    p.add_argument("--empresa", required=True)
    p.add_argument("--nif", required=True)
    p.add_argument("--email", required=True)
    p = sub.add_parser("clave", help="Nueva contraseña para un usuario")
    p.add_argument("--email", required=True)
    p = sub.add_parser("superadmin", help="Crear el superadministrador o darle una contraseña nueva")
    p.add_argument("--email", required=True)
    sub.add_parser("estado", help="Ver las empresas dadas de alta")
    args = parser.parse_args(argv)
    if args.orden == "alta":
        sys.exit(alta(args.empresa, args.nif, args.email))
    if args.orden == "clave":
        sys.exit(clave(args.email))
    if args.orden == "superadmin":
        sys.exit(superadmin(args.email))
    sys.exit(estado())


if __name__ == "__main__":
    main()
