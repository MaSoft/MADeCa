"""Albarán con bloques titulados y subetiquetas (maquetación habitual de un
ERP): antes salían «Razón social:» como nombre, «(ORIGEN)» como lugar,
«TRANSPORTADA» como mercancía, sin peso y con «No precisa» como
autorización especial."""

from app.extraccion import extraer_campos

TEXTO = """ALBARÁN DE TRANSPORTE
Albarán nº:
TC-261010-0042
Fecha de carga:
10/10/2026 07:45
CARGADOR CONTRACTUAL
Razón social:
Distribuciones Ejemplo SA
CIF/NIF:
B00345678
Domicilio:
Avda. Los Majuelos 31, 38108 La Laguna (Santa Cruz de
Tenerife)
TRANSPORTISTA EFECTIVO
Razón social:
Transportes Ejemplo SL
CIF/NIF:
B00654327
Domicilio:
C/ Las Gaviotas 14, 38509
Güímar
LUGAR DE CARGA (ORIGEN)
Centro:
Almacén Los Majuelos
Dirección:
Avda. Los Majuelos 31, 38108 La Laguna
LUGAR DE ENTREGA (DESTINO)
Centro:
Hiper Costa Sur
Dirección:
C/ Bentejuí 3, 38660 Adeje
VEHÍCULO Y CONDUCTOR
Matrícula tractora:
4127ZBK
Matrícula remolque:
R4412ZBC
Autorización especial de circulación:
No precisa
MERCANCÍA TRANSPORTADA
Nº
Naturaleza de la mercancía
Embalaje
Bultos
Peso bruto (kg)
1
Producto refrigerado (lácteos)
Palé europeo
14
9.800
Total bultos: 20
Peso bruto total: 13.700 kg
"""


def test_bloques_con_subetiquetas():
    r = extraer_campos(TEXTO)
    d = r.datos
    assert d["cargador"] == {"nombre_razon_social": "Distribuciones Ejemplo SA", "nif_cif": "B00345678",
                             "domicilio": "Avda. Los Majuelos 31, 38108 La Laguna (Santa Cruz de Tenerife)"}
    assert d["transportista"]["nif_cif"] == "B00654327"
    assert d["transportista"]["domicilio"] == "C/ Las Gaviotas 14, 38509 Güímar"
    assert d["origen_direccion"] == "Almacén Los Majuelos, Avda. Los Majuelos 31, 38108 La Laguna"
    assert d["destino_direccion"] == "Hiper Costa Sur, C/ Bentejuí 3, 38660 Adeje"
    assert d["mercancia"] == {"naturaleza": "Producto refrigerado (lácteos)", "peso_kg": 13700.0, "bultos": 20}
    assert d["referencia_externa"] == "TC-261010-0042"
    assert d["matricula_vehiculo"] == "4127ZBK" and d["matricula_remolque"] == "R4412ZBC"
    assert "autorizacion_especial_circulacion" not in d
    assert r.avisos == []


def test_autorizacion_real_se_conserva():
    r = extraer_campos("Autorización especial de circulación: AEC TF-457/2026\nMatrícula: 1234BCD")
    assert r.datos["autorizacion_especial_circulacion"] == "AEC TF-457/2026"


def test_nombre_en_mayusculas_con_dos_puntos_no_es_titulo():
    r = extraer_campos("Cargador: TRANSPORTES PEREZ SL\nCIF: B00345678\nCalle Mayor 3, 28001 Madrid")
    assert r.datos["cargador"]["nombre_razon_social"] == "TRANSPORTES PEREZ SL"
