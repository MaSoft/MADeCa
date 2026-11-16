"""Conectores de documentos: leen ficheros nuevos de una carpeta y cada
uno pasa por la extracción de datos (app.extraccion) a la bandeja."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import jwt

from app.config import get_settings
from app.conectores.base import (
    MAX_ELEMENTOS_POR_EJECUCION,
    CampoConfig,
    Conector,
    ConectorError,
    Elemento,
    siguientes_por_marca,
    token_entra_id,
    validar_url_externa,
)
from app.extraccion.texto import EXTENSIONES_SOPORTADAS

GRAPH = "https://graph.microsoft.com/v1.0"


def _soportado(nombre: str) -> bool:
    return any(nombre.lower().endswith(ext) for ext in EXTENSIONES_SOPORTADAS)


class SharePointOneDrive(Conector):
    """Microsoft Graph, permiso de aplicación Files.Read.All (el mínimo
    que documenta Microsoft para driveItem delta). El seguimiento de
    cambios (delta) solo existe en la raíz de la unidad: se filtran los
    ficheros cuyo padre es la carpeta elegida (no entra en subcarpetas)."""

    tipo = "sharepoint"
    titulo = "SharePoint / OneDrive"
    descripcion = "Lee los albaranes que se dejen en una carpeta de SharePoint u OneDrive de tu empresa."
    CAMPOS = [
        CampoConfig("tenant_azure", "Id del inquilino de Microsoft (tenant)"),
        CampoConfig("client_id", "Id de la aplicación registrada en Entra ID"),
        CampoConfig("client_secret", "Secreto de la aplicación", secreto=True),
        CampoConfig("drive_id", "Id de la unidad (biblioteca de documentos)", ayuda="GET /sites/{sitio}/drives"),
        CampoConfig("carpeta", "Carpeta dentro de la unidad", ayuda="Ej.: Albaranes/Pendientes"),
    ]

    def _token(self) -> str:
        return token_entra_id(
            self.cliente, tenant=self.config["tenant_azure"], client_id=self.config["client_id"],
            secreto=self.config["client_secret"], scope="https://graph.microsoft.com/.default",
        )

    def _carpeta_id(self, cab: dict) -> str:
        ruta = quote(self.config["carpeta"].strip("/"))
        datos = self._json(self.cliente.get(f"{GRAPH}/drives/{self.config['drive_id']}/root:/{ruta}", headers=cab), "Carpeta")
        if "folder" not in datos:
            raise ConectorError("La ruta indicada no es una carpeta")
        return datos["id"]

    def _descargar(self, item_id: str, cab: dict) -> bytes:
        r = self.cliente.get(f"{GRAPH}/drives/{self.config['drive_id']}/items/{item_id}/content", headers=cab)
        if r.status_code in (301, 302, 303, 307, 308):
            # Graph redirige a una URL de descarga ya autorizada (sin token).
            r = self.cliente.get(validar_url_externa(r.headers["Location"]))
        if r.status_code != 200:
            raise ConectorError(f"Descarga de {item_id}: error {r.status_code}")
        return r.content

    def probar(self) -> str:
        cab = {"Authorization": f"Bearer {self._token()}"}
        self._carpeta_id(cab)
        return "Conexión correcta y carpeta encontrada."

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        cab = {"Authorization": f"Bearer {self._token()}"}
        carpeta_id = estado.get("carpeta_id") or self._carpeta_id(cab)
        url = estado.get("siguiente") or estado.get("delta") or f"{GRAPH}/drives/{self.config['drive_id']}/root/delta"
        elementos: list[Elemento] = []
        while url:
            datos = self._json(self.cliente.get(url, headers=cab), "Cambios de la unidad")
            for item in datos.get("value", []):
                if "deleted" in item or "file" not in item:
                    continue
                if (item.get("parentReference") or {}).get("id") != carpeta_id or not _soportado(item.get("name", "")):
                    continue
                elementos.append(Elemento(
                    referencia=f"{item['id']}@{item.get('lastModifiedDateTime', '')}",
                    nombre=item["name"],
                    contenido=self._descargar(item["id"], cab),
                ))
            if "@odata.nextLink" in datos:
                url = datos["@odata.nextLink"]
                if len(elementos) >= MAX_ELEMENTOS_POR_EJECUCION:
                    return elementos, {"carpeta_id": carpeta_id, "siguiente": url}
            else:
                return elementos, {"carpeta_id": carpeta_id, "delta": datos.get("@odata.deltaLink")}
        return elementos, {"carpeta_id": carpeta_id}


class GoogleDrive(Conector):
    """Cuenta de servicio de Google (JSON) con acceso de lectura a la
    carpeta (compartida con el correo de la cuenta de servicio)."""

    tipo = "google_drive"
    titulo = "Google Drive"
    descripcion = "Lee los albaranes de una carpeta de Google Drive compartida con una cuenta de servicio."
    CAMPOS = [
        CampoConfig("credenciales_json", "JSON de la cuenta de servicio", secreto=True),
        CampoConfig("carpeta_id", "Id de la carpeta", ayuda="La parte final de la URL de la carpeta"),
    ]
    API = "https://www.googleapis.com/drive/v3"

    def __init__(self, config: dict, cliente=None):
        super().__init__(config, cliente)
        # El id va dentro de la consulta q="'<id>' in parents": solo se
        # admiten los caracteres de un id de Drive (evita alterar la consulta).
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", config["carpeta_id"].strip()):
            raise ConectorError("El id de la carpeta de Google Drive no es válido")
        self.config["carpeta_id"] = config["carpeta_id"].strip()

    def _token(self) -> str:
        try:
            cuenta = json.loads(self.config["credenciales_json"])
            ahora = int(time.time())
            afirmacion = jwt.encode(
                {"iss": cuenta["client_email"], "scope": "https://www.googleapis.com/auth/drive.readonly",
                 "aud": "https://oauth2.googleapis.com/token", "iat": ahora, "exp": ahora + 3600},
                cuenta["private_key"], algorithm="RS256",
            )
        except (ValueError, KeyError) as exc:
            raise ConectorError("El JSON de la cuenta de servicio no es válido") from exc
        r = self.cliente.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": afirmacion})
        return self._json(r, "Google rechazó la cuenta de servicio")["access_token"]

    def probar(self) -> str:
        cab = {"Authorization": f"Bearer {self._token()}"}
        datos = self._json(self.cliente.get(f"{self.API}/files/{self.config['carpeta_id']}",
                                            params={"supportsAllDrives": "true", "fields": "id,name,mimeType"}, headers=cab), "Carpeta")
        if datos.get("mimeType") != "application/vnd.google-apps.folder":
            raise ConectorError("El id indicado no es una carpeta")
        return f"Conexión correcta: carpeta «{datos.get('name')}»."

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        cab = {"Authorization": f"Bearer {self._token()}"}
        desde = estado.get("desde", "1970-01-01T00:00:00Z")
        consulta = f"'{self.config['carpeta_id']}' in parents and trashed = false and modifiedTime > '{desde}'"
        datos = self._json(self.cliente.get(f"{self.API}/files", headers=cab, params={
            "q": consulta, "orderBy": "modifiedTime", "pageSize": MAX_ELEMENTOS_POR_EJECUCION,
            "fields": "files(id,name,mimeType,modifiedTime)", "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true"}), "Listado de la carpeta")
        elementos = []
        for f in datos.get("files", []):
            desde = max(desde, f["modifiedTime"])
            # Los documentos nativos de Google (Docs, Hojas) no se descargan
            # con alt=media; no son albaranes en PDF/imagen.
            if f["mimeType"].startswith("application/vnd.google-apps") or not _soportado(f["name"]):
                continue
            r = self.cliente.get(f"{self.API}/files/{f['id']}", headers=cab, params={"alt": "media", "supportsAllDrives": "true"})
            if r.status_code != 200:
                raise ConectorError(f"Descarga de {f['name']}: error {r.status_code}")
            elementos.append(Elemento(referencia=f"{f['id']}@{f['modifiedTime']}", nombre=f["name"], contenido=r.content))
        return elementos, {"desde": desde}


class Dropbox(Conector):
    tipo = "dropbox"
    titulo = "Dropbox"
    descripcion = "Lee los albaranes de una carpeta de Dropbox (app con permiso files.content.read)."
    CAMPOS = [
        CampoConfig("app_key", "App key"),
        CampoConfig("app_secret", "App secret", secreto=True),
        CampoConfig("refresh_token", "Refresh token", secreto=True),
        CampoConfig("carpeta", "Carpeta", ayuda="Ej.: /Albaranes"),
    ]

    def _token(self) -> str:
        r = self.cliente.post("https://api.dropboxapi.com/oauth2/token", data={
            "grant_type": "refresh_token", "refresh_token": self.config["refresh_token"],
            "client_id": self.config["app_key"], "client_secret": self.config["app_secret"]})
        return self._json(r, "Dropbox rechazó las credenciales")["access_token"]

    def probar(self) -> str:
        cab = {"Authorization": f"Bearer {self._token()}"}
        self._json(self.cliente.post("https://api.dropboxapi.com/2/files/list_folder", headers=cab,
                                     json={"path": self.config["carpeta"], "limit": 1}), "Carpeta")
        return "Conexión correcta y carpeta encontrada."

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        cab = {"Authorization": f"Bearer {self._token()}"}
        if estado.get("cursor"):
            r = self.cliente.post("https://api.dropboxapi.com/2/files/list_folder/continue", headers=cab, json={"cursor": estado["cursor"]})
        else:
            r = self.cliente.post("https://api.dropboxapi.com/2/files/list_folder", headers=cab,
                                  json={"path": self.config["carpeta"], "recursive": False})
        datos = self._json(r, "Listado de la carpeta")
        elementos = []
        for e in datos.get("entries", []):
            if e.get(".tag") != "file" or not _soportado(e["name"]):
                continue
            d = self.cliente.post("https://content.dropboxapi.com/2/files/download",
                                  headers={**cab, "Dropbox-API-Arg": json.dumps({"path": e["id"]})})
            if d.status_code != 200:
                raise ConectorError(f"Descarga de {e['name']}: error {d.status_code}")
            elementos.append(Elemento(referencia=f"{e['id']}@{e.get('rev', '')}", nombre=e["name"], contenido=d.content))
        return elementos, {"cursor": datos.get("cursor")}


class Sftp(Conector):
    """SFTP (NAS, servidor de ficheros). La huella del servidor es
    obligatoria: sin ella no hay forma de saber que no es un impostor."""

    tipo = "sftp"
    titulo = "SFTP (NAS / servidor de ficheros)"
    descripcion = "Lee los albaranes de una carpeta por SFTP. El NAS debe ser accesible desde internet o usa el conector local."
    CAMPOS = [
        CampoConfig("host", "Servidor"),
        CampoConfig("puerto", "Puerto", defecto="22"),
        CampoConfig("usuario", "Usuario"),
        CampoConfig("password", "Contraseña", secreto=True, obligatorio=False),
        CampoConfig("clave_privada", "Clave privada (OpenSSH)", secreto=True, obligatorio=False),
        CampoConfig("huella_host", "Huella SHA256 del servidor", ayuda="ssh-keygen -lf: SHA256:xxxx"),
        CampoConfig("carpeta", "Carpeta", ayuda="Ej.: /albaranes"),
    ]

    def _conectar(self):
        import paramiko

        validar_url_externa(f"https://{self.config['host']}:{int(self.config.get('puerto') or 22)}")
        transporte = paramiko.Transport((self.config["host"], int(self.config.get("puerto") or 22)))
        try:
            transporte.start_client(timeout=15)
            clave_servidor = transporte.get_remote_server_key()
            huella = "SHA256:" + base64.b64encode(hashlib.sha256(clave_servidor.asbytes()).digest()).decode().rstrip("=")
            if huella != self.config["huella_host"].strip():
                raise ConectorError(f"La huella del servidor no coincide ({huella}): conexión rechazada")
            if self.config.get("clave_privada"):
                clave = None
                for tipo_clave in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
                    try:
                        clave = tipo_clave.from_private_key(io.StringIO(self.config["clave_privada"]))
                        break
                    except paramiko.SSHException:
                        continue
                if clave is None:
                    raise ConectorError("La clave privada no es válida")
                transporte.auth_publickey(self.config["usuario"], clave)
            elif self.config.get("password"):
                transporte.auth_password(self.config["usuario"], self.config["password"])
            else:
                raise ConectorError("Indica contraseña o clave privada")
            return transporte, paramiko.SFTPClient.from_transport(transporte)
        except paramiko.SSHException as exc:
            transporte.close()
            raise ConectorError(f"SFTP: {exc}") from exc
        except ConectorError:
            transporte.close()
            raise

    def probar(self) -> str:
        transporte, sftp = self._conectar()
        try:
            sftp.listdir(self.config["carpeta"])
            return "Conexión correcta y carpeta encontrada."
        finally:
            transporte.close()

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        transporte, sftp = self._conectar()
        elementos = []
        try:
            carpeta = PurePosixPath(self.config["carpeta"])
            ficheros, nuevo_estado = siguientes_por_marca(
                (a for a in sftp.listdir_attr(str(carpeta)) if stat.S_ISREG(a.st_mode or 0) and _soportado(a.filename)),
                lambda a: (float(a.st_mtime or 0), a.filename), estado)
            for a in ficheros:
                with sftp.open(str(carpeta / a.filename), "rb") as f:
                    contenido = f.read(get_settings().documento_origen_max_bytes + 1)
                elementos.append(Elemento(referencia=f"{carpeta / a.filename}@{a.st_mtime}", nombre=a.filename, contenido=contenido))
        finally:
            transporte.close()
        return elementos, nuevo_estado


class CarpetaServidor(Conector):
    """Carpeta montada en el servidor de MADeCa (instalación on-premise,
    p. ej. un NAS montado). Solo dentro de FUENTES_CARPETA_BASE/<empresa>:
    una empresa no puede leer otras rutas del servidor."""

    tipo = "carpeta"
    titulo = "Carpeta del servidor (instalación propia)"
    descripcion = "Solo en instalaciones propias: una carpeta montada en el servidor de MADeCa."
    CAMPOS = [CampoConfig("subcarpeta", "Subcarpeta", ayuda="Dentro de la carpeta asignada a tu empresa", obligatorio=False)]

    def __init__(self, config: dict, cliente=None, *, tenant_id: str | None = None):
        super().__init__(config, cliente)
        base = get_settings().fuentes_carpeta_base
        if base is None or tenant_id is None:
            raise ConectorError("Esta instalación no tiene carpetas del servidor habilitadas")
        raiz = (Path(base) / str(tenant_id)).resolve()
        ruta = (raiz / (config.get("subcarpeta") or "")).resolve()
        if ruta != raiz and raiz not in ruta.parents:
            raise ConectorError("La subcarpeta debe estar dentro de la carpeta asignada a tu empresa")
        self.ruta = ruta

    def probar(self) -> str:
        if not self.ruta.is_dir():
            raise ConectorError("La carpeta no existe en el servidor")
        return "Carpeta encontrada."

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        ficheros, nuevo_estado = siguientes_por_marca(
            ((p, p.stat().st_mtime) for p in self.ruta.iterdir() if p.is_file() and _soportado(p.name)),
            lambda pm: (pm[1], pm[0].name), estado)
        elementos = [Elemento(referencia=f"{p}@{mtime}", nombre=p.name, contenido=p.read_bytes()) for p, mtime in ficheros]
        return elementos, {**nuevo_estado, "comprobado": datetime.now(timezone.utc).isoformat()}
