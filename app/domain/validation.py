"""Validadores de identificadores españoles (NIF/NIE/CIF, matrícula).

Implementan el algoritmo de control real (no solo la forma), porque un DTO
legal que va a firmarse y presentarse ante inspección no puede aceptar un
NIF con dígito de control incorrecto.
"""

from __future__ import annotations

import re

_NIF_LETRAS = "TRWAGMYFPDXBNJZSQVHLCKE"

_CIF_LETRAS_CONTROL_ALFA = "JABCDEFGHI"  # tabla de conversión dígito->letra para CIF
# Organizaciones (primera letra del CIF) cuyo dígito de control es letra
_CIF_PRIMERA_LETRA_ALFA = set("PQSNWKR")
# Organizaciones cuyo dígito de control es numérico
_CIF_PRIMERA_LETRA_NUM = set("ABEH")
# El resto (CDEFGJUV) admite letra o número indistintamente

_NIE_PREFIJOS = {"X": "0", "Y": "1", "Z": "2"}

_MATRICULA_ACTUAL_RE = re.compile(r"^\d{4}[BCDFGHJKLMNPRSTVWXYZ]{3}$")
_MATRICULA_ANTIGUA_RE = re.compile(r"^[A-Z]{1,2}\d{4}[A-Z]{1,2}$")
# Remolques y semirremolques de MMA >= 750 kg: placa roja "R" + 4 cifras +
# 3 letras (sistema del RD 2822/1998, Reglamento General de Vehículos).
# El Art. 6.g de la Orden FOM/2861/2012 exige la matrícula del
# semirremolque/remolque en conjuntos articulados.
_MATRICULA_REMOLQUE_RE = re.compile(r"^R\d{4}[BCDFGHJKLMNPRSTVWXYZ]{3}$")


def _normalizar(valor: str) -> str:
    return re.sub(r"[\s\-]", "", valor.strip().upper())


# NIF de españoles sin DNI (K: menores de 14 años residentes en España;
# L: residentes en el extranjero) y de extranjeros sin NIE (M): letra, siete
# caracteres y carácter de verificación alfabético (art. 19.2 del RD
# 1065/2007). La norma no publica el cálculo de ese carácter: solo se
# comprueba la forma (antes se validaban con el algoritmo del CIF, que no
# es el suyo, y se rechazaban NIF correctos).
_NIF_KLM_RE = re.compile(r"^[KLM]\d{7}[A-Z]$")


def validar_nif_nie(valor: str) -> str:
    """Valida NIF (persona física) o NIE y devuelve el valor normalizado.

    DNI: 8 cifras y letra (se admiten 7 cifras, sin el cero inicial, y se
    completa). NIE: X, Y o Z, 7 cifras y letra. Lanza ValueError si la
    forma no es esa o la letra de control no coincide (antes no se
    comprobaba la longitud y valores como «0T» pasaban por NIF válidos).
    """
    doc = _normalizar(valor)

    if _NIF_KLM_RE.match(doc):
        return doc
    if doc and doc[0] in _NIE_PREFIJOS:
        if len(doc) != 9:
            raise ValueError(f"Formato de NIE inválido: '{valor}' (X, Y o Z, 7 cifras y letra)")
        numero_str = _NIE_PREFIJOS[doc[0]] + doc[1:-1]
        letra = doc[-1]
    else:
        if len(doc) == 8 and doc[:7].isdigit():
            doc = "0" + doc  # DNI escrito sin el cero inicial
        if len(doc) != 9:
            raise ValueError(f"Formato de NIF inválido: '{valor}' (8 cifras y letra)")
        numero_str = doc[:-1]
        letra = doc[-1]

    if not numero_str.isdigit() or len(letra) != 1 or not letra.isalpha():
        raise ValueError(f"Formato de NIF/NIE inválido: '{valor}'")

    esperada = _NIF_LETRAS[int(numero_str) % 23]
    if letra != esperada:
        raise ValueError(
            f"Letra de control incorrecta en NIF/NIE '{valor}' "
            f"(se esperaba '{esperada}')"
        )
    return doc


def validar_cif(valor: str) -> str:
    """Valida CIF de persona jurídica y devuelve el valor normalizado."""
    doc = _normalizar(valor)
    if len(doc) != 9 or not doc[0].isalpha() or not doc[1:8].isdigit():
        raise ValueError(f"Formato de CIF inválido: '{valor}'")

    primera = doc[0]
    digitos = doc[1:8]
    control = doc[8]

    pares = sum(int(d) for d in digitos[1::2])
    impares_transformados = []
    for d in digitos[0::2]:
        doble = int(d) * 2
        impares_transformados.append(doble // 10 + doble % 10)
    suma = pares + sum(impares_transformados)
    digito_control = (10 - (suma % 10)) % 10

    if primera in _CIF_PRIMERA_LETRA_NUM:
        esperado = str(digito_control)
    elif primera in _CIF_PRIMERA_LETRA_ALFA:
        esperado = _CIF_LETRAS_CONTROL_ALFA[digito_control]
    else:
        esperado_num = str(digito_control)
        esperado_alfa = _CIF_LETRAS_CONTROL_ALFA[digito_control]
        if control not in (esperado_num, esperado_alfa):
            raise ValueError(f"Dígito de control incorrecto en CIF '{valor}'")
        return doc

    if control != esperado:
        raise ValueError(
            f"Dígito de control incorrecto en CIF '{valor}' (se esperaba '{esperado}')"
        )
    return doc


# Prefijos de los números de IVA de los demás Estados de la UE (VIES; Grecia
# usa EL; XI = Irlanda del Norte).
_PREFIJOS_IVA_UE = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "FI", "FR", "HR", "HU", "IE",
    "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK", "XI",
}
_IVA_UE_RE = re.compile(r"^[A-Z0-9]{2,13}$")


def validar_nif_cif(valor: str) -> str:
    """Acepta indistintamente NIF de persona física, NIE o CIF de empresa.

    También el número de IVA de otro Estado de la UE (p. ej. PT509871236):
    en el cabotaje, incluido en el DeCA según el preámbulo de la Resolución
    de 5 de junio de 2026, el transportista efectivo no residente puede no
    tener NIF español. El art. 6.b de la Orden FOM/2861/2012 pide «NIF» y
    ninguna norma concreta el identificador del no residente: admitir el
    NIF-IVA es una interpretación, no un texto expreso. De esos números solo
    se comprueba la forma (cada país tiene su propio control). Ningún
    documento español empieza por dos letras, así que no hay confusión.
    Con prefijo ES se valida el NIF español que sigue.
    """
    doc = _normalizar(valor)
    if not doc:
        raise ValueError("El NIF/CIF no puede estar vacío")
    if doc.startswith("ES") and len(doc) > 9:
        return validar_nif_cif(doc[2:])
    if doc[:2] in _PREFIJOS_IVA_UE:
        if _IVA_UE_RE.match(doc[2:]):
            return doc
        raise ValueError(f"Número de IVA comunitario no válido: '{valor}'")
    if doc[0] in _NIE_PREFIJOS or doc[0].isdigit() or _NIF_KLM_RE.match(doc):
        return validar_nif_nie(doc)
    try:
        return validar_cif(doc)
    except ValueError:
        return validar_nif_nie(doc)


_PAIS_RE = re.compile(r"^[A-Z]{2}$")
_MATRICULA_EXTRANJERA_RE = re.compile(r"^[A-Z0-9]{2,12}$")


def validar_pais(valor: str) -> str:
    """Código de país ISO 3166-1 alfa-2 (ES, PT, FR...)."""
    pais = valor.strip().upper()
    if not _PAIS_RE.match(pais):
        raise ValueError(f"Código de país no válido: '{valor}' (dos letras, p. ej. ES, PT, FR)")
    return pais


def validar_matricula(valor: str, pais: str = "ES") -> str:
    """Valida matrícula española (formato actual 1234BBC, anterior M-1234-AB
    o de remolque R-1234-BCD).

    Con `pais` distinto de ES (cabotaje: transporte interior hecho por un
    transportista de otro Estado, incluido en el DeCA según la Resolución
    de 5 de junio de 2026) solo se comprueba que sea alfanumérica de 2 a 12
    caracteres: cada país tiene su formato y no se pueden validar todos.
    """
    mat = _normalizar(valor)
    if pais != "ES":
        if _MATRICULA_EXTRANJERA_RE.match(mat):
            return mat
        raise ValueError(f"Matrícula extranjera no válida: '{valor}' (solo letras y números)")
    if (
        _MATRICULA_ACTUAL_RE.match(mat)
        or _MATRICULA_ANTIGUA_RE.match(mat)
        or _MATRICULA_REMOLQUE_RE.match(mat)
    ):
        return mat
    raise ValueError(f"Formato de matrícula no reconocido: '{valor}'")
