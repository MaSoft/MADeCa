"""Libros de Excel (.xlsx) del panel: exportación de Expediciones e Historial.

- Hoja «Resumen»: título, fecha de generación, filtros aplicados y bloques
  de cifras.
- Hojas de datos como tablas de Excel (estilo, bandas, filtros en la
  cabecera), cabecera fija, anchos de columna, fechas y números con su
  formato y una fila de totales con SUBTOTAL (se recalcula al filtrar en
  Excel).
- Modo de escritura rápida de openpyxl: miles de filas sin agotar la
  memoria del servidor.
- Los textos que empiezan por «=», «+», «-» o «@» se guardan como texto,
  nunca como fórmula (inyección de fórmulas).
"""

from __future__ import annotations

import io
import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

try:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo
except ImportError as exc:  # instalación sin las dependencias de requirements.txt
    from fastapi import HTTPException

    raise HTTPException(503, "La exportación a Excel no está disponible en esta instalación: falta el paquete "
                             "openpyxl (instala requirements.txt o reconstruye la imagen de MADeCa).") from exc

from app.zonas import local

AZUL = "1A3A5C"
AZUL_CLARO = "EFF6FF"
GRIS = "64748B"
FORMATOS = {
    "fecha": "dd/mm/yyyy",
    "hora": "hh:mm",
    "fecha_hora": "dd/mm/yyyy hh:mm",
    "entero": "#,##0",
    "decimal": "#,##0.0##",
    "porcentaje": "0.0%",
    "texto": "@",
}
_FORMULA = ("=", "+", "-", "@")


@dataclass
class Columna:
    titulo: str
    ancho: float = 16
    formato: str | None = None  # clave de FORMATOS
    total: str | None = None  # "suma" | "cuenta" | "media"


def _valor_excel(valor):
    """Fechas con zona -> hora local sin zona (Excel no admite zonas)."""
    if isinstance(valor, datetime) and valor.tzinfo is not None:
        return local(valor).replace(tzinfo=None)
    return valor


class LibroExcel:
    def __init__(self, titulo: str):
        self.titulo = titulo
        self.wb = Workbook(write_only=True)
        self._tablas = 0
        self._f_cabecera = Font(bold=True, color="FFFFFF")
        self._r_cabecera = PatternFill("solid", fgColor=AZUL)
        self._borde_total = Border(top=Side(style="thin", color=AZUL))

    # ------------------------------------------------------------------
    def _celda(self, ws, valor, formato: str | None = None, negrita=False, color=None, tamano=None, relleno=None):
        valor = _valor_excel(valor)
        celda = WriteOnlyCell(ws, value=valor)
        if isinstance(valor, str) and valor.startswith(_FORMULA):
            celda.data_type = "s"  # texto literal, no fórmula
        if formato:
            celda.number_format = FORMATOS.get(formato, formato)
        if negrita or color or tamano:
            celda.font = Font(bold=negrita, color=color, size=tamano)
        if relleno:
            celda.fill = PatternFill("solid", fgColor=relleno)
        return celda

    def resumen(self, subtitulo: str, filtros: list[tuple[str, str]], bloques: list[tuple[str, list[tuple]]]) -> None:
        """Primera hoja. `bloques`: [(título, [(etiqueta, valor, formato?)])]."""
        ws = self.wb.create_sheet("Resumen")
        ws.column_dimensions["A"].width = 46
        ws.column_dimensions["B"].width = 26
        ws.sheet_view.showGridLines = False
        ws.append([self._celda(ws, self.titulo, negrita=True, color=AZUL, tamano=16)])
        ws.append([self._celda(ws, subtitulo, color=GRIS)])
        # Hora de la instalación (ZONA_HORARIA), no la del servidor (UTC en Docker).
        ws.append([self._celda(ws, f"Generado el {local(datetime.now(timezone.utc)):%d/%m/%Y a las %H:%M}", color=GRIS)])
        ws.append([])
        if filtros:
            ws.append([self._celda(ws, "Filtros aplicados", negrita=True, color="FFFFFF", relleno=AZUL),
                       self._celda(ws, "", relleno=AZUL)])
            for etiqueta, valor in filtros:
                ws.append([self._celda(ws, etiqueta), self._celda(ws, valor)])
            ws.append([])
        for titulo, filas in bloques:
            ws.append([self._celda(ws, titulo, negrita=True, color="FFFFFF", relleno=AZUL), self._celda(ws, "", relleno=AZUL)])
            for fila in filas:
                etiqueta, valor = fila[0], fila[1]
                formato = fila[2] if len(fila) > 2 else ("entero" if isinstance(valor, int) else None)
                ws.append([self._celda(ws, etiqueta), self._celda(ws, valor, formato)])
            ws.append([])
        ws.append([self._celda(ws, "MADeCa - Desarrollado por Masoft.es (https://masoft.es)", color=GRIS)])

    def tabla(self, nombre: str, columnas: list[Columna], filas: Iterable[list]) -> int:
        """Hoja con una tabla de Excel. Devuelve el número de filas."""
        ws = self.wb.create_sheet(nombre[:31])
        ws.freeze_panes = "A2"
        for i, col in enumerate(columnas, start=1):
            ws.column_dimensions[get_column_letter(i)].width = col.ancho
        cabecera = []
        for col in columnas:
            c = self._celda(ws, col.titulo)
            c.font, c.fill = self._f_cabecera, self._r_cabecera
            c.alignment = Alignment(vertical="center", wrap_text=True)
            cabecera.append(c)
        ws.append(cabecera)
        n = 0
        for fila in filas:
            ws.append([self._celda(ws, valor, col.formato) for valor, col in zip(fila, columnas)])
            n += 1
        ultima = get_column_letter(len(columnas))
        if n:
            self._tablas += 1
            tabla = Table(displayName=f"Tabla{self._tablas}_{re.sub(r'[^A-Za-z0-9]', '', nombre)[:20]}",
                          ref=f"A1:{ultima}{n + 1}")
            tabla.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            tabla.tableColumns = [TableColumn(id=i, name=col.titulo) for i, col in enumerate(columnas, start=1)]
            tabla.autoFilter = None
            with warnings.catch_warnings():
                # Aviso fijo del modo rápido; las columnas ya están puestas arriba.
                warnings.filterwarnings("ignore", message="In write-only mode you must add table columns manually")
                ws.add_table(tabla)
            # Totales: SUBTOTAL se recalcula al filtrar en Excel.
            if any(col.total for col in columnas):
                funciones = {"suma": 109, "cuenta": 103, "media": 101}
                total = []
                for i, col in enumerate(columnas, start=1):
                    letra = get_column_letter(i)
                    if i == 1 and not col.total:
                        valor = "Total"
                    elif col.total:
                        valor = f"=SUBTOTAL({funciones[col.total]},{letra}2:{letra}{n + 1})"
                    else:
                        valor = None
                    c = WriteOnlyCell(ws, value=valor)
                    c.font = Font(bold=True, color=AZUL)
                    c.border = self._borde_total
                    if col.total and col.total != "cuenta" and col.formato:
                        c.number_format = FORMATOS.get(col.formato, col.formato)
                    elif col.total == "cuenta":
                        c.number_format = FORMATOS["entero"]
                    total.append(c)
                ws.append(total)
        return n

    def guardar(self) -> bytes:
        salida = io.BytesIO()
        self.wb.save(salida)
        return salida.getvalue()


def respuesta_xlsx(contenido: bytes, nombre_base: str):
    from fastapi.responses import Response

    nombre = f"{nombre_base}_{local(datetime.now(timezone.utc)):%Y%m%d_%H%M}.xlsx"
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"', "Cache-Control": "no-store"},
    )


def solo_fecha(valor: datetime | None) -> date | None:
    return local(valor).date() if isinstance(valor, datetime) else None
