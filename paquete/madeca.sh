#!/usr/bin/env bash
# MADeCa — instalación y mantenimiento con Docker en cualquier Linux x86_64
# (Ubuntu, Debian, Rocky/Alma/RHEL, Fedora, openSUSE...), en un servidor
# propio, un VPS, una máquina virtual o Windows (WSL2, con
# INSTALAR_EN_WINDOWS.bat). Funciona también sin internet.
#
#   sudo ./madeca.sh instalar          Instala y deja MADeCa en marcha (pregunta los datos)
#   sudo ./madeca.sh actualizar        Desde el paquete de una versión nueva: copia de seguridad + actualización
#   sudo ./madeca.sh estado            Versión, servicios y empresas
#   sudo ./madeca.sh alta              Alta de una empresa y su administrador (la primera se da al instalar)
#   sudo ./madeca.sh clave CORREO      Nueva contraseña para un usuario (la muestra una vez)
#   sudo ./madeca.sh superadmin CORREO Superadministrador para gestionar varias empresas en /superadmin
#   sudo ./madeca.sh certificado CERT CLAVE   Cambiar el certificado propio (PEM)
#   sudo ./madeca.sh copia             Copia de seguridad completa en /opt/madeca/copias
#   sudo ./madeca.sh restaurar FICHERO Restaura una copia (sustituye los datos actuales)
#   sudo ./madeca.sh parar | arrancar | reiniciar
#   sudo ./madeca.sh registros [servicio]
#   sudo ./madeca.sh desinstalar
#
# Acceso (--acceso):
#   publico   Dominio que llega desde internet: certificado de Let's Encrypt automático (por defecto)
#   propio    Certificado propio (comprado o de la entidad de la empresa): --cert y --clave en PEM
#   proxy     Ya hay un proxy HTTPS (nginx, IIS, Traefik...): MADeCa escucha en HTTP en --puerto-app
#   prueba    Certificado interno de prueba (red local). No vale para DeCA reales
#
# Instalación desatendida (todas las respuestas como opciones):
#   sudo ./madeca.sh instalar --acepto-condiciones --acceso publico \
#       --dominio deca.miempresa.es --correo it@miempresa.es --zona peninsula \
#       --empresa "Transportes X SL" --nif B00345678 --admin admin@miempresa.es
# Otras opciones: --cert FICHERO --clave FICHERO (acceso propio) · --puerto-app N
#   [--ip-app IP --ip-proxy IP] (acceso proxy) · --puerto-http N --puerto-https N (si 80/443
#   están ocupados) · --sin-internet (no descarga nada: todo sale del paquete) · --si
#
# MADeCa es un programa de Masoft.es (https://masoft.es). Software libre
# bajo licencia MIT: ver LICENCIA.txt.
set -euo pipefail

# ------------------------------------------------- edición (MADeCa / Mini)
APP="MADeCa"
DESTINO=/opt/madeca
PROYECTO=madeca
IMAGEN=madeca
COMPROBAR_IMAGEN="from app.version import VERSION; assert VERSION == '%s'"
MULTIEMPRESA=1
# -------------------------------------------------------------------------

PAQUETE="$(cd "$(dirname "$0")" && pwd)"

# En «actualizar» no se muestra la numeración de los pasos de la instalación.
paso()  { local t="$*"; [ "${ACTUALIZANDO:-0}" = "1" ] && t="${t#[0-9]/9 }"; printf '\n\033[1;34m== %s\033[0m\n' "$t"; }
ok()    { printf '\033[1;32m%s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m[AVISO] %s\033[0m\n' "$*"; }
error() { printf '\033[1;31m[ERROR] %s\033[0m\n' "$*" >&2; exit 1; }

# Docker Compose del proyecto (con docker-compose.override.yml si existe: lo
# escribe el acceso «proxy» y la convivencia con otros programas). De su
# salida de errores se quitan las líneas de progreso («Container ... Running»),
# que llenaban la pantalla; se conserva el código de salida de Compose.
PROGRESO='^ ?(Image|Network|Volume|Container) [^ ]+ +(Pulling|Pulled|Creating|Created|Starting|Started|Waiting|Healthy|Running|Recreate|Recreated|Stopping|Stopped|Removing|Removed|Restarting|Exited)[[:space:]]*$|^INFO  \[alembic\.'
dc() {
  local rc=0 ficheros=(-f "$DESTINO/docker-compose.yml")
  [ -f "$DESTINO/docker-compose.override.yml" ] && ficheros+=(-f "$DESTINO/docker-compose.override.yml")
  { docker compose -p "$PROYECTO" --project-directory "$DESTINO" "${ficheros[@]}" "$@" 2>&1 1>&3 3>&- \
      | { grep -v -E "$PROGRESO" || true; } >&2 \
      || rc=$?; } 3>&1
  return "$rc"
}
herramienta() { dc --profile herramientas run --rm -T herramientas "$@"; }
madeca_cli() { herramienta python -c "from app.instalacion import main; main()" "$@"; }

requiere_root() { [ "$(id -u)" -eq 0 ] || error "Ejecútalo como root: sudo ./madeca.sh $*"; }
requiere_instalado() { [ -f "$DESTINO/.env" ] || error "$APP no está instalado en $DESTINO (sudo ./madeca.sh instalar)."; }

valor_env() { grep -m1 "^$1=" "$DESTINO/.env" | cut -d= -f2- || true; }  # vacía si falta (set -e)
poner_env() {  # poner_env CLAVE VALOR (la añade si no está)
  if grep -q "^$1=" "$DESTINO/.env"; then sed -i "s|^$1=.*|$1=$2|" "$DESTINO/.env"; else printf '%s=%s\n' "$1" "$2" >> "$DESTINO/.env"; fi
}

preguntar() {  # preguntar VARIABLE "Texto" [valor por defecto]
  local var="$1" texto="$2" defecto="${3:-}" r
  if [ -n "${!var:-}" ]; then return; fi
  if [ ! -t 0 ]; then
    # Desatendido: si hay valor por defecto se usa (y se dice); si no, falta la opción.
    [ -n "$defecto" ] || error "Falta $texto (opción en modo desatendido)."
    echo "$texto: $defecto (por defecto)"
    printf -v "$var" '%s' "$defecto"; return
  fi
  if [ -n "$defecto" ]; then read -r -p "$texto [$defecto]: " r; r="${r:-$defecto}"
  else read -r -p "$texto: " r; fi
  printf -v "$var" '%s' "$r"
}

# Pide una dirección (opciones de curl: -k, --resolve dominio:puerto:IP). Sin curl (sistemas mínimos sin internet)
# lo hace el Python de la imagen de la aplicación, en la red del equipo.
pedir_url() {
  local url="$1"; shift
  if command -v curl >/dev/null; then curl -fsS --max-time 5 "$@" "$url" >/dev/null 2>&1; return; fi
  local inseguro=0 hosts=() a
  while [ $# -gt 0 ]; do
    case "$1" in -k) inseguro=1 ;; --resolve) a="$2"; hosts+=(--add-host "${a%%:*}:${a##*:}"); shift ;; esac
    shift
  done
  docker run --rm --network host "${hosts[@]}" --entrypoint python "$IMAGEN:$(cat "$DESTINO/VERSION" 2>/dev/null || cat "$PAQUETE/VERSION")" -c "
import ssl, sys, urllib.request
c = ssl._create_unverified_context() if $inseguro else ssl.create_default_context()
urllib.request.urlopen(sys.argv[1], timeout=5, context=c)" "$url" >/dev/null 2>&1
}

esperar_salud() {
  local url="$1" extra=("${@:2}")
  for _ in $(seq 1 60); do
    pedir_url "$url" "${extra[@]}" && return 0
    sleep 3
  done
  return 1
}

hay_internet() {
  [ "${SIN_INTERNET:-0}" = "1" ] && return 1
  command -v curl >/dev/null || return 1
  curl -fsS --max-time 6 -o /dev/null https://registry-1.docker.io/v2/ 2>/dev/null || \
    curl -sS --max-time 6 -o /dev/null -w '%{http_code}' https://registry-1.docker.io/v2/ 2>/dev/null | grep -q '^401$'
}

puerto_ocupado() { ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }

# WSL2 de verdad (no basta con el núcleo de Microsoft: también lo usan los contenedores de Docker Desktop).
es_wsl() { [ -d /run/WSL ] || [ -e /proc/sys/fs/binfmt_misc/WSLInterop ] || [ -n "${WSL_DISTRO_NAME:-}" ]; }

# ---------------------------------------------------------------- instalar

comprobar_sistema() {
  paso "1/9 Comprobación del equipo"
  [ "$(uname -m)" = "x86_64" ] || error "Este paquete es para equipos x86_64 (Intel/AMD). Este es $(uname -m)."
  . /etc/os-release 2>/dev/null || true
  echo "Sistema: ${PRETTY_NAME:-${ID:-Linux}}$(es_wsl && echo ' (WSL2 en Windows)')"
  local ram_mb; ram_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  echo "Memoria: ${ram_mb} MB"
  [ "$ram_mb" -ge 1800 ] || error "Hacen falta al menos 2 GB de memoria (recomendado 4 GB)."
  local libre_gb; libre_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  echo "Disco libre: ${libre_gb} GB"
  [ "$libre_gb" -ge 10 ] || error "Hacen falta al menos 10 GB libres en disco."
  command -v ss >/dev/null || aviso "Sin la orden «ss» no se comprueba si los puertos están libres."
}

# Docker desde los repositorios oficiales (docs.docker.com/engine/install) si
# hay internet; si no, o en distribuciones sin paquete, los binarios estáticos
# que trae el paquete (docker/), que valen para cualquier Linux x86_64.
instalar_docker() {
  paso "2/9 Docker"
  if docker compose version >/dev/null 2>&1; then
    echo "Docker ya está instalado: $(docker --version)"
  elif [ "${SIN_INTERNET:-0}" != "1" ] && instalar_docker_repositorio; then
    echo "Docker instalado: $(docker --version)"
  else
    instalar_docker_estatico
  fi
  docker info >/dev/null 2>&1 || instalar_iptables
  if command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
    systemctl enable --now docker >/dev/null 2>&1 || true
  elif ! docker info >/dev/null 2>&1; then
    # Sin systemd (contenedores, algunos WSL): se arranca a mano.
    (command -v service >/dev/null && service docker start >/dev/null 2>&1) || (nohup dockerd >/var/log/dockerd.log 2>&1 &)
    for _ in $(seq 1 20); do docker info >/dev/null 2>&1 && break; sleep 1; done
  fi
  docker info >/dev/null 2>&1 || error "Docker no responde (systemctl status docker)."
}

instalar_iptables() {
  # Docker necesita iptables para sus redes (también el DNS interno entre servicios). Los sistemas mínimos sin
  # internet no lo traen: el paquete lleva uno estático (backend nf_tables) que vale para cualquier Linux actual.
  command -v iptables >/dev/null && return 0
  local tgz; tgz="$(ls "$PAQUETE"/docker/iptables-*.tgz 2>/dev/null | head -1)"
  [ -n "$tgz" ] || error "Falta iptables y el paquete no lo trae. Instálalo (apt/dnf/zypper install iptables) y repite."
  echo "Instalando iptables desde el paquete ($(basename "$tgz"))..."
  local tmp; tmp="$(mktemp -d)"
  tar xzf "$tgz" -C "$tmp"
  install -D -m 0755 "$tmp/iptables/xtables-nft-multi" /usr/local/sbin/xtables-nft-multi
  rm -rf "$tmp"
  local n
  for n in iptables iptables-save iptables-restore ip6tables ip6tables-save ip6tables-restore; do
    ln -sf /usr/local/sbin/xtables-nft-multi "/usr/local/sbin/$n"
  done
  export PATH="/usr/local/sbin:$PATH"
  /usr/local/sbin/iptables -t nat -L -n >/dev/null 2>&1 || error "El núcleo de este sistema no admite nf_tables: instala iptables de tu distribución y repite."
}

instalar_docker_repositorio() {
  . /etc/os-release 2>/dev/null || return 1
  local base="${ID:-}" familia=" ${ID:-} ${ID_LIKE:-} "
  if command -v apt-get >/dev/null; then
    case "$familia" in *" ubuntu "*) base=ubuntu ;; *" debian "*) base=debian ;; esac
    [ "$base" = ubuntu ] || [ "$base" = debian ] || return 1
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq || return 1
    apt-get install -y -qq ca-certificates curl >/dev/null || return 1
    if command -v docker >/dev/null; then
      apt-get install -y -qq docker-compose-v2 >/dev/null 2>&1 || apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || return 1
      return 0
    fi
    echo "Instalando Docker desde su repositorio oficial (download.docker.com)..."
    local codigo="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$base/gpg" -o /etc/apt/keyrings/docker.asc || return 1
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$base $codigo stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -qq || return 1
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null || return 1
  elif command -v dnf >/dev/null; then
    local repo=centos
    case "$familia" in *" fedora "*) [ "$ID" = fedora ] && repo=fedora ;; esac
    case "$ID" in rhel) repo=rhel ;; esac
    echo "Instalando Docker desde su repositorio oficial (download.docker.com, $repo)..."
    dnf -y -q install dnf-plugins-core >/dev/null 2>&1 || true
    dnf config-manager --add-repo "https://download.docker.com/linux/$repo/docker-ce.repo" >/dev/null 2>&1 \
      || dnf config-manager addrepo --from-repofile="https://download.docker.com/linux/$repo/docker-ce.repo" >/dev/null 2>&1 || return 1
    dnf -y -q install docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null || return 1
  elif command -v zypper >/dev/null; then
    zypper -n -q install docker docker-compose >/dev/null || return 1
    docker compose version >/dev/null 2>&1 || return 1
  else
    return 1
  fi
}

instalar_docker_estatico() {
  local tgz compose
  tgz="$(ls "$PAQUETE"/docker/docker-*.tgz 2>/dev/null | head -1)"
  compose="$PAQUETE/docker/docker-compose"
  [ -n "$tgz" ] && [ -f "$compose" ] || error "No se pudo instalar Docker y el paquete no trae sus binarios (docker/). Instala Docker y Docker Compose v2 y repite."
  echo "Instalando Docker desde el paquete ($(basename "$tgz"), sin internet)..."
  getent group docker >/dev/null || groupadd -r docker 2>/dev/null || true
  tar xzf "$tgz" -C /usr/local/bin --strip-components=1
  install -D -m 0755 "$compose" /usr/local/lib/docker/cli-plugins/docker-compose
  if command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
    cat > /etc/systemd/system/docker.service <<'EOF'
[Unit]
Description=Docker (instalado por el paquete de MADeCa)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/local/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
Restart=always
LimitNOFILE=infinity
TasksMax=infinity
Delegate=yes
KillMode=process

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
  fi
  echo "Docker instalado: $(/usr/local/bin/docker --version)"
}

memoria_intercambio() {
  es_wsl && return 0   # en WSL la memoria la reparte Windows
  local ram_mb; ram_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  if [ "$ram_mb" -lt 3800 ] && ! swapon --show 2>/dev/null | grep -q .; then
    echo "Menos de 4 GB y sin memoria de intercambio: se crea /swapfile de 2 GB."
    { fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; } \
      && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile \
      && { grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab; } \
      || aviso "No se pudo crear la memoria de intercambio (no es imprescindible)."
  fi
}

cortafuegos() {
  local puertos=("$@")
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    for p in "${puertos[@]}"; do ufw allow "$p/tcp" >/dev/null; done
    echo "Cortafuegos (ufw) activo: abiertos ${puertos[*]}."
  elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
    for p in "${puertos[@]}"; do firewall-cmd -q --permanent --add-port="$p/tcp"; done
    firewall-cmd -q --reload
    echo "Cortafuegos (firewalld) activo: abiertos ${puertos[*]}."
  else
    echo "No hay cortafuegos activo en el equipo (ufw/firewalld): no se toca. Comprueba tu cortafuegos o router (${puertos[*]})."
  fi
}

aceptar_condiciones() {
  paso "Condiciones de uso"
  if [ "${ACEPTO:-}" = "1" ]; then echo "Aceptadas (--acepto-condiciones)."; return; fi
  [ -t 0 ] || error "Lee LICENCIA.txt y añade --acepto-condiciones."
  cat "$PAQUETE/LICENCIA.txt"
  echo
  local r; read -r -p "Escribe ACEPTO para aceptar las condiciones y continuar: " r
  [ "$r" = "ACEPTO" ] || error "Instalación cancelada: no se han aceptado las condiciones."
}

datos_instalacion() {
  paso "3/9 Datos de la instalación"
  [ "${PRUEBA_LOCAL:-0}" = "1" ] && ACCESO=prueba
  if [ -z "${ACCESO:-}" ]; then
    [ -t 0 ] || error "Falta --acceso (publico, propio, proxy o prueba)."
    echo "¿Cómo se llegará a $APP?"
    echo "  1) publico  Dominio que llega desde internet (VPS, o servidor propio con el router reenviando 80 y 443): certificado automático"
    echo "  2) propio   Tengo un certificado (comprado o de mi empresa) para el dominio"
    echo "  3) proxy    Ya tengo un proxy HTTPS (nginx, IIS, Traefik...) delante: $APP solo escucha en HTTP"
    echo "  4) prueba   Solo para probar en la red local (certificado interno, no vale para DeCA reales)"
    local r; read -r -p "Elige 1, 2, 3 o 4 [1]: " r
    case "${r:-1}" in 1) ACCESO=publico ;; 2) ACCESO=propio ;; 3) ACCESO=proxy ;; 4) ACCESO=prueba ;; *) error "Opción no válida: $r" ;; esac
  fi
  case "$ACCESO" in publico|propio|proxy|prueba) ;; *) error "--acceso no válido: $ACCESO (publico, propio, proxy o prueba)" ;; esac
  [ "$ACCESO" = "prueba" ] && aviso "MODO PRUEBA: certificado interno, NO vale para DeCA reales (el móvil del inspector no lo aceptaría)."

  preguntar DOMINIO "Dominio de $APP (p. ej. deca.miempresa.es)"
  DOMINIO="$(echo "$DOMINIO" | tr 'A-Z' 'a-z')"
  if [ "$ACCESO" != "prueba" ]; then
    [[ "$DOMINIO" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || error "Dominio no válido: $DOMINIO"
  fi
  if [ "$ACCESO" = "publico" ]; then
    preguntar CORREO "Correo técnico (avisos de Let's Encrypt sobre el certificado)"
    [[ "$CORREO" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || error "Correo no válido: $CORREO"
  fi
  if [ "$ACCESO" = "propio" ]; then
    preguntar CERT "Fichero del certificado (PEM, con la cadena completa)"
    preguntar CLAVE_CERT "Fichero de la clave privada del certificado (PEM, sin contraseña)"
    comprobar_certificado "$CERT" "$CLAVE_CERT"
  fi
  if [ "$ACCESO" = "proxy" ]; then
    preguntar PUERTO_APP "Puerto HTTP en el que escuchará $APP para tu proxy" "8080"
    preguntar IP_APP "IP en la que escuchará (127.0.0.1 si el proxy está en este equipo, 0.0.0.0 si está en otro)" "127.0.0.1"
    [[ "$PUERTO_APP" =~ ^[0-9]+$ ]] || error "Puerto no válido: $PUERTO_APP"
    if [ "$IP_APP" != "127.0.0.1" ]; then
      # Solo se acepta la IP real del usuario (cabecera X-Forwarded-For) si la
      # petición llega del proxy: con «*» cualquiera que alcanzase el puerto
      # podía falsearla y saltarse el límite de intentos de acceso o falsear
      # el registro de lecturas del QR.
      preguntar IP_PROXY "IP del proxy que reenvía las peticiones a $APP (una o varias separadas por comas)"
      [[ "$IP_PROXY" =~ ^[0-9A-Fa-f.:,/]+$ ]] || error "IP del proxy no válida: $IP_PROXY"
    fi
    if puerto_ocupado "$PUERTO_APP"; then error "El puerto $PUERTO_APP ya está en uso. Elige otro con --puerto-app."; fi
  else
    PUERTO_HTTP="${PUERTO_HTTP:-80}"; PUERTO_HTTPS="${PUERTO_HTTPS:-443}"
    for p in "$PUERTO_HTTP" "$PUERTO_HTTPS"; do
      [[ "$p" =~ ^[0-9]+$ ]] || error "Puerto no válido: $p"
      if puerto_ocupado "$p" && ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${PROYECTO}-caddy"; then
        error "El puerto $p ya lo usa otro programa (¿Apache, nginx, IIS?). Libéralo, usa --puerto-http/--puerto-https o el acceso «proxy»."
      fi
    done
    if [ "$ACCESO" = "publico" ] && { [ "$PUERTO_HTTP" != 80 ] || [ "$PUERTO_HTTPS" != 443 ]; }; then
      aviso "Con puertos distintos de 80/443, el router debe reenviar los puertos 80 y 443 de internet a $PUERTO_HTTP y $PUERTO_HTTPS de este equipo (Let's Encrypt los necesita)."
    fi
  fi
  preguntar ZONA "Zona horaria: peninsula (también Baleares, Ceuta y Melilla) o canarias" "peninsula"
  case "$ZONA" in
    peninsula|península) ZONA_TZ="Europe/Madrid" ;;
    canarias) ZONA_TZ="Atlantic/Canary" ;;
    *) error "Zona no válida: $ZONA (peninsula o canarias)" ;;
  esac
}

comprobar_certificado() {
  local cert="$1" clave="$2"
  [ -f "$cert" ] || error "No existe el certificado: $cert"
  [ -f "$clave" ] || error "No existe la clave: $clave"
  grep -q "BEGIN CERTIFICATE" "$cert" || error "$cert no es un certificado PEM (-----BEGIN CERTIFICATE-----)."
  grep -q "PRIVATE KEY" "$clave" || error "$clave no es una clave privada PEM."
  grep -q "ENCRYPTED" "$clave" && error "La clave tiene contraseña: quítasela antes (openssl pkey -in clave.pem -out clave_sin.pem)."
  if command -v openssl >/dev/null; then
    local fin; fin="$(openssl x509 -in "$cert" -noout -enddate 2>/dev/null | cut -d= -f2)"
    [ -n "$fin" ] && echo "Certificado válido hasta: $fin"
    openssl x509 -in "$cert" -noout -checkend 0 >/dev/null 2>&1 || error "El certificado está caducado."
    [ "$(openssl x509 -in "$cert" -noout -pubkey 2>/dev/null | openssl sha256)" = "$(openssl pkey -in "$clave" -pubout 2>/dev/null | openssl sha256)" ] \
      || error "La clave no corresponde al certificado."
  fi
}

comprobar_dns() {
  [ "$ACCESO" = "publico" ] || return 0
  paso "4/9 Comprobación del dominio"
  local ip_dominio ip_servidor
  # Sin «|| true», un dominio que aún no existe cortaba aquí el instalador sin decir nada (set -e + pipefail).
  ip_dominio="$(getent ahostsv4 "$DOMINIO" | awk 'NR==1{print $1}' || true)"
  ip_servidor="$(curl -4 -s --max-time 8 https://api.ipify.org 2>/dev/null || true)"
  echo "$DOMINIO apunta a: ${ip_dominio:-(sin resolver)} · IP pública de este equipo: ${ip_servidor:-(desconocida)}"
  if [ -z "$ip_dominio" ] || { [ -n "$ip_servidor" ] && [ "$ip_dominio" != "$ip_servidor" ]; }; then
    aviso "El dominio aún no apunta a este equipo: sin eso no habrá certificado HTTPS."
    aviso "Si el equipo está detrás de un router, el dominio debe apuntar a la IP pública y el router reenviar 80 y 443 a este equipo."
    if [ "${SI:-0}" != "1" ]; then
      [ -t 0 ] || error "Corrige el DNS o usa --si para continuar igualmente."
      local r; read -r -p "¿Continuar de todos modos? (s/N) " r
      [[ "$r" =~ ^[sS]$ ]] || exit 1
    fi
  fi
}

copiar_ficheros() {
  paso "5/9 Ficheros en $DESTINO"
  mkdir -p "$DESTINO"/{estado,copias,certificado,intercambio/hotfolder,intercambio/nas}
  for f in docker-compose.yml Caddyfile Caddyfile.propio Caddyfile.prueba madeca.sh LICENCIA.txt LEEME.txt VERSION; do
    [ -f "$PAQUETE/$f" ] && { [ "$PAQUETE" = "$DESTINO" ] || cp -f "$PAQUETE/$f" "$DESTINO/$f"; }
  done
  ls "$PAQUETE"/GUIA_*.html >/dev/null 2>&1 && { [ "$PAQUETE" = "$DESTINO" ] || cp -f "$PAQUETE"/GUIA_*.html "$DESTINO/"; }
  chmod 750 "$DESTINO/madeca.sh"
  # Los contenedores de MADeCa trabajan con el usuario 1000.
  chown -R 1000:1000 "$DESTINO/intercambio"
  chmod 700 "$DESTINO/estado" "$DESTINO/copias" "$DESTINO/certificado"
}

instalar_certificado() {  # instalar_certificado CERT CLAVE: Caddy (usuario root del contenedor) los lee de certificado/
  install -m 0644 "$1" "$DESTINO/certificado/certificado.pem"
  install -m 0600 "$2" "$DESTINO/certificado/clave.pem"
}

crear_env() {
  local version="$1"
  if [ -f "$DESTINO/.env" ]; then
    echo ".env ya existe: se conservan las claves."
    poner_env MADECA_VERSION "$version"
    # Instalaciones anteriores a los modos de acceso: eran «publico» o «prueba».
    grep -q '^ACCESO=' "$DESTINO/.env" || poner_env ACCESO "$([ "$(valor_env CADDYFILE)" = Caddyfile.prueba ] && echo prueba || echo publico)"
    grep -q '^COMPOSE_PROFILES=' "$DESTINO/.env" || poner_env COMPOSE_PROFILES proxy
    grep -q '^PUERTO_HTTP=' "$DESTINO/.env" || poner_env PUERTO_HTTP 80
    grep -q '^PUERTO_HTTPS=' "$DESTINO/.env" || poner_env PUERTO_HTTPS 443
    if [ -f "$DESTINO/docker-compose.override.yml" ] && grep -q '"0\.0\.0\.0:' "$DESTINO/docker-compose.override.yml" \
       && ! grep -q 'FORWARDED_ALLOW_IPS' "$DESTINO/docker-compose.override.yml"; then
      aviso "La aplicación escucha en todas las IP y acepta de cualquiera la IP real del usuario. Añade en docker-compose.override.yml, bajo «app:», environment: FORWARDED_ALLOW_IPS: \"<IP de tu proxy>\" y reinicia."
    fi
    return
  fi
  local caddyfile=Caddyfile perfiles=proxy url="https://$DOMINIO"
  case "$ACCESO" in
    propio) caddyfile=Caddyfile.propio ;;
    prueba) caddyfile=Caddyfile.prueba ;;
    proxy) perfiles="" ;;
  esac
  [ "$ACCESO" != "proxy" ] && [ "${PUERTO_HTTPS:-443}" != 443 ] && [ "$ACCESO" != "publico" ] && url="https://$DOMINIO:$PUERTO_HTTPS"
  umask 077
  cat > "$DESTINO/.env" <<EOF
# $APP — configuración generada por madeca.sh. No compartir: contiene claves.
MADECA_VERSION=$version
ACCESO=$ACCESO
DOMINIO=$DOMINIO
CORREO_ACME=${CORREO:-}
CADDYFILE=$caddyfile
# «proxy» arranca el Caddy de $APP; vacío = sin Caddy (acceso «proxy», lo pone tu propio proxy).
COMPOSE_PROFILES=$perfiles
PUERTO_HTTP=${PUERTO_HTTP:-80}
PUERTO_HTTPS=${PUERTO_HTTPS:-443}
APP_BASE_URL=$url
APP_SECRET_KEY=$(head -c 48 /dev/urandom | base64 | tr -d '/+=\n' | head -c 64)
JWT_SECRET_KEY=$(head -c 48 /dev/urandom | base64 | tr -d '/+=\n' | head -c 64)
JWT_EXPIRE_MINUTES=480
POSTGRES_PASSWORD=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
ZONA_HORARIA=$ZONA_TZ
RETENTION_DAYS=365
PUBLIC_URL_DEACTIVATION_DAYS=7
STORAGE_BACKEND=local
FUENTES_PERMITIR_RED_PRIVADA=false
CONECTORES_CICLO_SEGUNDOS=60
OCR_LIMITE_SEGUNDOS=60
OCR_SIMULTANEOS=1
# Avisos diarios por correo a los administradores (opcional):
# SMTP_HOST=smtp.miempresa.es
# SMTP_PUERTO=587
# SMTP_USUARIO=
# SMTP_CLAVE=
# SMTP_REMITENTE=madeca@miempresa.es
EOF
  umask 022
  chmod 600 "$DESTINO/.env"
  echo ".env creado con claves aleatorias."
  if [ "$ACCESO" = "proxy" ]; then
    cat > "$DESTINO/docker-compose.override.yml" <<EOF
# Acceso «proxy» (madeca.sh): sin Caddy; $APP escucha en HTTP para el proxy de la empresa.
services:
  app:
    ports:
      - "${IP_APP:-127.0.0.1}:${PUERTO_APP}:8000"
EOF
    if [ "${IP_APP:-127.0.0.1}" != "127.0.0.1" ]; then
      cat >> "$DESTINO/docker-compose.override.yml" <<EOF
    environment:
      # Solo el proxy puede indicar la IP real del usuario.
      FORWARDED_ALLOW_IPS: "$IP_PROXY"
EOF
    fi
  fi
  [ "$ACCESO" = "propio" ] && instalar_certificado "$CERT" "$CLAVE_CERT"
  return 0
}

# Imágenes: la de MADeCa siempre del paquete; las de base (PostgreSQL, Redis,
# Caddy) del paquete si las trae (instalación sin internet) o de Docker Hub.
cargar_imagen() {
  local version="$1"
  local fichero="$PAQUETE/imagen/$IMAGEN-$version.tar.gz"
  if docker image inspect "$IMAGEN:$version" >/dev/null 2>&1; then
    echo "La imagen $IMAGEN:$version ya está cargada."
  else
    [ -f "$fichero" ] || error "No se encuentra $fichero (¿paquete incompleto?)."
    if [ -f "$PAQUETE/SHA256SUMS" ] && command -v sha256sum >/dev/null; then
      (cd "$PAQUETE" && sha256sum -c --ignore-missing --quiet SHA256SUMS) || error "El paquete está dañado (no coincide SHA256SUMS). Descárgalo de nuevo."
    fi
    echo "Cargando la imagen de $APP (puede tardar unos minutos)..."
    gunzip -c "$fichero" | docker load >/dev/null
    # docker load puede terminar «bien» con capas sin desempaquetar: se comprueba arrancando la imagen.
    # shellcheck disable=SC2059
    docker run --rm --entrypoint python "$IMAGEN:$version" -c "$(printf "$COMPROBAR_IMAGEN" "$version")" \
      || error "La imagen no se ha cargado bien en Docker (mira los mensajes anteriores)."
  fi
  local base="$PAQUETE/imagen/base.tar.gz" faltan=0 img
  for img in postgres:16-alpine redis:7-alpine caddy:2.8.4-alpine; do docker image inspect "$img" >/dev/null 2>&1 || faltan=1; done
  [ "$faltan" = 1 ] || return 0
  if [ -f "$base" ]; then
    echo "Cargando PostgreSQL, Redis y Caddy desde el paquete..."
    gunzip -c "$base" | docker load >/dev/null
  elif hay_internet; then
    dc --profile proxy pull -q db redis caddy
  else
    error "Sin internet y el paquete no trae imagen/base.tar.gz (PostgreSQL, Redis y Caddy)."
  fi
}

arrancar_servicios() {
  paso "7/9 Base de datos y arranque"
  dc up -d db redis
  herramienta alembic upgrade head
  echo "Base de datos preparada."
  dc up -d
  echo "Servicios en marcha."
}

comprobar_https() {
  paso "8/9 Comprobación del acceso"
  local dom acceso puerto
  dom="$(valor_env DOMINIO)"; acceso="$(valor_env ACCESO)"; puerto="$(valor_env PUERTO_HTTPS)"; puerto="${puerto:-443}"
  case "$acceso" in
    proxy)
      local p; p="$(grep -oE '[0-9.]+:[0-9]+:8000' "$DESTINO/docker-compose.override.yml" | head -1)"
      local ip="${p%%:*}" pa; pa="$(echo "$p" | cut -d: -f2)"; [ "$ip" = "0.0.0.0" ] && ip=127.0.0.1
      esperar_salud "http://$ip:$pa/salud" && ok "$APP responde en http://$ip:$pa. Configura tu proxy HTTPS de https://$dom hacia esa dirección." \
        || aviso "Aún no responde. Mira: sudo ./madeca.sh registros" ;;
    publico)
      if esperar_salud "https://$dom/salud"; then ok "$APP responde en https://$dom"
      else aviso "$APP aún no responde por HTTPS. Suele ser el DNS o los puertos 80/443. Mira: sudo ./madeca.sh registros caddy"; fi ;;
    *)
      # -k: el certificado ya se comprobó al instalarlo; si es de una entidad interna, este equipo puede no conocerla.
      local extra=(--resolve "$dom:$puerto:127.0.0.1" -k)
      esperar_salud "https://$dom:$puerto/salud" "${extra[@]}" \
        && ok "$APP responde en https://$dom$([ "$puerto" = 443 ] || echo ":$puerto")$([ "$acceso" = prueba ] && echo ' (certificado de prueba)')" \
        || aviso "Aún no responde. Mira: sudo ./madeca.sh registros caddy" ;;
  esac
}

alta_empresa() {  # alta_empresa [otra]: sin «otra», solo si aún no hay ninguna empresa
  paso "9/9 Empresa y administrador"
  if [ "${1:-}" != "otra" ] && madeca_cli estado 2>/dev/null | grep -q '^Empresa:'; then
    echo "Ya hay una empresa dada de alta:"; madeca_cli estado; return
  fi
  preguntar EMPRESA "Nombre o razón social de la empresa"
  preguntar NIF "NIF/CIF de la empresa"
  preguntar ADMIN "Correo del administrador (será su usuario)"
  umask 077
  if madeca_cli alta --empresa "$EMPRESA" --nif "$NIF" --email "$ADMIN" | tee "$DESTINO/estado/ALTA.txt"; then
    chmod 600 "$DESTINO/estado/ALTA.txt"
    umask 022
    local id; id="$(sed -n 's/^Id de la empresa: //p' "$DESTINO/estado/ALTA.txt")"
    if [ -n "$id" ]; then
      mkdir -p "$DESTINO/intercambio/hotfolder/incoming/$id" "$DESTINO/intercambio/nas/$id"
      chown -R 1000:1000 "$DESTINO/intercambio"
    fi
    echo "(Copia en $DESTINO/estado/ALTA.txt, solo para root: bórrala cuando la hayas guardado.)"
  else
    umask 022
    rm -f "$DESTINO/estado/ALTA.txt"
    aviso "No se dio de alta la empresa. Corrige el dato y repite: sudo ./madeca.sh alta"
  fi
}

direccion_final() {
  local dom acceso puerto
  dom="$(valor_env DOMINIO)"; acceso="$(valor_env ACCESO)"; puerto="$(valor_env PUERTO_HTTPS)"
  if [ "$acceso" = "publico" ] || [ "$acceso" = "proxy" ] || [ "${puerto:-443}" = 443 ]; then echo "https://$dom"; else echo "https://$dom:$puerto"; fi
}

instalar() {
  requiere_root instalar
  local version; version="$(cat "$PAQUETE/VERSION")"
  echo "$APP $version — Masoft.es."
  # Datos de una instalación anterior más nueva que este paquete: su base de datos puede no valer con esta versión.
  if [ -f "$DESTINO/VERSION" ]; then
    local previa; previa="$(cat "$DESTINO/VERSION")"
    if [ "$previa" != "$version" ] && [ "$(printf '%s\n%s\n' "$previa" "$version" | sort -V | tail -1)" = "$previa" ]; then
      error "En este equipo hubo $APP $previa, más nueva que este paquete ($version). Instala con el paquete $previa o posterior."
    fi
  fi
  aceptar_condiciones
  comprobar_sistema
  instalar_docker
  datos_instalacion
  comprobar_dns
  copiar_ficheros
  paso "6/9 Configuración, memoria y cortafuegos"
  crear_env "$version"
  memoria_intercambio
  if [ "$ACCESO" = "proxy" ]; then
    [ "${IP_APP:-127.0.0.1}" = "127.0.0.1" ] || cortafuegos "$PUERTO_APP"
  else
    cortafuegos "${PUERTO_HTTP:-80}" "${PUERTO_HTTPS:-443}"
  fi
  cargar_imagen "$version"
  arrancar_servicios
  comprobar_https
  alta_empresa
  echo
  ok "Instalación terminada: $(direccion_final)"
  if { [ "$ACCESO" = "prueba" ] || [ "$ACCESO" = "propio" ]; } && [ "$DOMINIO" != "localhost" ]; then
    echo "En los equipos de la red, $DOMINIO debe apuntar a la IP de este equipo (DNS interno o fichero hosts)."
  fi
  echo "Siguientes pasos (ver la guía): entrar con el administrador, completar los datos de la empresa,"
  echo "subir el certificado de firma si se usa, crear los usuarios y configurar las fuentes de datos."
  echo "Desde ahora usa: sudo $DESTINO/madeca.sh <orden>"
}

certificado() {
  requiere_root certificado; requiere_instalado
  [ "$(valor_env ACCESO)" = "propio" ] || error "Esta instalación no usa certificado propio (acceso $(valor_env ACCESO))."
  local cert="${1:-}" clave="${2:-}"
  [ -n "$cert" ] && [ -n "$clave" ] || error "Uso: sudo ./madeca.sh certificado certificado.pem clave.pem"
  comprobar_certificado "$cert" "$clave"
  instalar_certificado "$cert" "$clave"
  dc restart caddy
  ok "Certificado cambiado."
}

# --------------------------------------------------------------- copias

copia() {
  requiere_root copia; requiere_instalado
  local marca tmp destino
  marca="$(date +%Y%m%d-%H%M%S)"
  tmp="$(mktemp -d)"; chmod 777 "$tmp"
  destino="$DESTINO/copias/$PROYECTO-$marca.tar.gz"
  paso "Copia de seguridad"
  dc exec -T db pg_dump -U madeca -Fc madeca > "$tmp/bd.dump"
  dc --profile herramientas run --rm -T --no-deps -v "$tmp:/salida" herramientas \
    tar czf /salida/ficheros.tar.gz -C /app storage certs
  cp "$DESTINO/.env" "$tmp/env"
  cat "$DESTINO/VERSION" > "$tmp/VERSION" 2>/dev/null || true
  [ -f "$DESTINO/certificado/certificado.pem" ] && tar czf "$tmp/certificado.tar.gz" -C "$DESTINO" certificado
  umask 077
  local partes=(bd.dump ficheros.tar.gz env VERSION)
  [ -f "$tmp/certificado.tar.gz" ] && partes+=(certificado.tar.gz)
  tar czf "$destino" -C "$tmp" "${partes[@]}"
  umask 022
  rm -rf "$tmp"
  chmod 600 "$destino"
  ok "Copia creada: $destino ($(du -h "$destino" | cut -f1))"
  echo "Contiene las claves (.env): guárdala fuera del equipo y en lugar seguro."
}

restaurar() {
  requiere_root restaurar; requiere_instalado
  local fichero="${1:-}"
  [ -f "$fichero" ] || error "Indica la copia: sudo ./madeca.sh restaurar $DESTINO/copias/$PROYECTO-....tar.gz"
  aviso "Se SUSTITUIRÁN todos los datos actuales por los de la copia."
  local r; read -r -p "Escribe RESTAURAR para continuar: " r
  [ "$r" = "RESTAURAR" ] || error "Cancelado."
  local tmp; tmp="$(mktemp -d)"; chmod 777 "$tmp"
  tar xzf "$fichero" -C "$tmp"
  [ -f "$tmp/bd.dump" ] && [ -f "$tmp/ficheros.tar.gz" ] || error "La copia no es de $APP."
  chmod 644 "$tmp/ficheros.tar.gz"
  dc stop app hotfolder-watcher conectores
  dc stop caddy 2>/dev/null || true
  # Las claves de la copia son las que descifran sus certificados de firma.
  cp "$tmp/env" "$DESTINO/.env"; chmod 600 "$DESTINO/.env"
  poner_env MADECA_VERSION "$(cat "$DESTINO/VERSION")"
  [ -f "$tmp/certificado.tar.gz" ] && tar xzf "$tmp/certificado.tar.gz" -C "$DESTINO"
  dc up -d db
  sleep 3
  dc exec -T db pg_restore -U madeca -d madeca --clean --if-exists --no-owner --disable-triggers < "$tmp/bd.dump"
  dc --profile herramientas run --rm -T --no-deps -v "$tmp:/entrada:ro" herramientas \
    sh -c 'rm -rf /app/storage/* /app/certs/* && tar xzf /entrada/ficheros.tar.gz -C /app'
  rm -rf "$tmp"
  herramienta alembic upgrade head
  dc up -d
  ok "Restaurada. Comprueba: sudo ./madeca.sh estado"
}

# ------------------------------------------------------------- actualizar

actualizar() {
  requiere_root actualizar; requiere_instalado
  local version; version="$(cat "$PAQUETE/VERSION")"
  [ "$PAQUETE" != "$DESTINO" ] || error "Lanza 'actualizar' desde la carpeta del paquete NUEVO, no desde $DESTINO."
  ACTUALIZANDO=1
  local actual; actual="$(cat "$DESTINO/VERSION")"
  [ "$actual" != "$version" ] || error "$APP $version ya está instalado."
  [ "$(printf '%s\n%s\n' "$actual" "$version" | sort -V | tail -1)" = "$version" ] \
    || error "Este paquete ($version) es más antiguo que lo instalado ($actual): no se puede volver atrás."
  echo "Actualizar $APP de $actual a $version."
  aceptar_condiciones
  copia
  copiar_ficheros
  crear_env "$version"
  cargar_imagen "$version"
  paso "Base de datos y servicios"
  herramienta alembic upgrade head
  echo "Base de datos al día."
  dc up -d --remove-orphans
  echo "Servicios en marcha."
  comprobar_https
  ok "Actualizado a $version."
}

# ------------------------------------------------------------------ resto

estado() {
  requiere_root estado; requiere_instalado
  dc ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
  echo
  madeca_cli estado
  echo "Acceso: $(valor_env ACCESO) · Dirección: $(direccion_final)"
}

desinstalar() {
  requiere_root desinstalar; requiere_instalado
  aviso "Se pararán y quitarán los servicios de $APP."
  local r; read -r -p "¿Continuar? (s/N) " r
  [[ "$r" =~ ^[sS]$ ]] || exit 1
  read -r -p "¿Hacer antes una copia de seguridad? (S/n) " r
  [[ "$r" =~ ^[nN]$ ]] || copia
  echo "Los datos (DeCA, certificados, base de datos) se CONSERVAN salvo que lo pidas."
  echo "Recuerda: los DeCA deben conservarse al menos un año (Apartado Segundo.4 BOE-A-2026-12784)."
  read -r -p "Escribe BORRAR DATOS para borrarlos también, o pulsa Intro para conservarlos: " r
  if [ "$r" = "BORRAR DATOS" ]; then
    dc --profile proxy --profile herramientas down -v
    ok "Servicios y datos borrados. Quedan $DESTINO/copias y $DESTINO/.env."
  else
    dc --profile proxy --profile herramientas down
    ok "Servicios quitados; datos conservados (volúmenes de Docker ${PROYECTO}_*)."
  fi
}

uso() { sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; }

# ---------------------------------------------------------------- opciones

ORDEN="${1:-}"; shift || true
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dominio|--correo|--zona|--empresa|--nif|--admin|--acceso|--cert|--clave|--puerto-app|--ip-app|--ip-proxy|--puerto-http|--puerto-https)
      [ $# -ge 2 ] && [ -n "$2" ] || error "Falta el valor de $1." ;;
  esac
  case "$1" in
    --acepto-condiciones) ACEPTO=1 ;;
    --prueba-local) PRUEBA_LOCAL=1 ;;
    --sin-internet) SIN_INTERNET=1 ;;
    --si) SI=1 ;;
    --dominio) DOMINIO="$2"; shift ;;
    --correo) CORREO="$2"; shift ;;
    --zona) ZONA="$2"; shift ;;
    --empresa) EMPRESA="$2"; shift ;;
    --nif) NIF="$2"; shift ;;
    --admin) ADMIN="$2"; shift ;;
    --acceso) ACCESO="$2"; shift ;;
    --cert) CERT="$2"; shift ;;
    --clave) CLAVE_CERT="$2"; shift ;;
    --puerto-app) PUERTO_APP="$2"; shift ;;
    --ip-app) IP_APP="$2"; shift ;;
    --ip-proxy) IP_PROXY="$2"; shift ;;
    --puerto-http) PUERTO_HTTP="$2"; shift ;;
    --puerto-https) PUERTO_HTTPS="$2"; shift ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done

case "$ORDEN" in
  instalar) instalar ;;
  actualizar) actualizar ;;
  estado) estado ;;
  alta) requiere_root alta; requiere_instalado; alta_empresa otra ;;
  clave) requiere_root clave; requiere_instalado; [ -n "${ARGS[0]:-}" ] || error "Uso: sudo ./madeca.sh clave CORREO"; madeca_cli clave --email "${ARGS[0]}" ;;
  superadmin)
    [ "$MULTIEMPRESA" = 1 ] || error "$APP es de una sola empresa: no tiene superadministrador."
    requiere_root superadmin; requiere_instalado; [ -n "${ARGS[0]:-}" ] || error "Uso: sudo ./madeca.sh superadmin CORREO"; madeca_cli superadmin --email "${ARGS[0]}" ;;
  certificado) certificado "${ARGS[0]:-}" "${ARGS[1]:-}" ;;
  copia) copia ;;
  restaurar) restaurar "${ARGS[0]:-}" ;;
  parar) requiere_root parar; requiere_instalado; dc stop ;;
  arrancar) requiere_root arrancar; requiere_instalado; dc up -d ;;
  reiniciar) requiere_root reiniciar; requiere_instalado; dc restart ;;
  registros) requiere_root registros; requiere_instalado; dc logs --tail 200 "${ARGS[@]}" ;;
  desinstalar) desinstalar ;;
  *) uso; exit 1 ;;
esac
