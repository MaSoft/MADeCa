# Conector local de MADeCa

Para clientes cuyos albaranes están en un NAS o en carpetas de su red sin
acceso desde internet, o que no quieren que sus documentos salgan de su
red. El conector:

1. Vigila las carpetas indicadas (admite rutas de red `\\NAS\...`).
2. Extrae en ese mismo equipo los datos del DeCA de cada documento nuevo
   (texto del PDF, u OCR con Tesseract para escaneos y fotos).
3. Envía **solo los datos** a MADeCa (`POST /api/v1/borradores`), que
   aparecen en la bandeja de revisión. El documento no sale de la red.

## Requisitos

- Python 3.12 o superior y `pip install -r requirements.txt`.
- Tesseract OCR con el idioma español, si hay escaneos o fotos.
- La carpeta `app/extraccion` y `app/domain/validation.py` del proyecto
  junto al conector (la misma extracción que usa el servidor).
- Una clave de API de MADeCa (Panel → Integraciones ERP).

## Configuración

1. Copia `conector.ini.ejemplo` como `conector.ini` y ajústalo.
2. Define la variable de entorno `MADECA_API_KEY` con la clave de API para
   el usuario de Windows que ejecuta el conector (no la pongas en el .ini).
3. Prueba: `python conector.py --config conector.ini --una-vez`.
4. Para dejarlo funcionando: tarea programada de Windows que ejecute
   `python conector.py --config conector.ini` al iniciar el equipo, o la
   versión `--una-vez` cada pocos minutos.

## Seguridad

- Solo se conecta a MADeCa por `https://` y verifica el certificado.
- Guarda qué ficheros ya envió (`[estado] fichero`) para no repetirlos;
  MADeCa además descarta duplicados.
- Si MADeCa rechaza la clave, se detiene la pasada sin mover ni perder
  ningún documento.
