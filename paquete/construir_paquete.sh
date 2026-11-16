#!/usr/bin/env bash
# Monta INSTALL/: todo lo necesario para instalar MADeCa desde cero en cualquier
# entorno (Linux de cualquier distribución, VPS, servidor propio, máquina
# virtual o Windows con WSL2), también SIN internet:
#
#   INSTALL/
#     LEEME_INSTALACION.txt
#     GUIA_INSTALACION_MADeCa.html, img_guia/
#     madeca-<versión>.tar.gz (+ .sha256)     Linux: copiar al servidor y «sudo ./madeca.sh instalar»
#       madeca-<versión>/ madeca.sh, docker-compose.yml, Caddyfile(.propio/.prueba), LICENCIA.txt,
#                         LEEME.txt, VERSION, GUIA_*.html, img_guia/, SHA256SUMS,
#                         imagen/madeca-<versión>.tar.gz   la aplicación (compilada, sin código fuente)
#                         imagen/base.tar.gz               PostgreSQL, Redis y Caddy
#                         docker/docker-*.tgz, docker/docker-compose, docker/iptables-*.tgz
#                                                          Docker para cualquier Linux sin internet
#     INSTALAR_EN_WINDOWS.bat
#     windows/madeca_windows.ps1, windows/ubuntu-24.04-wsl.rootfs.tar.gz
#
# Se lanza desde la raíz del proyecto, con Docker e internet, DESPUÉS de
# construir la imagen:  docker build -f Dockerfile.paquete -t madeca:<versión> .
#                       bash paquete/construir_paquete.sh
# Las descargas (Docker estático, Compose, Ubuntu para WSL) se guardan en
# ~/.cache/masoft_paquetes (o MASOFT_DESCARGAS) y se reutilizan.
set -euo pipefail

DOCKER_ESTATICO=27.5.1   # download.docker.com/linux/static/stable/x86_64
COMPOSE=2.32.4           # github.com/docker/compose/releases
UBUNTU_WSL="https://cloud-images.ubuntu.com/wsl/releases/24.04/current"
UBUNTU_WSL_FICHERO="ubuntu-noble-wsl-amd64-24.04lts.rootfs.tar.gz"
BASES=(postgres:16-alpine redis:7-alpine caddy:2.8.4-alpine)
IMAGEN=madeca

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RAIZ"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' app/version.py)"
[ -n "$VERSION" ] || { echo "No se pudo leer la versión de app/version.py" >&2; exit 1; }
NOMBRE="$IMAGEN-$VERSION"
SALIDA=INSTALL
DESCARGAS="${MASOFT_DESCARGAS:-$HOME/.cache/masoft_paquetes}"  # fuera del proyecto (OneDrive): se reutiliza entre proyectos
# Carpeta de trabajo dentro del proyecto y con ruta relativa: vale igual con
# el Python de Windows (Git Bash) que con el de Linux.
TRABAJO="$SALIDA/.paquete_en_curso"
rm -rf "$TRABAJO"
mkdir -p "$TRABAJO" "$DESCARGAS"
PAQ="$TRABAJO/$NOMBRE"
mkdir -p "$PAQ/imagen" "$PAQ/docker"

docker image inspect "$IMAGEN:$VERSION" >/dev/null || {
  echo "Falta la imagen $IMAGEN:$VERSION (docker build -f Dockerfile.paquete -t $IMAGEN:$VERSION .)" >&2; exit 1; }
# La imagen no debe llevar código fuente y debe ser de esta versión.
docker run --rm -e "ESPERADA=$VERSION" --entrypoint sh "$IMAGEN:$VERSION" -c '
  test -z "$(find /app -name "*.py"; find /app/app -name "*.pyc")" &&
  python -c "import os; from app.version import VERSION; assert VERSION == os.environ[\"ESPERADA\"]"' \
  || { echo "La imagen lleva código fuente o no es la versión $VERSION" >&2; exit 1; }

descargar() {  # descargar URL FICHERO
  [ -s "$2" ] && return 0
  echo "Descargando $(basename "$2")..."
  curl -fL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"
}
descargar "https://download.docker.com/linux/static/stable/x86_64/docker-$DOCKER_ESTATICO.tgz" "$DESCARGAS/docker-$DOCKER_ESTATICO.tgz"
descargar "https://github.com/docker/compose/releases/download/v$COMPOSE/docker-compose-linux-x86_64" "$DESCARGAS/docker-compose-$COMPOSE"
descargar "https://github.com/docker/compose/releases/download/v$COMPOSE/docker-compose-linux-x86_64.sha256" "$DESCARGAS/docker-compose-$COMPOSE.sha256"
(cd "$DESCARGAS" && echo "$(cut -d' ' -f1 "docker-compose-$COMPOSE.sha256")  docker-compose-$COMPOSE" | sha256sum -c --quiet) \
  || { echo "docker-compose descargado no coincide con su SHA256" >&2; exit 1; }
if [ ! -s "$DESCARGAS/iptables-1.8.10-nft-estatico.tgz" ]; then
  echo "Compilando iptables estático (una sola vez)..."
  docker build -q -f paquete/herramientas/iptables.Dockerfile -t masoft-iptables-estatico paquete/herramientas >/dev/null
  docker run --rm masoft-iptables-estatico tar czf - -C /salida iptables > "$DESCARGAS/iptables-1.8.10-nft-estatico.tgz"
fi
descargar "$UBUNTU_WSL/$UBUNTU_WSL_FICHERO" "$DESCARGAS/$UBUNTU_WSL_FICHERO"
descargar "$UBUNTU_WSL/SHA256SUMS" "$DESCARGAS/ubuntu-wsl.SHA256SUMS"
(cd "$DESCARGAS" && grep " \*\?$UBUNTU_WSL_FICHERO\$" ubuntu-wsl.SHA256SUMS | sed 's/ \*/  /' | sha256sum -c --quiet) \
  || { echo "La Ubuntu para WSL descargada no coincide con su SHA256SUMS" >&2; exit 1; }

for f in madeca.sh docker-compose.yml Caddyfile Caddyfile.propio Caddyfile.prueba LEEME.txt LICENCIA.txt; do
  tr -d '\r' < "paquete/$f" > "$PAQ/$f"
done
printf '%s\n' "$VERSION" > "$PAQ/VERSION"
cp paquete/GUIA_INSTALACION_MADeCa.html "$PAQ/"
[ -d paquete/img_guia ] && cp -r paquete/img_guia "$PAQ/"
cp "$DESCARGAS/docker-$DOCKER_ESTATICO.tgz" "$PAQ/docker/"
cp "$DESCARGAS/docker-compose-$COMPOSE" "$PAQ/docker/docker-compose"
cp "$DESCARGAS/iptables-1.8.10-nft-estatico.tgz" "$PAQ/docker/"

echo "Guardando la imagen de MADeCa (tarda unos minutos)..."
docker save "$IMAGEN:$VERSION" | gzip -6 > "$PAQ/imagen/$NOMBRE.tar.gz"
echo "Guardando PostgreSQL, Redis y Caddy (instalación sin internet)..."
for b in "${BASES[@]}"; do docker pull -q --platform linux/amd64 "$b" >/dev/null; done
docker save "${BASES[@]}" | gzip -6 > "$PAQ/imagen/base.tar.gz"

(cd "$PAQ" && sha256sum imagen/*.tar.gz docker/* madeca.sh docker-compose.yml Caddyfile Caddyfile.propio Caddyfile.prueba LICENCIA.txt > SHA256SUMS)

# Con Python: permisos fijos (madeca.sh y los binarios ejecutables) aunque se monte en Windows.
NUEVO="$SALIDA/.nuevo"
rm -rf "$NUEVO"; mkdir -p "$NUEVO/windows"
python - "$TRABAJO" "$NOMBRE" "$NUEVO/$NOMBRE.tar.gz" <<'PY'
import os, sys, tarfile
trabajo, nombre, salida = sys.argv[1:]
def filtro(ti):
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = "root"
    ejecutable = ti.isdir() or ti.name.endswith("/madeca.sh") or ti.name.endswith("/docker/docker-compose")
    ti.mode = 0o755 if ejecutable else 0o644
    return ti
with tarfile.open(salida, "w:gz") as tar:
    tar.add(os.path.join(trabajo, nombre), arcname=nombre, filter=filtro)
PY
(cd "$NUEVO" && sha256sum "$NOMBRE.tar.gz" > "$NOMBRE.tar.gz.sha256")
cp paquete/INSTALAR_EN_WINDOWS.bat "$NUEVO/"
cp paquete/windows/madeca_windows.ps1 "$NUEVO/windows/"
cp "$DESCARGAS/$UBUNTU_WSL_FICHERO" "$NUEVO/windows/ubuntu-24.04-wsl.rootfs.tar.gz"
cp paquete/GUIA_INSTALACION_MADeCa.html "$NUEVO/"
[ -d paquete/img_guia ] && cp -r paquete/img_guia "$NUEVO/"
sed "s/{VERSION}/$VERSION/g" paquete/LEEME_INSTALACION.txt > "$NUEVO/LEEME_INSTALACION.txt"

# Sustituye lo entregable de INSTALL (las descargas se conservan para la próxima vez).
find "$SALIDA" -mindepth 1 -maxdepth 1 ! -name .nuevo ! -name .paquete_en_curso -exec rm -rf {} +
mv "$NUEVO"/* "$SALIDA/"
rm -rf "$NUEVO" "$TRABAJO"
ls -la "$SALIDA" "$SALIDA/windows"
cat "$SALIDA/$NOMBRE.tar.gz.sha256"
