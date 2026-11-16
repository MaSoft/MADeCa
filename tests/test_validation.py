import pytest

from app.domain.validation import (
    validar_cif,
    validar_matricula,
    validar_nif_cif,
    validar_nif_nie,
)


def test_nif_valido():
    assert validar_nif_nie("12345678Z") == "12345678Z"


def test_nif_con_letra_incorrecta():
    with pytest.raises(ValueError):
        validar_nif_nie("12345678A")


def test_nie_valido():
    assert validar_nif_nie("X0000000T") == "X0000000T"


def test_nie_normaliza_espacios_y_guiones():
    assert validar_nif_nie(" x-0000000-t ") == "X0000000T"


def test_cif_valido_control_numerico():
    assert validar_cif("B00345678") == "B00345678"


def test_cif_con_digito_control_incorrecto():
    with pytest.raises(ValueError):
        validar_cif("B12345670")


def test_validar_nif_cif_acepta_los_tres_formatos():
    assert validar_nif_cif("12345678Z") == "12345678Z"
    assert validar_nif_cif("X0000000T") == "X0000000T"
    assert validar_nif_cif("B00345678") == "B00345678"


def test_validar_nif_cif_vacio():
    with pytest.raises(ValueError):
        validar_nif_cif("")


def test_matricula_formato_actual():
    assert validar_matricula("1234 bbc") == "1234BBC"


def test_matricula_formato_antiguo():
    assert validar_matricula("M-1234-AB") == "M1234AB"


def test_matricula_remolque():
    assert validar_matricula("R-1234-BCD") == "R1234BCD"


def test_matricula_remolque_con_vocal_prohibida():
    with pytest.raises(ValueError):
        validar_matricula("R1234BAD")


def test_matricula_con_vocal_prohibida_formato_actual():
    # El formato actual excluye vocales y la Ñ en las 3 letras finales.
    with pytest.raises(ValueError):
        validar_matricula("1234AOU")


def test_matricula_invalida():
    with pytest.raises(ValueError):
        validar_matricula("ABC123")


# --- Cabotaje: identificador fiscal de un transportista de otro Estado de la UE ---

@pytest.mark.parametrize("valor", ["PT509871236", "pt 509 871 236", "FR12345678901", "DE123456789"])
def test_nif_iva_comunitario_admitido(valor):
    from app.domain.validation import validar_nif_cif

    assert validar_nif_cif(valor) == valor.replace(" ", "").upper()


def test_nif_iva_espanol_valida_el_nif():
    from app.domain.validation import validar_nif_cif

    assert validar_nif_cif("ESB00345678") == "B00345678"
    with pytest.raises(ValueError):
        validar_nif_cif("ESB12345670")


def test_nif_iva_comunitario_mal_formado():
    from app.domain.validation import validar_nif_cif

    with pytest.raises(ValueError):
        validar_nif_cif("PT5")
