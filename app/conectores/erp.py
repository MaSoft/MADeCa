"""Conectores de ERP: leen expediciones/albaranes ya estructurados y los
dejan como borradores (sin OCR). Ningún ERP trae todos los datos del
Art. 6 (casi nunca la matrícula ni el transportista efectivo): lo que
falte se avisa y se completa en la bandeja, o se fija por defecto en la
configuración de la fuente (p. ej. el transportista habitual).

Campos comprobados (09/10/2026):
- Business Central: documentación oficial de la API v2.0 (salesShipment,
  salesShipmentLine, customer, companyInformation).
- Odoo: API JSON-2 oficial (POST /json/2/<modelo>/<método>, bearer).
  Los campos de stock.picking/stock.move/res.partner son los estándar;
  confirmar en la versión y módulos del cliente.
- SAP Business One: la referencia oficial del Service Layer
  (help.sap.com, «Service Layer API Reference») no publica las
  propiedades de Documents; DocNum, CardName, DocDate, FederalTaxID
  (LicTradNum) y Address2 (dirección de entrega) constan en la ayuda del
  objeto Documents de la DI API reproducida por terceros (biuan.com). Se
  pagina de 20 en 20 por defecto (@odata.nextLink). Confirmar con
  https://<servidor>:50000/b1s/v1/$metadata de la instalación.
- Holded: su referencia (holded.com/developers/api-reference) no se pudo
  leer el 09/10/2026; ya publica una API v2 con /api/v2/waybills (paginada
  por cursor). Se sigue usando la v1 de documentos (waybill). Campos y
  vigencia de la v1 por verificar con una cuenta real.
La correspondencia de SAP y Holded es editable por la empresa y la de
serie debe comprobarse con la instalación real antes de usarla.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from app.conectores.base import (
    MAX_ELEMENTOS_POR_EJECUCION,
    CampoConfig,
    Conector,
    ConectorError,
    Elemento,
    anidar,
    avisos_faltan,
    ruta_valor,
    token_entra_id,
    validar_url_externa,
)

CAMPOS_TRANSPORTISTA = [
    CampoConfig("transportista_nombre", "Transportista efectivo habitual (nombre)", obligatorio=False,
                ayuda="Si el ERP no lo indica; se puede cambiar en cada borrador"),
    CampoConfig("transportista_nif", "Transportista efectivo habitual (NIF)", obligatorio=False),
]


def _nif(valor) -> str | None:
    """NIF desde el que guarda el ERP. Muchos guardan el NIF-IVA
    intracomunitario («ESB00345678»), que la validación del DeCA rechaza.
    El NIF-IVA es el NIF con el prefijo ES (art. 25 del Reglamento del RD
    1065/2007; administracion.gob.es, «IVA: registro y pago», consultado
    el 09/10/2026): se quita el «ES» solo si queda un NIF de 9 caracteres."""
    if valor in (None, ""):
        return None
    texto = str(valor).replace(" ", "").replace("-", "").upper()
    return texto[2:] if texto.startswith("ES") and len(texto) == 11 else texto


def _une(*partes) -> str:
    return ", ".join(str(p).strip() for p in partes if p and str(p).strip())


class _ConectorErp(Conector):
    categoria = "erp"

    def _transportista_defecto(self, datos: dict) -> None:
        if self.config.get("transportista_nombre") and "transportista" not in datos:
            datos["transportista"] = {
                "nombre_razon_social": self.config["transportista_nombre"],
                "nif_cif": self.config.get("transportista_nif", ""),
            }

    def _elemento(self, referencia: str, nombre: str, datos: dict, avisos: list | None = None) -> Elemento:
        self._transportista_defecto(datos)
        return Elemento(
            referencia=referencia, nombre=nombre, datos=datos,
            confianza={k: "media" for k in datos},
            avisos=(avisos or []) + avisos_faltan(datos),
        )


class BusinessCentral(_ConectorErp):
    tipo = "business_central"
    titulo = "Dynamics 365 Business Central"
    descripcion = "Lee los albaranes de venta registrados (salesShipments) con la API v2.0."
    CAMPOS = [
        CampoConfig("tenant_azure", "Id del inquilino de Microsoft (tenant)"),
        CampoConfig("client_id", "Id de la aplicación registrada en Entra ID"),
        CampoConfig("client_secret", "Secreto de la aplicación", secreto=True),
        CampoConfig("entorno", "Entorno", defecto="production"),
        CampoConfig("empresa_id", "Id de la empresa (GUID)", ayuda="GET .../api/v2.0/companies"),
        *CAMPOS_TRANSPORTISTA,
    ]

    def _base(self) -> str:
        return (f"https://api.businesscentral.dynamics.com/v2.0/{self.config['tenant_azure']}/"
                f"{self.config.get('entorno') or 'production'}/api/v2.0/companies({self.config['empresa_id']})")

    def _cab(self) -> dict:
        token = token_entra_id(self.cliente, tenant=self.config["tenant_azure"], client_id=self.config["client_id"],
                               secreto=self.config["client_secret"], scope="https://api.businesscentral.dynamics.com/.default")
        return {"Authorization": f"Bearer {token}"}

    def probar(self) -> str:
        info = self._json(self.cliente.get(f"{self._base()}/companyInformation", headers=self._cab()), "Empresa")
        nombre = (info.get("value") or [{}])[0].get("displayName", "?")
        return f"Conexión correcta: empresa «{nombre}»."

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        cab = self._cab()
        empresa = (self._json(self.cliente.get(f"{self._base()}/companyInformation", headers=cab), "Empresa").get("value") or [{}])[0]
        desde = estado.get("desde") or (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        datos = self._json(self.cliente.get(f"{self._base()}/salesShipments", headers=cab, params={
            # «ge» y no «gt»: con «gt» se perdían los que comparten la hora
            # del último leído y quedaban tras el corte de $top. El repetido
            # lo descarta la bandeja (misma referencia). Business Central no
            # admite «or» entre campos distintos para desempatar por id.
            "$filter": f"lastModifiedDateTime ge {desde}", "$orderby": "lastModifiedDateTime",
            "$expand": "salesShipmentLines,customer", "$top": str(MAX_ELEMENTOS_POR_EJECUCION)}), "Albaranes de venta")
        elementos = []
        for s in datos.get("value", []):
            desde = max(desde, s.get("lastModifiedDateTime") or desde)
            cliente = s.get("customer") or {}
            lineas = [l for l in s.get("salesShipmentLines", []) if l.get("lineType") == "Item"]
            naturaleza = "; ".join(_une(f"{l.get('quantity', '')} {l.get('unitOfMeasureCode', '')}".strip(), l.get("description"))
                                   for l in lineas)
            planos = {
                "referencia_externa": s.get("number"),
                # Cargador: la propia empresa de Business Central que vende y
                # contrata el transporte. Revisar si lo contrata el cliente.
                "cargador.nombre_razon_social": empresa.get("displayName"),
                "cargador.nif_cif": _nif(empresa.get("taxRegistrationNumber")),
                "cargador.domicilio": _une(empresa.get("addressLine1"), empresa.get("addressLine2"),
                                           empresa.get("postalCode"), empresa.get("city")),
                "origen_direccion": _une(empresa.get("addressLine1"), empresa.get("postalCode"), empresa.get("city")),
                "destinatario.nombre_razon_social": s.get("shipToName") or s.get("customerName"),
                "destinatario.nif_cif": _nif(cliente.get("taxRegistrationNumber")),
                "destino_direccion": _une(s.get("shipToAddressLine1"), s.get("shipToAddressLine2"), s.get("shipToPostCode"),
                                          s.get("shipToCity"), s.get("shipToCountry")),
                "mercancia.naturaleza": naturaleza[:500],
                "fecha_transporte": s.get("postingDate"),
            }
            elementos.append(self._elemento(
                f"bc:{s['id']}@{s.get('lastModifiedDateTime', '')}", f"Albarán {s.get('number')}", anidar(planos),
                ["Origen tomado de la dirección de la empresa: corrígelo si la carga sale de otro almacén.",
                 "Business Central no indica la hora: revisa la fecha y hora del transporte."],
            ))
        return elementos, {"desde": desde}


class Odoo(_ConectorErp):
    tipo = "odoo"
    titulo = "Odoo 19+ (API JSON-2)"
    descripcion = "Lee las entregas (albaranes de salida) preparadas o hechas con la API JSON-2 y clave de API."
    CAMPOS = [
        CampoConfig("url", "Dirección de Odoo", ayuda="https://miempresa.odoo.com"),
        CampoConfig("base_datos", "Base de datos", obligatorio=False, ayuda="Solo si el servidor tiene varias"),
        CampoConfig("api_key", "Clave de API", secreto=True),
        *CAMPOS_TRANSPORTISTA,
    ]

    def _llamar(self, modelo: str, metodo: str, cuerpo: dict):
        url = validar_url_externa(self.config["url"]).rstrip("/")
        cab = {"Authorization": f"bearer {self.config['api_key']}", "Content-Type": "application/json; charset=utf-8",
               "User-Agent": "MADeCa"}
        if self.config.get("base_datos"):
            cab["X-Odoo-Database"] = self.config["base_datos"]
        return self._json(self.cliente.post(f"{url}/json/2/{modelo}/{metodo}", headers=cab, json=cuerpo), f"Odoo {modelo}")

    def probar(self) -> str:
        empresas = self._llamar("res.company", "search_read", {"domain": [], "fields": ["name"]})
        return f"Conexión correcta: {len(empresas)} empresa(s) accesibles."

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        desde = estado.get("desde") or (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        desde_id = int(estado.get("desde_id") or 0)
        # Seguir por (write_date, id): con solo «write_date >» se perdían las
        # entregas guardadas en el mismo segundo que la última leída que
        # quedaban tras el límite (validaciones en lote).
        entregas = self._llamar("stock.picking", "search_read", {
            "domain": [["picking_type_code", "=", "outgoing"], ["state", "in", ["assigned", "done"]],
                       "|", ["write_date", ">", desde], "&", ["write_date", "=", desde], ["id", ">", desde_id]],
            "fields": ["name", "partner_id", "scheduled_date", "origin", "company_id", "write_date"],
            "order": "write_date asc, id asc", "limit": MAX_ELEMENTOS_POR_EJECUCION,
        })
        if not entregas:
            return [], {"desde": desde, "desde_id": desde_id}
        socios = {p["id"]: p for p in self._llamar("res.partner", "read", {
            "ids": sorted({e["partner_id"][0] for e in entregas if e.get("partner_id")}),
            "fields": ["name", "vat", "street", "street2", "zip", "city"]})} if any(e.get("partner_id") for e in entregas) else {}
        empresas = {c["id"]: c for c in self._llamar("res.company", "read", {
            "ids": sorted({e["company_id"][0] for e in entregas if e.get("company_id")}),
            "fields": ["name", "vat", "street", "zip", "city"]})}
        movimientos = self._llamar("stock.move", "search_read", {
            "domain": [["picking_id", "in", [e["id"] for e in entregas]]],
            "fields": ["picking_id", "product_id", "product_uom_qty"]})
        elementos = []
        for e in entregas:
            socio = socios.get((e.get("partner_id") or [None])[0], {})
            empresa = empresas.get((e.get("company_id") or [None])[0], {})
            productos = [f"{m['product_uom_qty']} x {m['product_id'][1]}" for m in movimientos
                         if m.get("picking_id") and m["picking_id"][0] == e["id"] and m.get("product_id")]
            planos = {
                "referencia_externa": e.get("name"),
                "cargador.nombre_razon_social": empresa.get("name"),
                "cargador.nif_cif": _nif(empresa.get("vat")),
                "cargador.domicilio": _une(empresa.get("street"), empresa.get("zip"), empresa.get("city")),
                "origen_direccion": _une(empresa.get("street"), empresa.get("zip"), empresa.get("city")),
                "destinatario.nombre_razon_social": socio.get("name"),
                "destinatario.nif_cif": _nif(socio.get("vat")),
                "destino_direccion": _une(socio.get("street"), socio.get("street2"), socio.get("zip"), socio.get("city")),
                "mercancia.naturaleza": "; ".join(productos)[:500],
                # Odoo da la fecha en UTC sin desfase: se indica, para que no se
                # tome como hora local (una o dos horas de diferencia).
                "fecha_transporte": (e.get("scheduled_date") or "").replace(" ", "T") + "+00:00" if e.get("scheduled_date") else None,
            }
            elementos.append(self._elemento(f"odoo:{e['id']}@{e.get('write_date', '')}", f"Entrega {e.get('name')}", anidar(planos),
                                            ["Origen tomado de la dirección de la empresa: corrígelo si la carga sale de otro almacén.",
                                             "Odoo da la fecha prevista de la entrega: comprueba que es la del transporte."]))
        ultima = entregas[-1]
        return elementos, {"desde": ultima.get("write_date") or desde, "desde_id": ultima["id"]}


MAPEO_SAP_B1_DEFECTO = {
    "referencia_externa": "DocNum",
    "destinatario.nombre_razon_social": "CardName",
    "destinatario.nif_cif": "FederalTaxID",
    "destino_direccion": "Address2",
    "fecha_transporte": "DocDate",
}
MAPEO_HOLDED_DEFECTO = {
    "referencia_externa": "docNumber",
    "destinatario.nombre_razon_social": "contactName",
    "fecha_transporte": "date",
}


class _ConMapeo(_ConectorErp):
    MAPEO_DEFECTO: dict = {}

    def _mapeo(self) -> dict:
        texto = (self.config.get("mapeo") or "").strip()
        if not texto:
            return self.MAPEO_DEFECTO
        try:
            mapeo = json.loads(texto)
        except ValueError as exc:
            raise ConectorError("La correspondencia de campos no es un JSON válido") from exc
        if not isinstance(mapeo, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapeo.items()):
            raise ConectorError('La correspondencia debe ser {"campo_madeca": "campo_del_erp", ...}')
        return mapeo

    def _registro_a_elemento(self, registro: dict, referencia: str, nombre: str, avisos: list) -> Elemento:
        planos = {destino: ruta_valor(registro, origen) for destino, origen in self._mapeo().items()}
        for clave in [k for k in planos if k.endswith(".nif_cif")]:
            planos[clave] = _nif(planos[clave])
        if isinstance(planos.get("fecha_transporte"), (int, float)):
            planos["fecha_transporte"] = datetime.fromtimestamp(planos["fecha_transporte"], tz=timezone.utc).isoformat()
        for clave in ("cargador.nombre_razon_social", "cargador.nif_cif", "cargador.domicilio", "origen_direccion"):
            if self.config.get(clave.replace(".", "_")) and not planos.get(clave):
                planos[clave] = self.config[clave.replace(".", "_")]
        return self._elemento(referencia, nombre, anidar({k: (str(v) if v is not None else None) for k, v in planos.items()}), avisos)


CAMPOS_CARGADOR = [
    CampoConfig("cargador_nombre_razon_social", "Cargador (tu empresa): nombre", obligatorio=False),
    CampoConfig("cargador_nif_cif", "Cargador: NIF", obligatorio=False),
    CampoConfig("cargador_domicilio", "Cargador: domicilio", obligatorio=False),
    CampoConfig("origen_direccion", "Lugar de origen habitual", obligatorio=False),
]


class SapBusinessOne(_ConMapeo):
    tipo = "sap_b1"
    titulo = "SAP Business One (Service Layer)"
    descripcion = "Lee las entregas (DeliveryNotes) del Service Layer. Correspondencia de campos por verificar con tu instalación."
    MAPEO_DEFECTO = MAPEO_SAP_B1_DEFECTO
    CAMPOS = [
        CampoConfig("url", "Dirección del Service Layer", ayuda="https://servidor:50000/b1s/v1"),
        CampoConfig("company_db", "Base de datos de la empresa (CompanyDB)"),
        CampoConfig("usuario", "Usuario"),
        CampoConfig("password", "Contraseña", secreto=True),
        CampoConfig("mapeo", "Correspondencia de campos (JSON)", obligatorio=False,
                    defecto=json.dumps(MAPEO_SAP_B1_DEFECTO), ayuda="POR VERIFICAR con tu SAP antes de usarla"),
        *CAMPOS_CARGADOR,
        *CAMPOS_TRANSPORTISTA,
    ]

    def _sesion(self) -> str:
        url = validar_url_externa(self.config["url"]).rstrip("/")
        r = self.cliente.post(f"{url}/Login", json={"CompanyDB": self.config["company_db"], "UserName": self.config["usuario"],
                                                   "Password": self.config["password"]})
        self._json(r, "SAP rechazó el inicio de sesión")
        return url

    def _cerrar(self, url: str) -> None:
        try:
            self.cliente.post(f"{url}/Logout")
        except Exception:
            pass

    def probar(self) -> str:
        url = self._sesion()
        self._cerrar(url)
        return "Inicio de sesión en SAP correcto."

    # Páginas como máximo por pasada (MAX_ELEMENTOS_POR_EJECUCION cada una).
    MAX_PAGINAS = 40

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        """Sigue todas las páginas: el Service Layer pagina de 20 en 20 por
        defecto (@odata.nextLink) aunque se pida $top, y antes se leía solo
        la primera y la marca saltaba a hoy: el resto se perdía. UpdateDate
        es un día de la hora local de SAP: se vuelve a leer desde ayer y lo
        repetido lo descarta la bandeja (misma referencia)."""
        url = self._sesion()
        try:
            desde = estado.get("desde") or (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
            siguiente = f"{url}/DeliveryNotes"
            params = {"$filter": f"UpdateDate ge '{desde}'", "$orderby": "UpdateDate,DocEntry"}
            cab = {"Prefer": f"odata.maxpagesize={MAX_ELEMENTOS_POR_EJECUCION}"}
            registros: list = []
            for _ in range(self.MAX_PAGINAS):
                datos = self._json(self.cliente.get(siguiente, params=params, headers=cab), "Entregas de SAP")
                registros += datos.get("value", [])
                enlace = datos.get("@odata.nextLink") or datos.get("odata.nextLink")
                if not enlace:
                    break
                # El enlace es relativo a .../b1s/v1/ y ya lleva la consulta.
                siguiente, params = urljoin(f"{url}/", enlace), None
            else:
                # Atasco imposible de recuperar solo si un único día tiene más
                # de MAX_PAGINAS páginas; se avanza hasta lo leído.
                elementos = self._elementos_sap(registros)
                return elementos, {"desde": registros[-1].get("UpdateDate", desde)[:10]}
            ayer = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            return self._elementos_sap(registros), {"desde": max(desde, ayer) if registros else desde}
        finally:
            self._cerrar(url)

    def _elementos_sap(self, registros: list) -> list[Elemento]:
        return [self._registro_a_elemento(r, f"sap:{r.get('DocEntry')}@{r.get('UpdateDate', '')}{r.get('UpdateTime', '')}",
                                          f"Entrega {r.get('DocNum')}",
                                          ["Correspondencia de campos de SAP por verificar con tu instalación."])
                for r in registros]


class Holded(_ConMapeo):
    tipo = "holded"
    titulo = "Holded"
    descripcion = "Lee los albaranes (waybill) de Holded. Correspondencia de campos por verificar con tu cuenta."
    MAPEO_DEFECTO = MAPEO_HOLDED_DEFECTO
    CAMPOS = [
        CampoConfig("api_key", "Clave de API", secreto=True),
        CampoConfig("mapeo", "Correspondencia de campos (JSON)", obligatorio=False,
                    defecto=json.dumps(MAPEO_HOLDED_DEFECTO), ayuda="POR VERIFICAR con tu cuenta de Holded"),
        *CAMPOS_CARGADOR,
        *CAMPOS_TRANSPORTISTA,
    ]
    API = "https://api.holded.com/api/invoicing/v1/documents/waybill"

    def probar(self) -> str:
        self._json(self.cliente.get(self.API, headers={"key": self.config["api_key"]}, params={"page": 1}), "Holded")
        return "Conexión con Holded correcta."

    # starttmp/endtmp filtran por la fecha DEL ALBARÁN, no por cuándo se
    # creó: uno con fecha de ayer creado hoy quedaba fuera. Se vuelve a
    # mirar este margen y lo repetido lo descarta la bandeja (misma id).
    MARGEN_SEGUNDOS = 2 * 86400

    def sincronizar(self, estado: dict) -> tuple[list[Elemento], dict]:
        """Lee la ventana completa en cada pasada. Antes se cortaba en
        MAX_ELEMENTOS_POR_EJECUCION y la marca saltaba a «ahora»: lo que
        quedaba tras el corte se perdía."""
        desde = int(estado.get("desde") or (datetime.now(timezone.utc) - timedelta(days=2)).timestamp())
        ahora = int(datetime.now(timezone.utc).timestamp())
        datos = self._json(self.cliente.get(self.API, headers={"key": self.config["api_key"]},
                                            params={"starttmp": desde - self.MARGEN_SEGUNDOS, "endtmp": ahora}),
                           "Albaranes de Holded")
        registros = datos if isinstance(datos, list) else []
        elementos = [self._registro_a_elemento(r, f"holded:{r.get('id')}", f"Albarán {r.get('docNumber', r.get('id'))}",
                                               ["Correspondencia de campos de Holded por verificar con tu cuenta."])
                     for r in registros]
        return elementos, {"desde": ahora}
