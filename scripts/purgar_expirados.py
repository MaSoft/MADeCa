"""Purga opcional de expediciones más antiguas que RETENTION_DAYS.

La normativa (Apartado Segundo.4, BOE-A-2026-12784) exige conservar el
DeCA COMO MÍNIMO 1 año; no exige borrarlo después. Este script es una
utilidad opcional de minimización de datos (RGPD) para operadores que
quieran purgar documentación antigua una vez superado el mínimo legal —
nunca se ejecuta automáticamente. Por defecto hace un "dry run": solo
lista qué borraría. Hace falta --confirmar para borrar de verdad.

Uso:
    python -m scripts.purgar_expirados                # dry-run
    python -m scripts.purgar_expirados --confirmar     # borra de verdad
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete, select

from app.config import get_settings
from app.database import SessionLocal
from app.domain.models import AccesoLog, BorradorDeca, EventoExpedicion, Expedicion, ModificacionDeca
from app.services.retention import puede_purgarse
from app.services.storage import get_storage


def main() -> None:
    parser = argparse.ArgumentParser(description="Purgar expediciones fuera del periodo de retención")
    parser.add_argument("--confirmar", action="store_true", help="Ejecuta el borrado real (por defecto es dry-run)")
    args = parser.parse_args()

    settings = get_settings()
    storage = get_storage()
    db = SessionLocal()
    try:
        candidatas = db.execute(select(Expedicion)).scalars().all()
        elegibles = [e for e in candidatas if puede_purgarse(e, settings)]

        if not elegibles:
            print("No hay expediciones fuera del periodo de retención mínimo.")
            return

        rutas_pdf: list[str] = []
        for exp in elegibles:
            accion = "BORRANDO" if args.confirmar else "[dry-run] purgaría"
            print(f"{accion}: {exp.id} (tenant={exp.tenant_id}, emitida={exp.fecha_emision})")
            if args.confirmar:
                # Los registros de consulta del QR (con IP: dato personal,
                # RGPD art. 5.1.e) se van con la expedición. Antes no se
                # borraban y su clave foránea hacía fallar el commit.
                db.execute(delete(AccesoLog).where(AccesoLog.expedicion_id == exp.id))
                # Actividad de la expedición (quién la emitió, modificó...):
                # toda expedición tiene al menos el evento «emitida» y su clave
                # foránea hacía fallar el commit de la purga.
                db.execute(delete(EventoExpedicion).where(EventoExpedicion.expedicion_id == exp.id))
                # Versiones anteriores conservadas por modificaciones
                # (Apartado Quinto): mismo plazo que el documento.
                modificaciones = db.execute(
                    select(ModificacionDeca).where(ModificacionDeca.expedicion_id == exp.id)
                ).scalars().all()
                rutas_pdf.extend(m.ruta_pdf_anterior for m in modificaciones)
                db.execute(delete(ModificacionDeca).where(ModificacionDeca.expedicion_id == exp.id))
                # Borradores de la bandeja ya emitidos como esta expedición
                # (sus datos están en el propio DeCA).
                db.execute(delete(BorradorDeca).where(BorradorDeca.expedicion_id == exp.id))
                db.delete(exp)
                rutas_pdf.append(exp.storage_path)

        if args.confirmar:
            # Primero la BD y después los ficheros: si el commit falla no
            # quedan expediciones apuntando a PDF ya borrados.
            db.commit()
            for ruta in rutas_pdf:
                try:
                    storage.eliminar(ruta)
                except Exception as exc:
                    print(f"AVISO: no se pudo borrar el fichero {ruta}: {exc}")
            print(f"Purgadas {len(elegibles)} expediciones.")
        else:
            print(f"{len(elegibles)} expediciones purgables. Ejecuta con --confirmar para borrarlas.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
