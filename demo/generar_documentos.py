"""Genera los documentos de demostración en demo/documentos/.

    python demo/generar_documentos.py

Todos los datos son ficticios (empresas, NIF con dígito de control válido
para que pasen la validación, matrículas inventadas). Las fechas se
calculan respecto al día en que se genera.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

SALIDA = Path(__file__).resolve().parent / "documentos"

CARGADOR = ("Distribuciones Demo SL", "B00345678", "Calle Industria 14, 28001 Madrid")
TRANSPORTISTA = ("Transportes Rápidos Demo SL", "B00654327")
DESTINATARIO = ("Cliente Final Demo SA", "A00468132", "Polígono Sur, parcela 4, 41001 Sevilla")
MAÑANA = (datetime.now() + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)


def texto_albaran(numero: str, matricula: str = "1234BCD", peso: str = "1.250,5 kg") -> str:
    return f"""ALBARÁN DE ENTREGA (documento de demostración)
Albarán nº: {numero}
Fecha de carga: {MAÑANA:%d/%m/%Y %H:%M}
Cargador: {CARGADOR[0]}   CIF: {CARGADOR[1]}
{CARGADOR[2]}
Transportista: {TRANSPORTISTA[0]}
NIF: {TRANSPORTISTA[1]}
Lugar de carga: Nave 3, Polígono Norte, Getafe (Madrid)
Lugar de entrega: {DESTINATARIO[2]}
Destinatario: {DESTINATARIO[0]}  {DESTINATARIO[1]}
Mercancía: Palés de material eléctrico
Bultos: 12
Peso bruto: {peso}
Matrícula: {matricula}   Remolque: R-5678-BBC"""


def pdf_texto(texto: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 790
    for i, linea in enumerate(texto.splitlines()):
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 13 if i == 0 else 11)
        c.drawString(50, y, linea)
        y -= 20
    c.save()
    return buf.getvalue()


def _fuente(tam: int):
    for nombre in ("arial.ttf", "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def imagen_escaneo(texto: str) -> Image.Image:
    """Simula un albarán escaneado: solo imagen, sin capa de texto."""
    img = Image.new("RGB", (1654, 2339), "white")  # A4 a 200 ppp
    d = ImageDraw.Draw(img)
    fuente = _fuente(34)
    y = 140
    for linea in texto.splitlines():
        d.text((110, y), linea, fill=(20, 20, 20), font=fuente)
        y += 58
    return img.rotate(0.6, expand=False, fillcolor="white")


def main() -> None:
    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "lote").mkdir(exist_ok=True)
    (SALIDA / "hotfolder" / "pdf").mkdir(parents=True, exist_ok=True)
    (SALIDA / "hotfolder" / "datos").mkdir(parents=True, exist_ok=True)

    # 1. PDF propio del ERP (camino 1: se le estampa el QR)
    (SALIDA / "01_albaran_erp.pdf").write_bytes(pdf_texto(texto_albaran("ALB-DEMO-001")))

    # 2. Escaneo: como imagen y como PDF solo-imagen (el camino 1 lo rechaza;
    #    la bandeja lo lee con OCR y genera un DeCA nativo)
    escaneo = imagen_escaneo(texto_albaran("ALB-DEMO-002", matricula="5678CDF"))
    escaneo.save(SALIDA / "02_albaran_escaneado.png")
    escaneo.save(SALIDA / "02_albaran_escaneado.pdf", "PDF", resolution=200)

    # 3. CMR en inglés/español con datos incompletos (la bandeja avisa de lo que falta)
    (SALIDA / "03_cmr_incompleto.pdf").write_bytes(pdf_texto(
        "CMR (documento de demostración)\n"
        f"1 Sender / Remitente: {CARGADOR[0]}\n{CARGADOR[1]}\n{CARGADOR[2]}\n"
        f"16 Carrier / Transportista: {TRANSPORTISTA[0]}\n"
        "Gross weight 980 kg"
    ))

    # 4. Carga por lotes (panel → Nueva expedición → Carga por lotes)
    filas = []
    for i, matricula in ((1, "1234BCD"), (2, "5678CDF")):
        nombre = f"alb_lote_{i}.pdf"
        (SALIDA / "lote" / nombre).write_bytes(pdf_texto(texto_albaran(f"ALB-LOTE-00{i}", matricula)))
        filas.append({"archivo": nombre, "referencia_externa": f"ALB-LOTE-00{i}", "matricula_vehiculo": matricula,
                      "matricula_remolque": "R5678BBC", "fecha_transporte": MAÑANA.isoformat(), "finalidad_contractual": "false"})
    with (SALIDA / "lote" / "metadatos.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)

    # 5. Datos completos para la API (camino 2: MADeCa redacta el PDF)
    datos = {
        "referencia_externa": "ALB-DEMO-API-001",
        "cargador": {"nombre_razon_social": CARGADOR[0], "nif_cif": CARGADOR[1], "domicilio": CARGADOR[2]},
        "transportista": {"nombre_razon_social": TRANSPORTISTA[0], "nif_cif": TRANSPORTISTA[1]},
        "destinatario": {"nombre_razon_social": DESTINATARIO[0], "nif_cif": DESTINATARIO[1], "domicilio": DESTINATARIO[2]},
        "origen_direccion": "Nave 3, Polígono Norte, Getafe (Madrid)",
        "destino_direccion": DESTINATARIO[2],
        "mercancia": {"naturaleza": "Palés de material eléctrico", "peso_kg": 1250.5, "bultos": 12},
        "fecha_transporte": MAÑANA.isoformat(),
        "matricula_vehiculo": "1234BCD",
        "matricula_remolque": "R5678BBC",
        "observaciones": "Expedición de demostración",
    }
    (SALIDA / "05_expedicion_datos.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")

    # 6. Modificación durante el servicio (cambio de vehículo por avería)
    (SALIDA / "06_modificacion.json").write_text(json.dumps({
        "motivo": "Cambio de vehículo por avería de la tractora",
        "cambios": [{"campo": "matricula_vehiculo", "anterior": "1234BCD", "nuevo": "5678CDF"}],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 7. Hot-folder: PDF + sidecar (modo pdf) y datos completos (modo datos)
    (SALIDA / "hotfolder" / "pdf" / "ALB-DEMO-HF-001.pdf").write_bytes(pdf_texto(texto_albaran("ALB-DEMO-HF-001")))
    (SALIDA / "hotfolder" / "pdf" / "ALB-DEMO-HF-001.json").write_text(json.dumps({
        "referencia_externa": "ALB-DEMO-HF-001", "matricula_vehiculo": "1234BCD", "fecha_transporte": MAÑANA.isoformat(),
    }, indent=2), encoding="utf-8")
    datos_hf = dict(datos, referencia_externa="ALB-DEMO-HF-002")
    (SALIDA / "hotfolder" / "datos" / "pedido_demo.json").write_text(json.dumps(datos_hf, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Documentos de demostración generados en {SALIDA}")


if __name__ == "__main__":
    main()
