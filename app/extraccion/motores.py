"""Motores de OCR para documentos sin texto (escaneos, fotos).

- "tesseract": local, en la máquina que ejecuta la extracción (servidor
  MADeCa o equipo del cliente con el conector local). Necesita el
  programa Tesseract instalado con el idioma español (spa).
- "azure": Azure AI Document Intelligence, modelo prebuilt-read, API
  2024-11-30. Es un tercero: solo con autorización expresa de la empresa
  (subencargado, art. 28.2 RGPD) y con un recurso en región UE.
"""

from __future__ import annotations

import base64
import io
import os
import threading
import time

import httpx

from app.extraccion.posiciones import pagina_desde_tesseract, paginas_desde_azure


class MotorNoDisponibleError(Exception):
    pass


class OcrTiempoAgotadoError(MotorNoDisponibleError):
    """El OCR de un documento superó el tiempo máximo. El documento sigue
    llegando a la bandeja con el aviso, para revisarlo a mano."""


# Máximo de OCR simultáneos en este proceso (se fija con el primer motor).
_turnos: threading.BoundedSemaphore | None = None
_turnos_lock = threading.Lock()


def _semaforo(simultaneos: int) -> threading.BoundedSemaphore:
    global _turnos
    with _turnos_lock:
        if _turnos is None:
            _turnos = threading.BoundedSemaphore(max(1, simultaneos))
        return _turnos


class MotorOCR:
    nombre = "base"
    es_tercero = False

    def reconocer(self, contenido: bytes, tipo: str) -> str:
        """`tipo`: "pdf" o "imagen". Devuelve el texto reconocido."""
        return self.reconocer_con_posiciones(contenido, tipo)[0]

    def reconocer_con_posiciones(self, contenido: bytes, tipo: str) -> tuple[str, list[dict] | None]:
        """(texto, páginas con la posición de cada palabra o None); ver
        app/extraccion/posiciones.py."""
        raise NotImplementedError


class MotorTesseract(MotorOCR):
    nombre = "tesseract"

    # Lado mayor máximo de la imagen que se pasa al OCR: las fotos de móvil
    # de 12-50 Mpx tardan mucho más sin ganar precisión.
    LADO_MAXIMO_PX = 3500

    def __init__(self, ruta_ejecutable: str | None = None, idioma: str = "spa",
                 limite_segundos: int = 60, simultaneos: int = 2):
        # Tesseract reparte cada OCR en varios hilos (OpenMP). Con varios OCR
        # a la vez en pocos núcleos los hilos compiten: un solo documento
        # llegó a tardar más de 10 minutos y MADeCa dejó de responder. Con un
        # hilo por OCR el tiempo es estable. Lo heredan los procesos
        # tesseract que lanza pytesseract.
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")
        self.limite_segundos = max(5, int(limite_segundos))
        self._turnos = _semaforo(simultaneos)
        try:
            import pytesseract
        except ImportError as exc:
            raise MotorNoDisponibleError("Falta el paquete pytesseract") from exc
        if ruta_ejecutable:
            pytesseract.pytesseract.tesseract_cmd = ruta_ejecutable
        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:
            raise MotorNoDisponibleError(
                "Tesseract no está instalado o no se encuentra (indica su ruta en la configuración)"
            ) from exc
        self._pytesseract = pytesseract
        self.idioma = idioma

    def _imagenes(self, contenido: bytes, tipo: str):
        from PIL import Image

        def reducida(img):
            if max(img.size) > self.LADO_MAXIMO_PX:
                img = img.copy()
                img.thumbnail((self.LADO_MAXIMO_PX, self.LADO_MAXIMO_PX))
            return img

        if tipo == "imagen":
            yield reducida(Image.open(io.BytesIO(contenido)))
            return
        # PDF escaneado: cada página suele ser una imagen incrustada. Se
        # extraen con pypdf (sin poppler ni otras librerías nativas).
        from pypdf import PdfReader

        for pagina in PdfReader(io.BytesIO(contenido)).pages:
            for imagen in pagina.images:
                yield reducida(imagen.image)

    def reconocer_con_posiciones(self, contenido: bytes, tipo: str) -> tuple[str, list[dict]]:
        # Turno: con muchos documentos a la vez esperan aquí, en lugar de
        # lanzar decenas de procesos tesseract.
        if not self._turnos.acquire(timeout=self.limite_segundos * 4):
            raise OcrTiempoAgotadoError("hay demasiados documentos leyéndose a la vez; revísalo a mano")
        try:
            textos, paginas = [], []
            for img in self._imagenes(contenido, tipo):
                try:
                    # Una sola lectura da el texto y dónde está cada palabra.
                    datos = self._pytesseract.image_to_data(
                        img, lang=self.idioma, timeout=self.limite_segundos, output_type=self._pytesseract.Output.DICT
                    )
                    pagina, texto = pagina_desde_tesseract(datos, *img.size)
                    textos.append(texto)
                    paginas.append(pagina)
                except RuntimeError as exc:
                    # pytesseract mata el proceso y lanza RuntimeError al agotar el tiempo.
                    if "timeout" in str(exc).lower():
                        raise OcrTiempoAgotadoError(
                            f"la lectura automática superó {self.limite_segundos} s por página; revisa el documento a mano"
                        ) from exc
                    raise
            return "\n".join(textos), paginas
        finally:
            self._turnos.release()


class MotorAzure(MotorOCR):
    nombre = "azure"
    es_tercero = True
    API_VERSION = "2024-11-30"

    def __init__(self, endpoint: str, clave: str, espera_maxima_s: float = 120.0, cliente: httpx.Client | None = None):
        if not endpoint.startswith("https://"):
            raise MotorNoDisponibleError("El endpoint de Azure debe empezar por https://")
        if not clave:
            raise MotorNoDisponibleError("Falta la clave de Azure Document Intelligence")
        self.endpoint = endpoint.rstrip("/")
        self.clave = clave
        self.espera_maxima_s = espera_maxima_s
        self.cliente = cliente or httpx.Client(timeout=30.0)

    def reconocer_con_posiciones(self, contenido: bytes, tipo: str) -> tuple[str, list[dict]]:
        url = (
            f"{self.endpoint}/documentintelligence/documentModels/prebuilt-read:analyze"
            f"?_overload=analyzeDocument&api-version={self.API_VERSION}&locale=es-ES"
        )
        cabeceras = {"Ocp-Apim-Subscription-Key": self.clave}
        r = self.cliente.post(url, headers=cabeceras, json={"base64Source": base64.b64encode(contenido).decode()})
        if r.status_code != 202:
            raise MotorNoDisponibleError(f"Azure rechazó el análisis ({r.status_code}): {r.text[:300]}")
        operacion = r.headers.get("Operation-Location")
        if not operacion:
            raise MotorNoDisponibleError("Azure no devolvió Operation-Location")

        limite = time.monotonic() + self.espera_maxima_s
        while time.monotonic() < limite:
            time.sleep(float(r.headers.get("Retry-After", 1)) if r.status_code == 202 else 1.0)
            r = self.cliente.get(operacion, headers=cabeceras)
            r.raise_for_status()
            datos = r.json()
            estado = datos.get("status")
            if estado == "succeeded":
                resultado = datos.get("analyzeResult") or {}
                return resultado.get("content", ""), paginas_desde_azure(resultado)
            if estado == "failed":
                raise MotorNoDisponibleError(f"Azure no pudo analizar el documento: {datos.get('error')}")
        raise MotorNoDisponibleError("Azure no terminó el análisis en el tiempo máximo")


def crear_motor(nombre: str, config: dict | None = None) -> MotorOCR:
    config = config or {}
    if nombre == "tesseract":
        return MotorTesseract(ruta_ejecutable=config.get("tesseract_ruta") or None)
    if nombre == "azure":
        return MotorAzure(endpoint=config.get("azure_endpoint", ""), clave=config.get("azure_clave", ""))
    raise MotorNoDisponibleError(f"Motor de OCR desconocido: {nombre}")
