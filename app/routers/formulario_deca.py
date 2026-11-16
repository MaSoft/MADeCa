"""Lectura del formulario de datos del DeCA (camino de generación).

Lo comparten el alta manual (/dashboard/expediciones/nueva-datos) y la
bandeja de revisión (/dashboard/bandeja/{id}), para que ambos apliquen
exactamente los mismos campos y validaciones (Art. 6 FOM/2861/2012).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.domain.schemas import DecaCreate
from app.zonas import local, zona_de, zona_predeterminada

CAMPOS_TEXTO = (
    "referencia_externa", "cargador_nombre", "cargador_nif", "cargador_domicilio",
    "transportista_nombre", "transportista_nif", "destinatario_nombre", "destinatario_nif",
    "destinatario_domicilio", "origen_direccion", "destino_direccion", "mercancia_naturaleza",
    "mercancia_peso_kg", "mercancia_magnitud_alternativa", "mercancia_bultos",
    "mercancia_adr_numero_onu", "mercancia_adr_denominacion", "matricula_vehiculo",
    "matricula_remolque", "pais_matricula_vehiculo", "pais_matricula_remolque",
    "autorizacion_especial_circulacion", "fecha_transporte", "zona_horaria", "observaciones",
)


def valores_desde_formulario(formulario: Any) -> dict:
    valores = {c: (formulario.get(c) or "").strip() for c in CAMPOS_TEXTO}
    valores["pais_matricula_vehiculo"] = valores["pais_matricula_vehiculo"] or "ES"
    valores["pais_matricula_remolque"] = valores["pais_matricula_remolque"] or "ES"
    valores["finalidad_contractual"] = (formulario.get("finalidad_contractual") or "") in ("true", "on", "1")
    return valores


def valores_desde_datos(datos: dict) -> dict:
    """Datos con la forma de DecaCreate (p. ej. un borrador extraído) ->
    valores del formulario, para precargarlo."""
    def g(ruta: str) -> str:
        grupo, _, sub = ruta.partition(".")
        v = (datos.get(grupo) or {}).get(sub) if sub else datos.get(grupo)
        return "" if v is None else str(v)

    fecha = g("fecha_transporte")
    zona = g("zona_horaria") or zona_predeterminada()
    if fecha:
        try:
            momento = datetime.fromisoformat(fecha)
            # Se muestra en la hora local del propio transporte.
            if momento.tzinfo is not None:
                zona = g("zona_horaria") or zona_de(momento)
                momento = local(momento, zona)
            fecha = momento.strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            fecha = ""
    return {
        "referencia_externa": g("referencia_externa"),
        "cargador_nombre": g("cargador.nombre_razon_social"),
        "cargador_nif": g("cargador.nif_cif"),
        "cargador_domicilio": g("cargador.domicilio"),
        "transportista_nombre": g("transportista.nombre_razon_social"),
        "transportista_nif": g("transportista.nif_cif"),
        "destinatario_nombre": g("destinatario.nombre_razon_social"),
        "destinatario_nif": g("destinatario.nif_cif"),
        "destinatario_domicilio": g("destinatario.domicilio"),
        "origen_direccion": g("origen_direccion"),
        "destino_direccion": g("destino_direccion"),
        "mercancia_naturaleza": g("mercancia.naturaleza"),
        "mercancia_peso_kg": g("mercancia.peso_kg"),
        "mercancia_magnitud_alternativa": g("mercancia.magnitud_alternativa"),
        "mercancia_bultos": g("mercancia.bultos"),
        "mercancia_adr_numero_onu": g("mercancia.adr_numero_onu"),
        "mercancia_adr_denominacion": g("mercancia.adr_denominacion"),
        "matricula_vehiculo": g("matricula_vehiculo"),
        "matricula_remolque": g("matricula_remolque"),
        "pais_matricula_vehiculo": g("pais_matricula_vehiculo") or "ES",
        "pais_matricula_remolque": g("pais_matricula_remolque") or "ES",
        "autorizacion_especial_circulacion": g("autorizacion_especial_circulacion"),
        "fecha_transporte": fecha,
        "zona_horaria": zona,
        "observaciones": g("observaciones"),
        "finalidad_contractual": bool(datos.get("finalidad_contractual")),
    }


def deca_desde_valores(valores: dict, erp_origen: str = "generico") -> tuple[DecaCreate | None, str | None]:
    """Devuelve (deca, None) si es válido o (None, mensaje_de_error)."""
    v = valores
    try:
        payload = {
            "referencia_externa": v["referencia_externa"],
            "erp_origen": erp_origen,
            "cargador": {
                "nombre_razon_social": v["cargador_nombre"],
                "nif_cif": v["cargador_nif"],
                "domicilio": v["cargador_domicilio"] or None,
            },
            "transportista": {"nombre_razon_social": v["transportista_nombre"], "nif_cif": v["transportista_nif"]},
            "destinatario": (
                {
                    "nombre_razon_social": v["destinatario_nombre"],
                    "nif_cif": v["destinatario_nif"],
                    "domicilio": v["destinatario_domicilio"] or None,
                }
                if v["destinatario_nombre"] and v["destinatario_nif"]
                else None
            ),
            "origen_direccion": v["origen_direccion"],
            "destino_direccion": v["destino_direccion"],
            "mercancia": {
                "naturaleza": v["mercancia_naturaleza"],
                # float()/int() pueden lanzar ValueError con un valor mal
                # escrito (p. ej. "abc" en peso_kg) -> capturado abajo.
                "peso_kg": float(v["mercancia_peso_kg"].replace(",", ".")) if v["mercancia_peso_kg"] else None,
                "magnitud_alternativa": v["mercancia_magnitud_alternativa"] or None,
                "bultos": int(v["mercancia_bultos"]) if v["mercancia_bultos"] else None,
                "adr_numero_onu": v["mercancia_adr_numero_onu"] or None,
                "adr_denominacion": v["mercancia_adr_denominacion"] or None,
            },
            "fecha_transporte": v["fecha_transporte"],
            "zona_horaria": v.get("zona_horaria") or None,
            "matricula_vehiculo": v["matricula_vehiculo"],
            "matricula_remolque": v["matricula_remolque"] or None,
            "pais_matricula_vehiculo": v["pais_matricula_vehiculo"] or "ES",
            "pais_matricula_remolque": v["pais_matricula_remolque"] or "ES",
            "autorizacion_especial_circulacion": v["autorizacion_especial_circulacion"] or None,
            "observaciones": v["observaciones"] or None,
            "finalidad_contractual": v["finalidad_contractual"],
        }
        return DecaCreate.model_validate(payload), None
    except ValidationError as exc:
        return None, "; ".join(e["msg"] for e in exc.errors())
    except ValueError as exc:
        return None, f"Dato numérico inválido: {exc}"
