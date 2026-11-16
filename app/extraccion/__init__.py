"""Obtención de datos del DeCA a partir de documentos de origen (albaranes,
cartas de porte, pedidos...) en PDF o imagen.

Marco legal (comprobado el 09/10/2026):
- Un escaneo NO vale como DeCA (Apartado Segundo.2, BOE-A-2026-12784).
  Este módulo solo extrae datos para que MADeCa genere un DeCA nativo
  digital (camino de generación); nunca se emite el documento escaneado.
- El cargador responde de los datos a)-d) y el transportista de los
  e)-g) del Art. 6 (Art. 7 Orden FOM/2861/2012, redacción de la Orden
  TRM/282/2026). Por eso lo extraído llega SIEMPRE como borrador a la
  bandeja de revisión y una persona lo confirma antes de emitir.
- Si el OCR lo hace un tercero (Azure), es un subencargado del
  tratamiento (art. 28.2 RGPD): solo se usa si la empresa lo ha
  autorizado expresamente en su configuración.

Este paquete no depende de la base de datos ni de FastAPI, para poder
usarlo también en el conector local que se instala en el cliente.
"""

from app.extraccion.campos import ResultadoExtraccion, extraer_campos
from app.extraccion.texto import ExtraccionError, obtener_texto

__all__ = ["ExtraccionError", "ResultadoExtraccion", "extraer_campos", "obtener_texto"]
