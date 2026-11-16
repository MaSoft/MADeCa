"""Historial: periodos, filtros y permisos (los cálculos se prueban contra
PostgreSQL real; aquí, lo que no necesita base de datos)."""

from datetime import date

from app.estadisticas import VACIO, calcular_periodo, leer_filtros, tramos, Consulta


def test_periodos():
    p = calcular_periodo("dia", "2026-10-05", None, None)
    assert (p.inicio, p.fin, p.etiqueta, p.anterior, p.siguiente) == (date(2026, 10, 5), date(2026, 10, 6), "05/10/2026",
                                                                     date(2026, 10, 4), date(2026, 10, 6))
    p = calcular_periodo("semana", "2026-10-07", None, None)
    assert (p.inicio, p.fin) == (date(2026, 10, 5), date(2026, 10, 12)) and p.etiqueta.startswith("Semana 41")
    p = calcular_periodo("mes", "2026-12-15", None, None)
    assert (p.inicio, p.fin, p.etiqueta, p.siguiente) == (date(2026, 12, 1), date(2027, 1, 1), "Diciembre 2026", date(2027, 1, 1))
    p = calcular_periodo("trimestre", "2026-08-20", None, None)
    assert (p.inicio, p.fin, p.etiqueta) == (date(2026, 7, 1), date(2026, 10, 1), "3.º trimestre 2026")
    p = calcular_periodo("anio", "2025-03-01", None, None)
    assert (p.inicio, p.fin, p.etiqueta) == (date(2025, 1, 1), date(2026, 1, 1), "2025")
    p = calcular_periodo("personalizado", None, "2026-10-01", "2026-10-15")
    assert (p.inicio, p.fin, p.anterior) == (date(2026, 10, 1), date(2026, 10, 16), date(2026, 9, 16))
    p = calcular_periodo("personalizado", None, "2026-10-15", "2026-10-01")
    assert (p.inicio, p.fin) == (date(2026, 10, 1), date(2026, 10, 16)) and "intercambiado" in p.error
    assert calcular_periodo("inventado", "2026-10-07", None, None).tipo == "mes"


def test_filtros_solo_los_validos():
    filtros, tramo = leer_filtros({"destinatario": "B00345678", "estado": "entregado", "firmado": "quizas", "inventado": "x",
                                   "b_estado": "descartado", "prov_destino": VACIO, "tramo": "2026-10-09:2026-10-11"})
    assert filtros == {"destinatario": "B00345678", "estado": "entregado", "b_estado": "descartado", "prov_destino": VACIO}
    assert tramo == (date(2026, 10, 9), date(2026, 10, 11))
    assert leer_filtros({"tramo": "2026-10-11:2026-10-09"})[1] is None


def test_tramos_de_la_evolucion():
    mes = Consulta(calcular_periodo("mes", "2026-10-07", None, None), "transporte")
    unidad, grupos = tramos(mes)
    assert unidad == "día" and len(grupos) == 31
    unidad, grupos = tramos(Consulta(calcular_periodo("trimestre", "2026-10-07", None, None), "transporte"))
    assert unidad == "semana" and grupos[0][0] == date(2026, 10, 1)
    unidad, grupos = tramos(Consulta(calcular_periodo("anio", "2026-10-07", None, None), "transporte"))
    assert unidad == "mes" and len(grupos) == 12 and grupos[-1][2] == "Dic 26"


def test_solo_administradores():
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app
    from app.security.dependencies import SesionUsuario, obtener_usuario_actual

    app.dependency_overrides[obtener_usuario_actual] = lambda: SesionUsuario(uuid.uuid4(), uuid.uuid4(), "operador")
    try:
        c = TestClient(app)
        assert c.get("/dashboard/historial").status_code == 403
        assert c.get("/dashboard/historial/exportar").status_code == 403
    finally:
        app.dependency_overrides.clear()
