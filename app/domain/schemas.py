"""DTOs del dominio DeCA. MADeCa admite DOS caminos de ingesta:

1. **Passthrough** (`ExpedicionMetadata`): el ERP ya genera su propio
   PDF (albarán, factura, declaración de contenido) — MADeCa no lo
   redacta, solo valida los metadatos operativos mínimos (referencia,
   matrícula, fecha) para indexar y aplicar retención, y le estampa el
   QR. No valida el contenido del PDF (Art. 6) porque no hace parsing/
   OCR — esa responsabilidad es de la plantilla del ERP.

2. **Generación** (`DecaCreate`): el ERP no produce PDF propio, solo
   manda los datos estructurados, y es MADeCa quien redacta el
   documento. Aquí sí se validan los campos mínimos exigidos por el
   Art. 6 de la Orden FOM/2861/2012 (remitido por el Apartado
   Primero.1.a de BOE-A-2026-12784):

    a) Cargador contractual: nombre/razón social, NIF, domicilio.
    b) Transportista efectivo: nombre/razón social, NIF.
    c) Lugar de origen y destino del envío.
    d) Naturaleza y peso de la mercancía (u otra magnitud si el peso
       exacto no es determinable por las circunstancias de la carga).
    e) Identificación de la autorización especial de circulación, cuando
       el vehículo deba circular amparado por una.
    f) Fecha de realización del transporte.
    g) Matrícula del vehículo (y del semirremolque/remolque si procede).
    h) Observaciones, a petición de los intervinientes (opcional).

Cuál de los dos se usa lo decide el propio adaptador (hot-folder o
REST) según si llega o no un fichero PDF junto a los datos — ver
app/services/expedicion_service.py.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.validation import validar_matricula, validar_nif_cif, validar_pais
from app.zonas import con_zona


class OrigenErp(str, Enum):
    A3ERP = "a3erp"
    SIGRID = "sigrid"
    SAP = "sap"
    DYNAMICS365 = "dynamics365"
    ODOO = "odoo"
    SAGE = "sage"
    GENERICO = "generico"


class _Matriculas(BaseModel):
    """Matrículas del vehículo y del remolque con su país (ES por defecto).
    El país permite el cabotaje: vehículos de otro Estado haciendo
    transporte interior, que también necesitan DeCA."""

    matricula_vehiculo: str
    matricula_remolque: str | None = None
    pais_matricula_vehiculo: str = "ES"
    pais_matricula_remolque: str = "ES"

    @field_validator("pais_matricula_vehiculo", "pais_matricula_remolque")
    @classmethod
    def _validar_pais(cls, v: str) -> str:
        return validar_pais(v)

    @model_validator(mode="after")
    def _validar_matriculas(self):
        self.matricula_vehiculo = validar_matricula(self.matricula_vehiculo, self.pais_matricula_vehiculo)
        if self.matricula_remolque is not None:
            self.matricula_remolque = validar_matricula(self.matricula_remolque, self.pais_matricula_remolque)
        return self

    @model_validator(mode="after")
    def _fijar_zona_horaria(self):
        # Las dos clases hijas tienen fecha_transporte y zona_horaria. Una
        # fecha sin desfase (formulario, CSV, ERP) se guardaba como si fuera
        # UTC: una hora más tarde en invierno peninsular y en Canarias.
        fecha = getattr(self, "fecha_transporte", None)
        if fecha is not None:
            self.fecha_transporte, self.zona_horaria = con_zona(fecha, getattr(self, "zona_horaria", None))
        return self


# --- Camino 1: passthrough del PDF del ERP ---


class ExpedicionMetadata(_Matriculas):
    """Metadatos operativos que acompañan al PDF del ERP (sidecar .json
    en el hot-folder, o campos de formulario en la ingesta REST)."""

    referencia_externa: str = Field(
        min_length=1,
        max_length=200,
        description="ID del albarán/factura/declaración en el ERP de origen, usado para idempotencia",
    )
    erp_origen: OrigenErp = OrigenErp.GENERICO

    fecha_transporte: datetime
    zona_horaria: str | None = Field(
        default=None,
        description="Europe/Madrid (península, Baleares, Ceuta, Melilla) o Atlantic/Canary. "
        "Solo hace falta si fecha_transporte llega sin desfase horario.",
    )

    finalidad_contractual: bool = Field(
        default=False,
        description=(
            "Si es True, el PDF se firma con AdES (Apartado Cuarto "
            "BOE-A-2026-12784). Si es False, la firma es opcional."
        ),
    )


# --- Camino 2: MADeCa genera el PDF desde datos estructurados ---


class Parte(BaseModel):
    nombre_razon_social: str = Field(min_length=1, max_length=300)
    nif_cif: str
    domicilio: str | None = Field(default=None, max_length=500)

    @field_validator("nif_cif")
    @classmethod
    def _validar_nif(cls, v: str) -> str:
        return validar_nif_cif(v)


class Mercancia(BaseModel):
    naturaleza: str = Field(min_length=1, max_length=500)
    peso_kg: float | None = Field(default=None, gt=0)
    magnitud_alternativa: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Solo si, por las circunstancias de la carga, el peso exacto "
            "no es determinable (Art. 6.d Orden FOM/2861/2012)."
        ),
    )
    bultos: int | None = Field(default=None, ge=0)
    adr_numero_onu: str | None = Field(default=None, max_length=10)
    adr_denominacion: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _peso_o_magnitud(self) -> "Mercancia":
        if self.peso_kg is None and not self.magnitud_alternativa:
            raise ValueError(
                "Debe indicarse peso_kg o, si no es determinable, "
                "magnitud_alternativa (Art. 6.d)"
            )
        return self

    @model_validator(mode="after")
    def _adr_completo(self) -> "Mercancia":
        if bool(self.adr_numero_onu) != bool(self.adr_denominacion):
            raise ValueError(
                "Si la mercancía es ADR, adr_numero_onu y adr_denominacion "
                "deben indicarse juntos"
            )
        return self


class DecaCreate(_Matriculas):
    """Payload de ingesta cuando MADeCa debe redactar el PDF (el ERP no
    genera uno propio)."""

    referencia_externa: str = Field(
        min_length=1,
        max_length=200,
        description="ID del albarán/orden en el ERP de origen, usado para idempotencia",
    )
    erp_origen: OrigenErp = OrigenErp.GENERICO

    cargador: Parte
    transportista: Parte
    destinatario: Parte | None = None

    origen_direccion: str = Field(min_length=1, max_length=500)
    destino_direccion: str = Field(min_length=1, max_length=500)

    mercancia: Mercancia

    fecha_transporte: datetime
    zona_horaria: str | None = Field(
        default=None,
        description="Europe/Madrid (península, Baleares, Ceuta, Melilla) o Atlantic/Canary. "
        "Solo hace falta si fecha_transporte llega sin desfase horario.",
    )

    autorizacion_especial_circulacion: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Identificación de la autorización especial de circulación, "
            "solo si el vehículo debe circular amparado por una (Art. 6.e "
            "Orden FOM/2861/2012)."
        ),
    )

    observaciones: str | None = Field(default=None, max_length=2000)

    finalidad_contractual: bool = Field(
        default=False,
        description=(
            "Si es True, el DeCA requiere firma electrónica avanzada (AdES) "
            "según el Apartado Cuarto de BOE-A-2026-12784. Si es False, la "
            "firma es opcional (no exigida por la norma)."
        ),
    )

    @model_validator(mode="after")
    def _domicilio_cargador(self) -> "DecaCreate":
        # Art. 6.a: el domicilio del cargador contractual es dato esencial
        # (a diferencia del transportista efectivo, Art. 6.b, que no lo exige).
        if not (self.cargador.domicilio or "").strip():
            raise ValueError("Falta el domicilio del cargador contractual (Art. 6.a Orden FOM/2861/2012)")
        return self


class CambioDeca(BaseModel):
    """Un dato modificado. `campo` es el nombre legible del dato (p. ej.
    "Matrícula del vehículo"); si es uno de los que MADeCa indexa
    (CAMPOS_INDEXADOS) también se actualiza en sus registros."""

    campo: str = Field(min_length=1, max_length=100)
    anterior: str = Field(min_length=1, max_length=500)
    nuevo: str = Field(min_length=1, max_length=500)


# Nombre técnico -> nombre legible que se imprime en la página de
# modificación. Son los datos que MADeCa conoce también fuera del PDF.
CAMPOS_INDEXADOS: dict[str, str] = {
    "matricula_vehiculo": "Matrícula del vehículo",
    "matricula_remolque": "Matrícula del remolque",
    "fecha_transporte": "Fecha del transporte",
}


class ModificacionCreate(BaseModel):
    """Modificación durante el servicio (Apartado Quinto, método 1)."""

    motivo: str = Field(min_length=3, max_length=1000)
    cambios: list[CambioDeca] = Field(min_length=1, max_length=20)


class DecaEstado(str, Enum):
    EN_TRANSITO = "en_transito"
    ENTREGADO = "entregado"
    ANULADO = "anulado"


class ExpedicionOut(BaseModel):
    id: str
    tenant_id: str
    referencia_externa: str
    erp_origen: OrigenErp
    estado: DecaEstado
    fecha_transporte: datetime
    fecha_emision: datetime
    matricula_vehiculo: str
    matricula_remolque: str | None
    url_publica: str
    firmado: bool
    numero_escaneos: int
    # Enlace privado para la otra parte (Apartado Segundo.4), válido
    # durante el plazo de conservación.
    url_contraparte: str | None = None

    model_config = {"from_attributes": True}
