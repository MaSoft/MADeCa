"""Panel del operador de la plataforma MADeCa (no de una empresa cliente).

Aislado a propósito del dashboard de tenant: cookie de sesión distinta
(`madeca_superadmin_session`), claim JWT "tipo": "superadmin" verificado
en cada petición, y tabla `superadmins` sin relación con `tenants` ni
`usuarios`. Ver app/security/dependencies.py.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.domain.models import Expedicion, SuperAdmin, Tenant, Usuario
from app.domain.validation import validar_nif_cif
from app.security.auth import (
    crear_access_token_superadmin,
    generar_password_temporal,
    hash_password,
    inicio_sesiones_validas,
    validar_password_nueva,
    verificar_password,
)
from app.security.dependencies import SesionSuperAdmin, obtener_superadmin_actual
from app.security.rate_limit import limiter
from app.plantillas import crear_plantillas

router = APIRouter(prefix="/superadmin", tags=["superadmin"])
templates = crear_plantillas()

ROLES_VALIDOS = ("admin", "operador", "lectura")


@router.get("/login")
def form_login(request: Request):
    return templates.TemplateResponse(request, "superadmin/login.html", {"error": None})


@router.post("/login")
@limiter.limit("10/minute")
def procesar_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    superadmin = db.execute(
        select(SuperAdmin).where(SuperAdmin.email == email.strip().lower(), SuperAdmin.activo.is_(True))
    ).scalar_one_or_none()

    if superadmin is None or not verificar_password(password, superadmin.password_hash):
        return templates.TemplateResponse(
            request,
            "superadmin/login.html",
            {"error": "Credenciales incorrectas"},
            status_code=401,
        )

    token = crear_access_token_superadmin(superadmin_id=superadmin.id)
    settings = get_settings()
    respuesta = RedirectResponse(url="/superadmin/empresas", status_code=303)
    respuesta.set_cookie(
        key="madeca_superadmin_session",
        value=token,
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
    )
    return respuesta


@router.post("/logout")
def logout():
    respuesta = RedirectResponse(url="/superadmin/login", status_code=303)
    respuesta.delete_cookie("madeca_superadmin_session")
    return respuesta


@router.get("/empresas")
def listar_tenants(
    request: Request,
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
    error: str | None = None,
):
    tenants = db.execute(select(Tenant).order_by(Tenant.fecha_alta.desc())).scalars().all()

    ids = [t.id for t in tenants]
    conteos_usuarios: dict = {}
    conteos_expediciones: dict = {}
    if ids:
        conteos_usuarios = dict(
            db.execute(
                select(Usuario.tenant_id, func.count()).where(Usuario.tenant_id.in_(ids)).group_by(Usuario.tenant_id)
            ).all()
        )
        conteos_expediciones = dict(
            db.execute(
                select(Expedicion.tenant_id, func.count())
                .where(Expedicion.tenant_id.in_(ids))
                .group_by(Expedicion.tenant_id)
            ).all()
        )

    filas = [
        {
            "tenant": t,
            "num_usuarios": conteos_usuarios.get(t.id, 0),
            "num_expediciones": conteos_expediciones.get(t.id, 0),
        }
        for t in tenants
    ]

    return templates.TemplateResponse(
        request, "superadmin/empresas_list.html", {"filas": filas, "error": error}
    )


@router.post("/empresas")
def crear_tenant(
    nombre: str = Form(...),
    nif: str = Form(...),
    email_admin: str = Form(...),
    password_admin: str = Form(...),
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote

    try:
        nif_normalizado = validar_nif_cif(nif)
    except ValueError as exc:
        return RedirectResponse(url=f"/superadmin/empresas?error={quote(str(exc))}", status_code=303)

    existente = db.execute(select(Tenant).where(Tenant.nif_cif == nif_normalizado)).scalar_one_or_none()
    if existente is not None:
        return RedirectResponse(
            url=f"/superadmin/empresas?error={quote('Ya existe una empresa con ese NIF/CIF')}", status_code=303
        )

    nombre = nombre.strip()
    email_admin = email_admin.strip().lower()
    if not nombre or len(nombre) > 300:
        return RedirectResponse(url=f"/superadmin/empresas?error={quote('Nombre de empresa no válido')}", status_code=303)
    if "@" not in email_admin or len(email_admin) > 320:
        return RedirectResponse(url=f"/superadmin/empresas?error={quote('Correo no válido')}", status_code=303)
    try:
        password_hash = hash_password(validar_password_nueva(password_admin))
    except ValueError as exc:
        return RedirectResponse(url=f"/superadmin/empresas?error={quote(str(exc))}", status_code=303)

    tenant = Tenant(nombre=nombre, nif_cif=nif_normalizado)
    db.add(tenant)
    db.flush()

    usuario = Usuario(
        tenant_id=tenant.id,
        email=email_admin,
        password_hash=password_hash,
        rol="admin",
    )
    db.add(usuario)
    db.commit()

    return RedirectResponse(url="/superadmin/empresas", status_code=303)


@router.post("/empresas/{tenant_id}/desactivar")
def desactivar_tenant(
    tenant_id: uuid.UUID,
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Empresa no encontrada")
    tenant.activo = False
    db.add(tenant)
    db.commit()
    return RedirectResponse(url="/superadmin/empresas", status_code=303)


@router.post("/empresas/{tenant_id}/activar")
def activar_tenant(
    tenant_id: uuid.UUID,
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Empresa no encontrada")
    tenant.activo = True
    db.add(tenant)
    db.commit()
    return RedirectResponse(url="/superadmin/empresas", status_code=303)


# --- Usuarios de una empresa, vistos por el operador de la plataforma ---


def _pagina_usuarios(
    request: Request,
    db: Session,
    tenant_id: uuid.UUID,
    *,
    password_generada: str | None = None,
    usuario_email: str | None = None,
    error: str | None = None,
):
    """Las contraseñas temporales se muestran una sola vez en la propia
    respuesta del POST. Antes iban en la URL de una redirección y quedaban
    en el historial del navegador y en los registros del servidor."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Empresa no encontrada")

    usuarios = db.execute(
        select(Usuario).where(Usuario.tenant_id == tenant_id).order_by(Usuario.fecha_alta.desc())
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "superadmin/empresa_usuarios.html",
        {
            "tenant": tenant,
            "usuarios": usuarios,
            "password_generada": password_generada,
            "usuario_email": usuario_email,
            "error": error,
            "roles_validos": ROLES_VALIDOS,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/empresas/{tenant_id}/usuarios")
def listar_usuarios_de_empresa(
    request: Request,
    tenant_id: uuid.UUID,
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
    error: str | None = None,
):
    return _pagina_usuarios(request, db, tenant_id, error=error)


@router.post("/empresas/{tenant_id}/usuarios/{usuario_id}/resetear-password")
def resetear_password_usuario(
    request: Request,
    tenant_id: uuid.UUID,
    usuario_id: uuid.UUID,
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
):
    usuario = db.get(Usuario, usuario_id)
    if usuario is None or usuario.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado en esta empresa")

    nueva_password = generar_password_temporal()
    usuario.password_hash = hash_password(nueva_password)
    # Invalida las sesiones abiertas con la contraseña anterior (p. ej. si
    # el reseteo es porque la contraseña se ha visto comprometida).
    usuario.sesiones_validas_desde = inicio_sesiones_validas()
    db.add(usuario)
    db.commit()

    return _pagina_usuarios(
        request, db, tenant_id, password_generada=nueva_password, usuario_email=usuario.email
    )


@router.post("/empresas/{tenant_id}/usuarios")
def crear_usuario_empresa(
    request: Request,
    tenant_id: uuid.UUID,
    email: str = Form(...),
    rol: str = Form("operador"),
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Empresa no encontrada")

    if rol not in ROLES_VALIDOS:
        return RedirectResponse(
            url=f"/superadmin/empresas/{tenant_id}/usuarios?error={quote('Rol no válido')}", status_code=303
        )

    email_normalizado = email.strip().lower()
    existente = db.execute(
        select(Usuario).where(Usuario.tenant_id == tenant_id, Usuario.email == email_normalizado)
    ).scalar_one_or_none()
    if existente is not None:
        return RedirectResponse(
            url=f"/superadmin/empresas/{tenant_id}/usuarios?error={quote('Ya existe un usuario con ese correo en esta empresa')}",
            status_code=303,
        )

    nueva_password = generar_password_temporal()
    db.add(
        Usuario(
            tenant_id=tenant_id,
            email=email_normalizado,
            password_hash=hash_password(nueva_password),
            rol=rol,
        )
    )
    db.commit()

    return _pagina_usuarios(
        request, db, tenant_id, password_generada=nueva_password, usuario_email=email_normalizado
    )


@router.post("/empresas/{tenant_id}/usuarios/{usuario_id}/rol")
def cambiar_rol_usuario(
    tenant_id: uuid.UUID,
    usuario_id: uuid.UUID,
    rol: str = Form(...),
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
):
    from urllib.parse import quote

    if rol not in ROLES_VALIDOS:
        return RedirectResponse(
            url=f"/superadmin/empresas/{tenant_id}/usuarios?error={quote('Rol no válido')}", status_code=303
        )

    usuario = db.get(Usuario, usuario_id)
    if usuario is None or usuario.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado en esta empresa")

    usuario.rol = rol
    db.add(usuario)
    db.commit()
    return RedirectResponse(url=f"/superadmin/empresas/{tenant_id}/usuarios", status_code=303)


@router.post("/empresas/{tenant_id}/usuarios/{usuario_id}/desactivar")
def desactivar_usuario_empresa(
    tenant_id: uuid.UUID,
    usuario_id: uuid.UUID,
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
):
    usuario = db.get(Usuario, usuario_id)
    if usuario is None or usuario.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado en esta empresa")
    usuario.activo = False
    db.add(usuario)
    db.commit()
    return RedirectResponse(url=f"/superadmin/empresas/{tenant_id}/usuarios", status_code=303)


@router.post("/empresas/{tenant_id}/usuarios/{usuario_id}/activar")
def activar_usuario_empresa(
    tenant_id: uuid.UUID,
    usuario_id: uuid.UUID,
    sesion: SesionSuperAdmin = Depends(obtener_superadmin_actual),
    db: Session = Depends(get_db),
):
    usuario = db.get(Usuario, usuario_id)
    if usuario is None or usuario.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado en esta empresa")
    usuario.activo = True
    db.add(usuario)
    db.commit()
    return RedirectResponse(url=f"/superadmin/empresas/{tenant_id}/usuarios", status_code=303)
