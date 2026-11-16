"""Piezas comunes de los conectores."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.config import get_settings

# Máximo de elementos por ejecución: acota tiempo y memoria; lo que quede
# se recoge en la siguiente pasada (cada conector guarda por dónde iba).
MAX_ELEMENTOS_POR_EJECUCION = 50
TIMEOUT_HTTP = httpx.Timeout(30.0, connect=10.0)


def siguientes_por_marca(candidatos, clave, estado: dict) -> tuple[list, dict]:
    """Los siguientes MAX_ELEMENTOS_POR_EJECUCION tras la marca guardada,
    en orden de `clave` = (fecha, desempate). Guardar solo la fecha y
    seguir con «fecha > última» perdía los que compartían la fecha del
    último leído y quedaban tras el corte (p. ej. muchos ficheros copiados
    al NAS en el mismo segundo). Con el desempate no se pierde ni se
    repite ninguno."""
    marca = (float(estado.get("desde", 0)), estado.get("desde_nombre", ""))
    elegidos = sorted((c for c in candidatos if clave(c) > marca), key=clave)[:MAX_ELEMENTOS_POR_EJECUCION]
    if not elegidos:
        return [], {"desde": marca[0], "desde_nombre": marca[1]}
    ultima = clave(elegidos[-1])
    return elegidos, {"desde": ultima[0], "desde_nombre": ultima[1]}


class ConectorError(Exception):
    """Fallo de configuración o de comunicación con la fuente. El mensaje
    se muestra al usuario: no debe contener credenciales."""


@dataclass
class CampoConfig:
    nombre: str
    etiqueta: str
    secreto: bool = False
    obligatorio: bool = True
    ayuda: str = ""
    defecto: str = ""


@dataclass
class Elemento:
    """Un documento (contenido) o un registro de ERP ya estructurado
    (datos) que se convertirá en borrador."""

    referencia: str
    nombre: str
    contenido: bytes | None = None
    datos: dict | None = None
    confianza: dict = field(default_factory=dict)
    avisos: list = field(default_factory=list)


class Conector:
    tipo = "base"
    titulo = "Base"
    categoria = "documentos"  # o "erp"
    descripcion = ""
    CAMPOS: list[CampoConfig] = []

    def __init__(self, config: dict, cliente: httpx.Client | None = None):
        faltan = [c.etiqueta for c in self.CAMPOS if c.obligatorio and not str(config.get(c.nombre, "")).strip()]
        if faltan:
            raise ConectorError(f"Faltan datos de configuración: {', '.join(faltan)}")
        self.config = config
        self.cliente = cliente or httpx.Client(timeout=TIMEOUT_HTTP, follow_redirects=False)

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        """Devuelve los elementos nuevos desde `estado` y el estado nuevo."""
        raise NotImplementedError

    def probar(self) -> str:
        """Comprueba credenciales y acceso. Devuelve un mensaje para el usuario."""
        raise NotImplementedError

    # --- utilidades ---
    def _json(self, respuesta: httpx.Response, que: str) -> dict | list:
        if respuesta.status_code >= 400:
            raise ConectorError(f"{que}: error {respuesta.status_code} — {respuesta.text[:200]}")
        try:
            return respuesta.json()
        except ValueError as exc:
            raise ConectorError(f"{que}: respuesta no es JSON") from exc


def validar_url_externa(url: str, *, exigir_https: bool = True) -> str:
    """Rechaza URL que apunten a la red interna del servidor (SSRF):
    loopback, privadas, link-local (169.254.x, metadatos de nube)... salvo
    que el operador lo permita (instalaciones on-premise)."""
    partes = urlparse(url.strip())
    if exigir_https and partes.scheme != "https":
        raise ConectorError("La dirección debe empezar por https://")
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ConectorError("Dirección no válida")
    if get_settings().fuentes_permitir_red_privada:
        return url.strip()
    try:
        direcciones = {info[4][0] for info in socket.getaddrinfo(partes.hostname, partes.port or 443)}
    except socket.gaierror as exc:
        raise ConectorError(f"No se encuentra el servidor {partes.hostname}") from exc
    for d in direcciones:
        ip = ipaddress.ip_address(d.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ConectorError(
                f"{partes.hostname} apunta a una red privada ({ip}): no permitido desde MADeCa en la nube. "
                "Usa el conector local instalado en tu red."
            )
    return url.strip()


def token_entra_id(cliente: httpx.Client, *, tenant: str, client_id: str, secreto: str, scope: str) -> str:
    """Token de aplicación (client credentials) de Microsoft Entra ID."""
    r = cliente.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secreto, "scope": scope},
    )
    if r.status_code != 200:
        try:
            detalle = r.json().get("error_description", "")[:200]
        except ValueError:
            detalle = r.text[:200]
        raise ConectorError(f"Entra ID rechazó las credenciales: {detalle}")
    return r.json()["access_token"]


def ruta_valor(registro: dict, ruta: str):
    """'a.b.0.c' dentro de dicts/listas anidados."""
    valor = registro
    for parte in ruta.split("."):
        if isinstance(valor, list):
            try:
                valor = valor[int(parte)]
            except (ValueError, IndexError):
                return None
        elif isinstance(valor, dict):
            valor = valor.get(parte)
        else:
            return None
    return valor


def anidar(planos: dict) -> dict:
    """{'cargador.nif_cif': x} -> {'cargador': {'nif_cif': x}} sin vacíos."""
    datos: dict = {}
    for clave, valor in planos.items():
        if valor in (None, ""):
            continue
        if "." in clave:
            grupo, sub = clave.split(".", 1)
            datos.setdefault(grupo, {})[sub] = valor
        else:
            datos[clave] = valor
    return datos


def avisos_faltan(datos: dict) -> list[str]:
    obligatorios = {
        "cargador.nombre_razon_social": "nombre del cargador",
        "cargador.nif_cif": "NIF del cargador",
        "cargador.domicilio": "domicilio del cargador",
        "transportista.nombre_razon_social": "nombre del transportista",
        "transportista.nif_cif": "NIF del transportista",
        "origen_direccion": "lugar de origen",
        "destino_direccion": "lugar de destino",
        "mercancia.naturaleza": "naturaleza de la mercancía",
        "mercancia.peso_kg": "peso de la mercancía",
        "fecha_transporte": "fecha del transporte",
        "matricula_vehiculo": "matrícula del vehículo",
    }
    faltan = []
    for ruta, texto in obligatorios.items():
        grupo, _, sub = ruta.partition(".")
        valor = (datos.get(grupo) or {}).get(sub) if sub else datos.get(grupo)
        if not valor:
            faltan.append(f"No viene del ERP: {texto}")
    return faltan
