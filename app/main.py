from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.routers import (
    asistente_formulario,
    auth_routes,
    bandeja,
    contraparte,
    dashboard,
    erp_ingestion,
    fuentes,
    historial,
    public_inspector,
    superadmin,
)
from app.security.rate_limit import limiter
from app.version import VERSION

app = FastAPI(
    title="MADeCa",
    description="Plataforma de generación y gestión del Documento Electrónico de Control Administrativo (DeCA)",
    version=VERSION,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Defensa en profundidad: en producción, el proxy inverso (nginx/traefik)
# debe imponer también su propio client_max_body_size. Este límite
# aplicativo cubre despliegues sin proxy delante (p. ej. desarrollo).
TAMANO_MAXIMO_PETICION_BYTES = 12 * 1024 * 1024  # 12 MB (certificado + margen)
# Rutas que suben documentos de origen o varios PDF: cada documento puede
# llegar a DOCUMENTO_ORIGEN_MAX_BYTES (20 MB por defecto) y en el panel se
# suben varios a la vez. Antes todo se cortaba en 12 MB y un solo escaneo
# de 15 MB, admitido por la configuración, se rechazaba con un error.
DOCUMENTOS_POR_ENVIO = 10
RUTAS_SUBIDA = ("/dashboard/bandeja/subir", "/api/v1/borradores/documento", "/dashboard/expediciones/nueva-lote")


def tamano_maximo_peticion(ruta: str) -> int:
    if ruta in RUTAS_SUBIDA:
        from app.config import get_settings

        return max(TAMANO_MAXIMO_PETICION_BYTES, get_settings().documento_origen_max_bytes * DOCUMENTOS_POR_ENVIO + 1024 * 1024)
    return TAMANO_MAXIMO_PETICION_BYTES


@app.middleware("http")
async def limitar_tamano_peticion(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        # Una cabecera mal formada hacía saltar ValueError (error 500).
        try:
            tamano = int(content_length)
        except ValueError:
            return JSONResponse({"detail": "Cabecera Content-Length inválida"}, status_code=400)
        if tamano > tamano_maximo_peticion(request.url.path):
            return JSONResponse({"detail": "Petición demasiado grande"}, status_code=413)
    return await call_next(request)


@app.middleware("http")
async def cabeceras_seguridad(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    # setdefault: una ruta concreta puede fijar las suyas (p. ej. el
    # documento original de la bandeja, que se muestra en un iframe propio).
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'")
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(public_inspector.router)
app.include_router(contraparte.router)
app.include_router(erp_ingestion.router)
app.include_router(auth_routes.router)
app.include_router(dashboard.router)
app.include_router(bandeja.router)
app.include_router(asistente_formulario.router)
app.include_router(historial.router)
app.include_router(fuentes.router)
app.include_router(superadmin.router)


@app.get("/")
def raiz():
    return RedirectResponse(url="/login", status_code=303)


@app.get("/salud")
def salud():
    return {"estado": "ok"}
