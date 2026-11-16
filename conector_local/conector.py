"""Conector local de MADeCa.

Se instala en un equipo de la red del cliente. Vigila carpetas (NAS,
unidades de red, carpetas compartidas), extrae los datos del DeCA de cada
albarán nuevo EN ESE EQUIPO (texto del PDF u OCR con Tesseract local) y
envía a MADeCa solo los datos, que llegan a la bandeja de revisión. Los
documentos no salen de la red del cliente.

Uso:
    python conector.py --config conector.ini            # vigila sin parar
    python conector.py --config conector.ini --una-vez  # una pasada (Programador de tareas)

La clave de API se lee de la variable de entorno MADECA_API_KEY (no se
guarda en el .ini salvo que se indique expresamente).
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# El paquete de extracción es el mismo que usa el servidor (app/extraccion):
# se busca en la carpeta del conector o en la del proyecto.
_AQUI = Path(__file__).resolve().parent
for _ruta in (_AQUI, _AQUI.parent):
    if (_ruta / "app" / "extraccion").is_dir() and str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

import httpx  # noqa: E402

from app.extraccion import ExtraccionError, extraer_campos, obtener_texto  # noqa: E402
from app.extraccion.motores import MotorNoDisponibleError, MotorTesseract  # noqa: E402
from app.extraccion.texto import EXTENSIONES_SOPORTADAS  # noqa: E402

log = logging.getLogger("conector_local")
VERSION = "1.0.0"


class Configuracion:
    def __init__(self, ruta_ini: Path):
        if not ruta_ini.is_file():
            raise SystemExit(f"No existe el fichero de configuración: {ruta_ini}")
        ini = configparser.ConfigParser()
        ini.read(ruta_ini, encoding="utf-8")
        self.url = ini.get("madeca", "url", fallback="").rstrip("/")
        # http solo hacia el propio equipo (pruebas con MADeCa en local).
        local = self.url.startswith(("http://localhost", "http://127.0.0.1"))
        if not (self.url.startswith("https://") or local):
            raise SystemExit("[madeca] url debe empezar por https:// (o http://localhost para pruebas)")
        self.api_key = os.environ.get("MADECA_API_KEY") or ini.get("madeca", "api_key", fallback="")
        if not self.api_key.startswith("mad_live_"):
            raise SystemExit("Falta la clave de API (variable de entorno MADECA_API_KEY)")
        self.carpetas = [Path(c.strip()) for c in ini.get("carpetas", "rutas", fallback="").split(";") if c.strip()]
        if not self.carpetas:
            raise SystemExit("[carpetas] rutas está vacío")
        mover = ini.get("carpetas", "mover_procesados_a", fallback="").strip()
        self.mover_a = Path(mover) if mover else None
        self.intervalo = max(30, ini.getint("carpetas", "intervalo_segundos", fallback=120))
        self.tesseract = ini.get("ocr", "tesseract_ruta", fallback="").strip() or None
        self.estado = Path(ini.get("estado", "fichero", fallback=str(_AQUI / "estado_conector.json")))
        self.registro = Path(ini.get("estado", "registro", fallback=str(_AQUI / "conector.log")))


class Estado:
    """Ficheros ya enviados (ruta + fecha de modificación), para no
    repetirlos. MADeCa además es idempotente por referencia."""

    def __init__(self, ruta: Path):
        self.ruta = ruta
        try:
            self.enviados: dict = json.loads(ruta.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self.enviados = {}

    def ya_enviado(self, clave: str) -> bool:
        return clave in self.enviados

    def marcar(self, clave: str, borrador_id: str) -> None:
        self.enviados[clave] = borrador_id
        temporal = self.ruta.with_suffix(".tmp")
        temporal.write_text(json.dumps(self.enviados, ensure_ascii=False, indent=0), encoding="utf-8")
        temporal.replace(self.ruta)


def _ficheros(carpetas: list[Path]):
    for carpeta in carpetas:
        if not carpeta.is_dir():
            log.warning("Carpeta no accesible: %s", carpeta)
            continue
        for f in sorted(carpeta.iterdir()):
            if f.is_file() and f.suffix.lower() in EXTENSIONES_SOPORTADAS:
                yield f


def _estable(f: Path) -> bool:
    """No leer un fichero que todavía se está copiando."""
    try:
        t1 = f.stat().st_size
        time.sleep(0.5)
        return f.stat().st_size == t1
    except OSError:
        return False


def una_pasada(config: Configuracion, estado: Estado, cliente: httpx.Client, motor) -> int:
    enviados = 0
    for f in _ficheros(config.carpetas):
        clave = f"{f}@{int(f.stat().st_mtime)}"
        if estado.ya_enviado(clave) or not _estable(f):
            continue
        try:
            texto = obtener_texto(f.read_bytes(), f.name, motor)
            resultado = extraer_campos(texto.texto)
            datos, confianza, avisos = resultado.datos, resultado.confianza, resultado.avisos
        except (ExtraccionError, OSError) as exc:
            datos, confianza, avisos = {}, {}, [f"No se pudo leer el documento en el equipo del cliente: {exc}"]
        cuerpo = {
            "referencia_fuente": clave[:500], "origen": "conector_local", "nombre_documento": f.name[:300],
            "datos": datos, "confianza": confianza, "avisos": avisos[:50],
        }
        try:
            r = cliente.post(f"{config.url}/api/v1/borradores", json=cuerpo, headers={"X-API-Key": config.api_key})
        except httpx.HTTPError as exc:
            log.error("Sin conexión con MADeCa (%s); se reintentará", exc)
            return enviados
        if r.status_code in (200, 201):
            estado.marcar(clave, r.json().get("id", ""))
            enviados += 1
            log.info("Enviado %s -> borrador %s", f.name, r.json().get("id"))
            if config.mover_a:
                config.mover_a.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(config.mover_a / f.name))
        elif r.status_code in (401, 403):
            log.error("MADeCa rechaza la clave de API (%s): revisa MADECA_API_KEY", r.status_code)
            return enviados
        else:
            log.error("MADeCa devolvió %s para %s: %s", r.status_code, f.name, r.text[:200])
    return enviados


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Conector local de MADeCa")
    parser.add_argument("--config", default=str(_AQUI / "conector.ini"))
    parser.add_argument("--una-vez", action="store_true", help="Una sola pasada y salir")
    args = parser.parse_args(argv)

    config = Configuracion(Path(args.config))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(config.registro, encoding="utf-8")],
    )
    try:
        motor = MotorTesseract(config.tesseract)
    except MotorNoDisponibleError as exc:
        motor = None
        log.warning("OCR no disponible (%s): los escaneos llegarán a la bandeja sin datos", exc)
    estado = Estado(config.estado)
    log.info("Conector local %s: %d carpeta(s) -> %s", VERSION, len(config.carpetas), config.url)
    with httpx.Client(timeout=30.0, headers={"User-Agent": f"MADeCa-conector-local/{VERSION}"}) as cliente:
        while True:
            una_pasada(config, estado, cliente, motor)
            if args.una_vez:
                break
            time.sleep(config.intervalo)


if __name__ == "__main__":
    main()
