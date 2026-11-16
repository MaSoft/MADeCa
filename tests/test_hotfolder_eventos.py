"""El vigilante procesa también los ficheros renombrados dentro de la
carpeta de entrada (ERP que escriben con nombre temporal y renombran)."""

from pathlib import Path

from watchdog.events import FileCreatedEvent, FileMovedEvent

from app.adapters import hotfolder_watcher as hw


def test_renombrado_dentro_de_entrada_se_procesa(tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setattr(hw, "procesar_ruta", lambda ruta, incoming: llamadas.append(ruta))
    manejador = hw._Handler(tmp_path)
    origen = tmp_path / "t" / "generico" / "pdf" / "ALB-1.json.tmp"
    destino = tmp_path / "t" / "generico" / "pdf" / "ALB-1.json"
    manejador.on_moved(FileMovedEvent(str(origen), str(destino)))
    assert llamadas == [destino]


def test_lo_movido_fuera_de_entrada_se_ignora(tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setattr(hw, "procesar_ruta", lambda ruta, incoming: llamadas.append(ruta))
    entrada = tmp_path / "incoming"
    manejador = hw._Handler(entrada)
    manejador.on_moved(FileMovedEvent(str(entrada / "a.pdf"), str(tmp_path / "processed" / "a.pdf")))
    manejador.on_created(FileCreatedEvent(str(entrada / "nota.txt")))
    assert llamadas == []


def test_creado_sigue_funcionando(tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setattr(hw, "procesar_ruta", lambda ruta, incoming: llamadas.append(ruta))
    hw._Handler(tmp_path).on_created(FileCreatedEvent(str(tmp_path / "x" / "generico" / "datos" / "p.csv")))
    assert llamadas == [Path(tmp_path / "x" / "generico" / "datos" / "p.csv")]
