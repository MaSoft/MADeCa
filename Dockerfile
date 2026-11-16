FROM python:3.12-slim

# libpq: requerido por psycopg para PostgreSQL.
# Pango/Cairo/GDK-Pixbuf: requeridos por WeasyPrint para redactar el PDF
# cuando el ERP no genera uno propio (camino de generación). El camino
# passthrough (pypdf + reportlab) no las necesita, pero conviven en la
# misma imagen.
# tesseract-ocr(+spa): OCR local de documentos escaneados para la bandeja
# de revisión (los datos no salen del servidor de MADeCa).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu-core     tesseract-ocr     tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

# Un hilo por OCR: con varios Tesseract a la vez, OpenMP hacía que los
# hilos compitieran y el servidor dejaba de responder.
ENV OMP_THREAD_LIMIT=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini .

RUN useradd -m -u 1000 madeca && \
    mkdir -p /app/storage/pdfs /app/certs && \
    chown -R madeca:madeca /app
USER madeca

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
