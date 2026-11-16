from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.domain.models import Tenant, Usuario
from app.security.auth import crear_access_token, hash_password, verificar_password
from app.security.rate_limit import limiter
from app.plantillas import crear_plantillas

router = APIRouter(tags=["auth"])
templates = crear_plantillas()

_HASH_SEÑUELO = hash_password("madeca-señuelo-sin-uso")


@router.get("/login")
def form_login(request: Request):
    return templates.TemplateResponse(request, "dashboard/login.html", {"error": None})


@router.post("/login")
@limiter.limit("10/minute")
def procesar_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # El correo es único por empresa (uq_usuario_tenant_email), no en toda
    # la plataforma: la misma persona puede tener usuario en dos empresas.
    # scalar_one_or_none() lanzaba MultipleResultsFound (error 500) en ese
    # caso. Se entra en la primera cuenta cuya contraseña coincida.
    candidatos = db.execute(
        select(Usuario)
        .where(Usuario.email == email.strip().lower(), Usuario.activo.is_(True))
        .order_by(Usuario.fecha_alta)
    ).scalars().all()
    usuario = next((u for u in candidatos if verificar_password(password, u.password_hash)), None)
    if not candidatos:
        # Mismo coste bcrypt exista o no el correo, para no revelar por
        # el tiempo de respuesta qué correos están dados de alta.
        verificar_password(password, _HASH_SEÑUELO)

    if usuario is None:
        return templates.TemplateResponse(
            request,
            "dashboard/login.html",
            {"error": "Credenciales incorrectas"},
            status_code=401,
        )

    tenant = db.get(Tenant, usuario.tenant_id)
    if tenant is None or not tenant.activo:
        return templates.TemplateResponse(
            request,
            "dashboard/login.html",
            {"error": "Esta empresa está desactivada. Contacta con el operador de la plataforma."},
            status_code=403,
        )

    token = crear_access_token(usuario_id=usuario.id, tenant_id=usuario.tenant_id, rol=usuario.rol)
    settings = get_settings()
    respuesta = RedirectResponse(url="/dashboard/inicio", status_code=303)
    respuesta.set_cookie(
        key="madeca_session",
        value=token,
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
    )
    return respuesta


@router.post("/logout")
def logout():
    respuesta = RedirectResponse(url="/login", status_code=303)
    respuesta.delete_cookie("madeca_session")
    return respuesta
