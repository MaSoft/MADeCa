"""Resumen diario de avisos por correo a los administradores.

Lo lanza el proceso de conectores en cada pasada (app/conectores/worker.py):
a partir de AVISOS_CORREO_HORA (hora local), una vez al día y por empresa,
solo si hay avisos (los mismos del inicio, app/services/avisos.py). Cada
envío se marca en Redis para no repetirlo aunque se reinicie el proceso;
sin Redis no se envía (mejor ningún correo que uno cada minuto).

Datos: solo nombres de certificados y fuentes y recuentos; ningún dato de
las partes de los DeCA. Destinatarios: administradores activos de la propia
empresa.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.models import Tenant, Usuario
from app.services.avisos import avisos_empresa
from app.zonas import zona_predeterminada

logger = logging.getLogger("avisos_correo")

SEGUNDOS_MARCA = 2 * 24 * 3600
TIEMPO_SMTP = 20


def configurado() -> bool:
    return bool(get_settings().smtp_host)


def componer(empresa: str, avisos: list[dict], dia: str, base_url: str, remitente: str, destinatarios: list[str]) -> EmailMessage:
    """Mensaje en texto y HTML sencillo, con enlaces al panel."""
    base = base_url.rstrip("/")
    asunto = f"MADeCa · {len(avisos)} {'aviso' if len(avisos) == 1 else 'avisos'} de {empresa} ({dia})"
    lineas = [f"Avisos de {empresa} a {dia}:", ""]
    lineas += [f"- {'URGENTE: ' if a['nivel'] == 'grave' else ''}{a['texto']}  {base}{a['enlace']}" for a in avisos]
    lineas += ["", f"Panel: {base}/dashboard/inicio", "", "MADeCa - Desarrollado por Masoft.es (https://masoft.es)"]
    filas = "".join(
        f'<li style="margin:6px 0;color:{"#b91c1c" if a["nivel"] == "grave" else "#92400e"}">'
        f'<strong>{"Urgente" if a["nivel"] == "grave" else "Aviso"}:</strong> {escape(a["texto"])} '
        f'<a href="{escape(base + a["enlace"])}">Abrir</a></li>'
        for a in avisos
    )
    html = (
        f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1a1a2e">'
        f'<p>Avisos de <strong>{escape(empresa)}</strong> a {dia}:</p><ul style="padding-left:18px">{filas}</ul>'
        f'<p><a href="{escape(base)}/dashboard/inicio">Abrir el panel de MADeCa</a></p>'
        f'<p style="color:#64748b;font-size:12px">MADeCa - Desarrollado por <a href="https://masoft.es">Masoft.es</a></p></div>'
    )
    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = remitente
    mensaje["To"] = ", ".join(destinatarios)
    mensaje.set_content("\n".join(lineas))
    mensaje.add_alternative(html, subtype="html")
    return mensaje


def enviar(mensaje: EmailMessage) -> None:
    s = get_settings()
    contexto = ssl.create_default_context()
    if s.smtp_seguridad == "ssl":
        servidor = smtplib.SMTP_SSL(s.smtp_host, s.smtp_puerto, timeout=TIEMPO_SMTP, context=contexto)
    else:
        servidor = smtplib.SMTP(s.smtp_host, s.smtp_puerto, timeout=TIEMPO_SMTP)
    with servidor:
        if s.smtp_seguridad == "starttls":
            servidor.starttls(context=contexto)
        if s.smtp_usuario:
            servidor.login(s.smtp_usuario, s.smtp_clave or "")
        servidor.send_message(mensaje)


def _marcar(redis, clave: str) -> bool:
    """True si la marca es nueva (toca enviar)."""
    return bool(redis.set(clave, "1", nx=True, ex=SEGUNDOS_MARCA))


def pasada(db: Session, redis, ahora: datetime | None = None) -> int:
    """Envía los resúmenes que tocan. Devuelve cuántos correos salieron."""
    s = get_settings()
    if not s.smtp_host:
        return 0
    ahora = ahora or datetime.now(timezone.utc)
    local = ahora.astimezone(ZoneInfo(zona_predeterminada()))
    if local.hour < s.avisos_correo_hora:
        return 0
    dia = f"{local:%d/%m/%Y}"
    remitente = s.smtp_remitente or s.smtp_usuario or f"madeca@{s.smtp_host}"
    enviados = 0
    for tenant in db.execute(select(Tenant).where(Tenant.activo.is_(True))).scalars().all():
        clave = f"madeca:avisos-correo:{tenant.id}:{local:%Y-%m-%d}"
        try:
            ya_enviado = redis.exists(clave)
        except Exception:
            logger.warning("Sin Redis: no se envían avisos por correo en esta pasada")
            return enviados
        if ya_enviado:
            continue
        avisos = avisos_empresa(db, tenant.id, ahora)
        destinatarios = db.execute(
            select(Usuario.email).where(Usuario.tenant_id == tenant.id, Usuario.rol == "admin", Usuario.activo.is_(True))
        ).scalars().all()
        if not avisos or not destinatarios:
            _marcar(redis, clave)  # nada que contar hoy: no se vuelve a mirar hasta mañana
            continue
        if not _marcar(redis, clave):
            continue  # otro proceso se adelantó
        try:
            enviar(componer(tenant.nombre, avisos, dia, s.app_base_url, remitente, list(destinatarios)))
            enviados += 1
            logger.info("Avisos enviados a %s administrador(es) de la empresa %s", len(destinatarios), tenant.id)
        except (smtplib.SMTPException, OSError) as exc:
            redis.delete(clave)  # se reintenta en la siguiente pasada
            logger.error("No se pudo enviar el correo de avisos de la empresa %s: %s", tenant.id, exc.__class__.__name__)
    return enviados
