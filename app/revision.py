"""Datos por campo del formulario del DeCA: obligatorios y errores.

Lo usan la pantalla de revisión (bandeja) y el alta manual para señalar
cada dato en su campo. Contenido mínimo del documento de control: art. 6
de la Orden FOM/2861/2012; quién responde de cada dato: art. 7 (2: el
cargador de a-d; 3: el transportista de e-g). Las comprobaciones son las
mismas que hace MADeCa al emitir (app/domain/validation.py): la pantalla y
el servidor nunca discrepan.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.validation import validar_cif, validar_matricula, validar_nif_cif, validar_pais

# Campo del formulario -> texto, artículo, quién responde y ruta en los
# datos leídos (para traducir la «confianza» de la extracción).
CAMPOS: dict[str, dict] = {
    "referencia_externa": {"texto": "Referencia externa", "ruta": "referencia_externa"},
    "cargador_nombre": {"texto": "Nombre del cargador", "art": "6.a", "responde": "cargador", "ruta": "cargador.nombre_razon_social"},
    "cargador_nif": {"texto": "NIF del cargador", "art": "6.a", "responde": "cargador", "ruta": "cargador.nif_cif"},
    "cargador_domicilio": {"texto": "Domicilio del cargador", "art": "6.a", "responde": "cargador", "ruta": "cargador.domicilio"},
    "transportista_nombre": {"texto": "Nombre del transportista", "art": "6.b", "responde": "cargador", "ruta": "transportista.nombre_razon_social"},
    "transportista_nif": {"texto": "NIF del transportista", "art": "6.b", "responde": "cargador", "ruta": "transportista.nif_cif"},
    "destinatario_nombre": {"texto": "Nombre del destinatario", "ruta": "destinatario.nombre_razon_social"},
    "destinatario_nif": {"texto": "NIF del destinatario", "ruta": "destinatario.nif_cif"},
    "destinatario_domicilio": {"texto": "Domicilio del destinatario", "ruta": "destinatario.domicilio"},
    "origen_direccion": {"texto": "Lugar de origen", "art": "6.c", "responde": "cargador", "ruta": "origen_direccion"},
    "destino_direccion": {"texto": "Lugar de destino", "art": "6.c", "responde": "cargador", "ruta": "destino_direccion"},
    "mercancia_naturaleza": {"texto": "Naturaleza de la mercancía", "art": "6.d", "responde": "cargador", "ruta": "mercancia.naturaleza"},
    "mercancia_peso_kg": {"texto": "Peso de la mercancía", "art": "6.d", "responde": "cargador", "ruta": "mercancia.peso_kg"},
    "mercancia_magnitud_alternativa": {"texto": "Magnitud alternativa", "ruta": "mercancia.magnitud_alternativa"},
    "mercancia_bultos": {"texto": "Bultos", "ruta": "mercancia.bultos"},
    "matricula_vehiculo": {"texto": "Matrícula del vehículo", "art": "6.g", "responde": "transportista", "ruta": "matricula_vehiculo"},
    "matricula_remolque": {"texto": "Matrícula del remolque", "art": "6.g", "responde": "transportista", "ruta": "matricula_remolque"},
    "pais_matricula_vehiculo": {"texto": "País de la matrícula del vehículo"},
    "pais_matricula_remolque": {"texto": "País de la matrícula del remolque"},
    "autorizacion_especial_circulacion": {"texto": "Autorización especial de circulación", "art": "6.e", "responde": "transportista",
                                          "ruta": "autorizacion_especial_circulacion"},
    "fecha_transporte": {"texto": "Fecha del transporte", "art": "6.f", "responde": "transportista", "ruta": "fecha_transporte"},
}

# Datos esenciales del art. 6 siempre exigibles. No lo son la autorización
# especial (6.e: solo si el vehículo circula amparado por una) ni el
# remolque (6.g: solo en conjuntos articulados). El peso (6.d) se cumple
# también con otra magnitud cuando el peso exacto es de difícil
# determinación.
OBLIGATORIOS: tuple[str, ...] = (
    "cargador_nombre", "cargador_nif", "cargador_domicilio",
    "transportista_nombre", "transportista_nif",
    "origen_direccion", "destino_direccion",
    "mercancia_naturaleza", "mercancia_peso_kg",
    "fecha_transporte", "matricula_vehiculo",
)
ALTERNATIVAS: dict[str, str] = {"mercancia_peso_kg": "mercancia_magnitud_alternativa"}

# Qué se comprueba de cada campo al escribirlo (data-validar en el formulario).
TIPOS: dict[str, str] = {
    "cargador_nif": "nif", "transportista_nif": "nif", "destinatario_nif": "nif",
    "matricula_vehiculo": "matricula", "matricula_remolque": "matricula",
    "pais_matricula_vehiculo": "pais", "pais_matricula_remolque": "pais",
    "mercancia_peso_kg": "peso", "mercancia_bultos": "entero",
    "fecha_transporte": "fecha", "referencia_externa": "referencia",
}
PAIS_DE: dict[str, str] = {"matricula_vehiculo": "pais_matricula_vehiculo", "matricula_remolque": "pais_matricula_remolque"}

def texto_articulo(campo: str) -> str:
    """«Art. 6.a · responde el cargador» (vacío si no es del art. 6)."""
    c = CAMPOS.get(campo, {})
    if not c.get("art"):
        return ""
    return f"Art. {c['art']} · responde el {c['responde']}"


def faltan(valores: dict) -> list[str]:
    """Campos obligatorios vacíos, en el orden del formulario."""
    def vacio(nombre: str) -> bool:
        return not str(valores.get(nombre) or "").strip()

    return [c for c in OBLIGATORIOS if vacio(c) and (c not in ALTERNATIVAS or vacio(ALTERNATIVAS[c]))]


def _sin_prefijo(mensaje: str) -> str:
    return mensaje.removeprefix("Value error, ")


def validar_campo(campo: str, valor: str, pais: str | None = None) -> str | None:
    """Mensaje de error del campo o None si es válido (o está vacío: de los
    vacíos se ocupa `faltan`). Mismas reglas que al emitir."""
    valor = (valor or "").strip()
    tipo = TIPOS.get(campo)
    if not valor or tipo is None:
        return None
    try:
        if tipo == "nif":
            try:
                validar_nif_cif(valor)
            except ValueError:
                doc = valor.replace(" ", "").replace("-", "").upper()
                if len(doc) == 9 and doc[0].isalpha() and doc[0] not in "XYZ" and doc[1:8].isdigit():
                    validar_cif(doc)  # con forma de CIF: su propio mensaje
                raise
        elif tipo == "matricula":
            validar_matricula(valor, validar_pais(pais or "ES"))
        elif tipo == "pais":
            validar_pais(valor)
        elif tipo == "peso":
            if float(valor.replace(",", ".")) <= 0:
                return "El peso debe ser mayor que cero."
        elif tipo == "entero":
            if int(valor) < 0:
                return "No puede ser negativo."
        elif tipo == "fecha":
            datetime.fromisoformat(valor)
    except ValueError as exc:
        if tipo in ("peso", "entero"):
            return "Escribe solo el número (p. ej. 1250,5)." if tipo == "peso" else "Escribe un número entero."
        if tipo == "fecha":
            return "Fecha y hora no válidas."
        return _sin_prefijo(str(exc))
    return None


def errores_por_campo(valores: dict) -> dict[str, str]:
    """Errores de formato de todos los campos rellenos del formulario."""
    errores = {}
    for campo in TIPOS:
        mensaje = validar_campo(campo, str(valores.get(campo) or ""), valores.get(PAIS_DE.get(campo, ""), "ES") or "ES")
        if mensaje:
            errores[campo] = mensaje
    return errores
