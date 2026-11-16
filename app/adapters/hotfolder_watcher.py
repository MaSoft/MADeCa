"""Watcher de carpeta compartida para ERPs sin API (a3ERP, Sigrid u
otros). Admite los DOS caminos de ingesta, separados por subcarpeta para
que no haya ninguna ambigüedad sobre qué modo aplica a cada fichero:

    hotfolder_watch/incoming/<tenant_id>/<erp_origen>/pdf/
        ALB-2026-001.pdf     <- el ERP ya genera su propio documento
        ALB-2026-001.json    <- sidecar: solo referencia/matrícula/fecha

    hotfolder_watch/incoming/<tenant_id>/<erp_origen>/datos/
        pedido123.csv        <- el ERP NO genera PDF; manda los datos
                                 completos (cargador, transportista,
                                 mercancía...) y MADeCa redacta el
                                 documento. Puede tener varias filas
                                 (reparto multipunto: mismo origen,
                                 varios destinos -> una fila por envío).

En `pdf/`, el PDF y el sidecar pueden llegar en cualquier orden; no se
procesan hasta que ambos están presentes y estables.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from app.adapters.base import normalizar_e_validar_datos, normalizar_e_validar_metadata
from app.conectores.base import anidar
from app.config import get_settings
from app.database import SessionLocal
from app.domain.models import Tenant
from app.services.expedicion_service import (
    CertificadoNoDisponibleError,
    ExpedicionYaExisteError,
    crear_expedicion_desde_datos,
    crear_expedicion_desde_pdf,
)
from app.services.pdf_generator import (
    MotorPdfNoDisponibleError,
    PdfDemasiadoGrandeError,
    PdfInvalidoError,
    PdfPosibleEscaneoError,
)
from app.services.signing import FirmaError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hotfolder")

_SIDECAR_EXTENSIONES = (".json", ".csv")
_MODOS_VALIDOS = ("pdf", "datos")


def _leer_filas(ruta: Path) -> list[dict]:
    if ruta.suffix.lower() == ".json":
        contenido = json.loads(ruta.read_text(encoding="utf-8"))
        filas = contenido if isinstance(contenido, list) else [contenido]
        if not all(isinstance(f, dict) for f in filas):
            raise ValueError("Cada elemento del JSON debe ser un objeto")
        return filas
    if ruta.suffix.lower() == ".csv":
        with ruta.open(newline="", encoding="utf-8-sig") as f:
            # Un CSV es plano: las columnas «grupo.campo» (p. ej.
            # «cargador.nif_cif» o «Cabecera.NumeroAlbaran») se convierten
            # en datos anidados y las celdas vacías se quitan. Antes ningún
            # CSV del modo «datos» podía validarse (el DeCA tiene datos
            # anidados) y una celda vacía, p. ej. sin remolque, se rechazaba
            # como matrícula no válida.
            return [anidar({(k or "").strip(): (v or "").strip() for k, v in fila.items() if k}) for fila in csv.DictReader(f)]
    raise ValueError(f"Extensión no soportada: {ruta.suffix}")


def _extraer_contexto(ruta: Path, base: Path) -> tuple[uuid.UUID, str, str]:
    """<tenant_id>/<erp_origen>/<modo>/fichero, modo en {'pdf', 'datos'}."""
    relativo = ruta.relative_to(base)
    partes = relativo.parts
    if len(partes) < 4:
        raise ValueError(
            "Ruta de fichero inválida: se esperaba "
            "<tenant_id>/<erp_origen>/<pdf|datos>/fichero, obtenido: " + str(relativo)
        )
    tenant_id = uuid.UUID(partes[0])
    erp_origen = partes[1]
    modo = partes[2]
    if modo not in _MODOS_VALIDOS:
        raise ValueError(f"Carpeta de modo no reconocida: '{modo}' (debe ser 'pdf' o 'datos')")
    return tenant_id, erp_origen, modo


def _esperar_estabilidad(ruta: Path, intervalo: float = 0.5, intentos: int = 10) -> None:
    tamano_anterior = -1
    for _ in range(intentos):
        if not ruta.exists():
            return
        tamano_actual = ruta.stat().st_size
        if tamano_actual == tamano_anterior:
            return
        tamano_anterior = tamano_actual
        time.sleep(intervalo)


def _mover_a_procesados(rutas: list[Path], base: Path, destino_base: Path) -> None:
    for ruta in rutas:
        if not ruta.exists():
            continue
        destino = destino_base / ruta.relative_to(base)
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(ruta), str(destino))


def _mover_a_errores(rutas: list[Path], base: Path, destino_base: Path, errores: list[str]) -> None:
    destino_notas: Path | None = None
    for ruta in rutas:
        if not ruta.exists():
            continue
        try:
            relativo = ruta.relative_to(base)
        except ValueError:
            relativo = Path(ruta.name)
        destino = destino_base / relativo
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(ruta), str(destino))
        destino_notas = destino_notas or destino
    if destino_notas is not None:
        (destino_notas.parent / f"{destino_notas.stem}.errores.txt").write_text(
            "\n".join(errores), encoding="utf-8"
        )


def _error_empresa(db, tenant_id: uuid.UUID) -> str | None:
    """Mismo criterio que la API (obtener_tenant_por_api_key): una empresa
    inexistente o desactivada no puede emitir DeCA por ninguna vía. Sin
    esta comprobación, el hot-folder seguía aceptando ficheros de una
    empresa ya desactivada, y una carpeta con un UUID inexistente
    provocaba un IntegrityError no capturado."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        return f"No existe ninguna empresa con id {tenant_id}"
    if not tenant.activo:
        return f"La empresa {tenant_id} está desactivada"
    return None


# --- Modo "pdf": el ERP ya genera su propio documento ---


def _buscar_sidecar(pdf_path: Path) -> Path | None:
    base = pdf_path.with_suffix("")
    for ext in _SIDECAR_EXTENSIONES:
        candidato = base.with_suffix(ext)
        if candidato.exists():
            return candidato
    return None


def procesar_par_pdf(pdf_path: Path) -> None:
    settings = get_settings()
    incoming = settings.hotfolder_incoming_path
    procesados = settings.hotfolder_processed_path
    errores_dir = settings.hotfolder_error_path

    sidecar_path = _buscar_sidecar(pdf_path)
    if sidecar_path is None:
        return  # todavía no ha llegado el sidecar; se reintentará

    _esperar_estabilidad(pdf_path)
    _esperar_estabilidad(sidecar_path)
    if not pdf_path.exists() or not sidecar_path.exists():
        return

    try:
        tenant_id, erp_origen, _modo = _extraer_contexto(pdf_path, incoming)
    except (ValueError, IndexError) as exc:
        logger.error("Fichero mal ubicado, movido a errores: %s (%s)", pdf_path, exc)
        _mover_a_errores([pdf_path, sidecar_path], incoming, errores_dir, [str(exc)])
        return

    try:
        datos_sidecar = _leer_filas(sidecar_path)[0]
        pdf_bytes = pdf_path.read_bytes()
    except Exception as exc:
        logger.error("No se pudo leer %s / %s: %s", pdf_path, sidecar_path, exc)
        _mover_a_errores([pdf_path, sidecar_path], incoming, errores_dir, [str(exc)])
        return

    resultado = normalizar_e_validar_metadata(datos_sidecar, erp_origen=erp_origen)
    if not resultado.exito:
        logger.warning("Metadatos inválidos para %s: %s", pdf_path.name, resultado.errores)
        _mover_a_errores([pdf_path, sidecar_path], incoming, errores_dir, resultado.errores or [])
        return

    db = SessionLocal()
    try:
        error_empresa = _error_empresa(db, tenant_id)
        if error_empresa:
            logger.error("%s: %s", error_empresa, pdf_path.name)
            _mover_a_errores([pdf_path, sidecar_path], incoming, errores_dir, [error_empresa])
            return
        exp = crear_expedicion_desde_pdf(
            db, tenant_id=tenant_id, metadata=resultado.metadata, pdf_original_bytes=pdf_bytes,
            quien="Carpeta compartida", detalle=f"Carpeta compartida: {pdf_path.name}"[:1000],
        )
        logger.info("Expedición creada (PDF del ERP): %s (%s)", exp.id, pdf_path.name)
        _mover_a_procesados([pdf_path, sidecar_path], incoming, procesados)
    except ExpedicionYaExisteError as exc:
        logger.info("Ya procesado previamente (idempotencia): %s", exc)
        _mover_a_procesados([pdf_path, sidecar_path], incoming, procesados)
    except (
        PdfInvalidoError,
        PdfPosibleEscaneoError,
        PdfDemasiadoGrandeError,
        CertificadoNoDisponibleError,
        FirmaError,
    ) as exc:
        logger.error("Fallo procesando %s: %s", pdf_path.name, exc)
        _mover_a_errores([pdf_path, sidecar_path], incoming, errores_dir, [str(exc)])
    except Exception as exc:
        # Cualquier otro fallo (BD caída, almacenamiento, etc.): sin esta
        # captura la excepción subía al hilo de watchdog y el vigilante
        # dejaba de procesar ficheros sin ningún aviso.
        db.rollback()
        logger.exception("Error inesperado procesando %s", pdf_path.name)
        _mover_a_errores([pdf_path, sidecar_path], incoming, errores_dir, [f"Error inesperado: {exc}"])
    finally:
        db.close()


# --- Modo "datos": el ERP no genera PDF, MADeCa lo redacta ---


def procesar_fichero_datos(ruta: Path) -> None:
    settings = get_settings()
    incoming = settings.hotfolder_incoming_path
    procesados = settings.hotfolder_processed_path
    errores_dir = settings.hotfolder_error_path

    _esperar_estabilidad(ruta)
    if not ruta.exists():
        return

    try:
        tenant_id, erp_origen, _modo = _extraer_contexto(ruta, incoming)
    except (ValueError, IndexError) as exc:
        logger.error("Fichero mal ubicado, movido a errores: %s (%s)", ruta, exc)
        _mover_a_errores([ruta], incoming, errores_dir, [str(exc)])
        return

    try:
        filas = _leer_filas(ruta)
    except Exception as exc:
        logger.error("No se pudo leer %s: %s", ruta, exc)
        _mover_a_errores([ruta], incoming, errores_dir, [str(exc)])
        return

    errores_fichero: list[str] = []
    db = SessionLocal()
    try:
        error_empresa = _error_empresa(db, tenant_id)
        if error_empresa:
            logger.error("%s: %s", error_empresa, ruta.name)
            errores_fichero.append(error_empresa)
            filas = []
        for i, fila in enumerate(filas):
            resultado = normalizar_e_validar_datos(fila, erp_origen=erp_origen)
            if not resultado.exito:
                msg = f"Fila {i + 1}: {'; '.join(resultado.errores or [])}"
                logger.warning(msg)
                errores_fichero.append(msg)
                continue
            try:
                exp = crear_expedicion_desde_datos(db, tenant_id=tenant_id, deca=resultado.deca, quien="Carpeta compartida", detalle="Carpeta compartida: datos")
                logger.info("Expedición creada (datos): %s (fila %s de %s)", exp.id, i + 1, ruta.name)
            except ExpedicionYaExisteError as exc:
                logger.info("Fila %s ya procesada previamente (idempotencia): %s", i + 1, exc)
            except (CertificadoNoDisponibleError, PdfDemasiadoGrandeError, MotorPdfNoDisponibleError, FirmaError) as exc:
                msg = f"Fila {i + 1}: {exc}"
                logger.error(msg)
                errores_fichero.append(msg)
            except Exception as exc:
                db.rollback()
                msg = f"Fila {i + 1}: error inesperado: {exc}"
                logger.exception(msg)
                errores_fichero.append(msg)
    finally:
        db.close()

    if errores_fichero:
        _mover_a_errores([ruta], incoming, errores_dir, errores_fichero)
    else:
        _mover_a_procesados([ruta], incoming, procesados)


# --- Despacho común ---


def procesar_ruta(ruta: Path, incoming: Path) -> None:
    try:
        _tenant_id, _erp, modo = _extraer_contexto(ruta, incoming)
    except (ValueError, IndexError) as exc:
        logger.error("Fichero mal ubicado, movido a errores: %s (%s)", ruta, exc)
        _mover_a_errores([ruta], incoming, get_settings().hotfolder_error_path, [str(exc)])
        return

    if modo == "pdf":
        pdf_path = ruta if ruta.suffix.lower() == ".pdf" else ruta.with_suffix(".pdf")
        if pdf_path.exists():
            procesar_par_pdf(pdf_path)
    elif modo == "datos":
        if ruta.suffix.lower() in _SIDECAR_EXTENSIONES:
            procesar_fichero_datos(ruta)


class _Handler(FileSystemEventHandler):
    def __init__(self, incoming: Path):
        self.incoming = incoming

    def on_closed(self, event):
        self._despachar(event)

    def on_created(self, event):
        self._despachar(event)

    def on_moved(self, event):
        # Muchos ERP escriben con un nombre temporal y luego renombran (para
        # que nadie lea un fichero a medias): eso llega como «movido», no
        # como «creado», y el fichero se quedaba sin procesar hasta reiniciar.
        self._despachar(event, Path(event.dest_path))

    def _despachar(self, event, ruta: Path | None = None) -> None:
        if event.is_directory:
            return
        ruta = ruta or Path(event.src_path)
        if ruta.suffix.lower() not in (".pdf", *_SIDECAR_EXTENSIONES):
            return
        # Solo lo que queda dentro de la carpeta de entrada (no lo que el
        # propio vigilante mueve a procesados o a errores).
        try:
            ruta.resolve().relative_to(self.incoming.resolve())
        except ValueError:
            return
        try:
            procesar_ruta(ruta, self.incoming)
        except Exception:
            # Última red: una excepción aquí mataría el hilo del observer.
            logger.exception("Error no controlado con %s; el vigilante sigue activo", ruta)


def _barrido_inicial(incoming: Path) -> None:
    """Procesa lo que ya hubiera en la carpeta antes de arrancar (p. ej.
    tras un reinicio del contenedor) — si no, esos ficheros se quedarían
    esperando para siempre: ni el Observer nativo ni el PollingObserver
    emiten un evento de "creación" para algo que ya existía antes de
    empezar a vigilar.
    """
    pendientes = sorted(incoming.rglob("*.pdf")) + sorted(
        p for modo_dir in incoming.glob("*/*/datos") for p in modo_dir.iterdir() if p.suffix.lower() in _SIDECAR_EXTENSIONES
    )
    if pendientes:
        logger.info("Barrido inicial: %d ficheros pendientes de una ejecución anterior", len(pendientes))
    for ruta in pendientes:
        procesar_ruta(ruta, incoming)


def main() -> None:
    settings = get_settings()
    incoming = settings.hotfolder_incoming_path
    incoming.mkdir(parents=True, exist_ok=True)
    settings.hotfolder_processed_path.mkdir(parents=True, exist_ok=True)
    settings.hotfolder_error_path.mkdir(parents=True, exist_ok=True)

    _barrido_inicial(incoming)

    logger.info("Vigilando carpeta: %s", incoming.resolve())
    # PollingObserver, no el Observer nativo: los bind mounts de Docker
    # Desktop en Windows/Mac no propagan eventos inotify de forma fiable
    # cuando el fichero se escribe desde el lado host, así que el
    # observer nativo se queda sordo. Sondear cada segundo tiene un coste
    # mínimo para un hot-folder de documentos, no de eventos masivos.
    observer = PollingObserver(timeout=1)
    observer.schedule(_Handler(incoming), str(incoming), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
