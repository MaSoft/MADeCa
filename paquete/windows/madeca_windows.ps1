<#
.SYNOPSIS
    Instala MADeCa en Windows 10 (2004 o posterior) / 11 y Windows Server 2022/2025 de 64 bits, sobre WSL2 con una
    Ubuntu propia («MADeCa») y Docker dentro: sin Docker Desktop.

.DESCRIPTION
    1. Activa WSL2 si falta (pide reiniciar y volver a lanzar).
    2. Crea la distribución «MADeCa» desde la Ubuntu que trae el paquete (no necesita la Microsoft Store ni internet).
    3. Copia el paquete dentro y lanza madeca.sh instalar (las mismas preguntas y opciones que en Linux).
    4. Publica los puertos en la red (reenvío de puertos de Windows + regla del cortafuegos) y deja una tarea programada
       que arranca MADeCa con el equipo, aunque nadie inicie sesión.

    Uso (INSTALAR_EN_WINDOWS.bat pasa sus argumentos en MADECA_ARGS y lo lanza como administrador):
        INSTALAR_EN_WINDOWS.bat                            instalar (preguntas como en Linux)
        INSTALAR_EN_WINDOWS.bat instalar --acceso prueba --dominio madeca.local ...   (opciones de madeca.sh)
        INSTALAR_EN_WINDOWS.bat estado | copia | parar | arrancar | registros | clave CORREO | ...
        INSTALAR_EN_WINDOWS.bat desinstalar                quitar tarea, puertos y cortafuegos (y, si se pide, los datos)

    MADeCa es un programa de Masoft.es (https://masoft.es).
#>
[CmdletBinding()]
param(
    [string]$Distro = "MADeCa",
    [string]$Carpeta = "C:\MADeCa",
    [switch]$Desinstalar,
    [switch]$Arranque   # lo usa la tarea programada al encender el equipo
)

$ErrorActionPreference = "Stop"
# Argumentos de INSTALAR_EN_WINDOWS.bat: orden y opciones de madeca.sh (respetando "entre comillas").
$partes = @([regex]::Matches("$env:MADECA_ARGS", '"([^"]*)"|(\S+)') | ForEach-Object { if ($_.Groups[1].Success) { $_.Groups[1].Value } else { $_.Groups[2].Value } })
$Orden = if ($partes.Count -gt 0) { $partes[0].ToLower() } else { "instalar" }
$Opciones = if ($partes.Count -gt 1) { [string[]]$partes[1..($partes.Count - 1)] } else { [string[]]@() }
if ($Orden -eq "desinstalar") { $Desinstalar = [switch]$true }
$env:WSL_UTF8 = "1"          # salida de wsl.exe en UTF-8 (WSL 2.x)
$Paquete = Split-Path -Parent $PSScriptRoot   # carpeta INSTALL
$Tarea = "$Distro - arranque"
$ReglaCortafuegos = "$Distro (WSL2)"
$Script = Join-Path $Carpeta "madeca_windows.ps1"

function Paso($t)  { Write-Host "`n== $t" -ForegroundColor Cyan }
function Ok($t)    { Write-Host $t -ForegroundColor Green }
function Aviso($t) { Write-Host "[AVISO] $t" -ForegroundColor Yellow }
function Fallo($t) { Write-Host "[ERROR] $t" -ForegroundColor Red; exit 1 }

function Wsl([string[]]$a) {
    & wsl.exe @a
    return $LASTEXITCODE
}
function WslRoot([string]$orden) {
    & wsl.exe -d $Distro -u root -- bash -lc $orden
    if ($LASTEXITCODE -ne 0) { throw "Falló en WSL: $orden" }
}
function DistroExiste {
    $l = (& wsl.exe -l -q 2>$null) -replace "`0", "" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    return [bool]($l -contains $Distro)
}
function IpDistro {
    $ip = (& wsl.exe -d $Distro -u root -- hostname -I 2>$null) -replace "`0", ""
    return ($ip -split "\s+" | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1)
}
function PuertosInstalacion {
    # Los puertos que publica MADeCa según su .env (80/443, los elegidos, o el de la aplicación con acceso «proxy»).
    $envtxt = (& wsl.exe -d $Distro -u root -- cat /opt/madeca/.env 2>$null) -join "`n"
    if (-not $envtxt) { return @() }
    $acceso = [regex]::Match($envtxt, '(?m)^ACCESO=(.*)$').Groups[1].Value.Trim()
    if ($acceso -eq "proxy") {
        $ov = (& wsl.exe -d $Distro -u root -- cat /opt/madeca/docker-compose.override.yml 2>$null) -join "`n"
        $m = [regex]::Match($ov, '"([0-9.]+):(\d+):8000"')
        if ($m.Success -and $m.Groups[1].Value -ne "127.0.0.1") { return @([int]$m.Groups[2].Value) }
        return @()
    }
    $h = [regex]::Match($envtxt, '(?m)^PUERTO_HTTP=(\d+)').Groups[1].Value
    $s = [regex]::Match($envtxt, '(?m)^PUERTO_HTTPS=(\d+)').Groups[1].Value
    return @([int]($(if ($h) { $h } else { 80 })), [int]($(if ($s) { $s } else { 443 })))
}
function PublicarPuertos {
    $ip = IpDistro
    if (-not $ip) { Aviso "No se pudo saber la IP de la distribución $Distro."; return }
    $puertos = PuertosInstalacion
    foreach ($p in $puertos) {
        & netsh.exe interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 2>$null | Out-Null
        & netsh.exe interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$ip | Out-Null
    }
    if ($puertos.Count -gt 0) {
        Get-NetFirewallRule -DisplayName $ReglaCortafuegos -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName $ReglaCortafuegos -Direction Inbound -Protocol TCP -LocalPort $puertos -Action Allow -Profile Any | Out-Null
        Write-Host "Puertos $($puertos -join ', ') de este equipo -> $Distro ($ip). Cortafuegos de Windows: abiertos."
    }
}

# ------------------------------------------------------------- arranque
if ($Arranque) {
    # Tarea programada: arranca la distribución (systemd levanta Docker y Docker los servicios), publica los puertos
    # con la IP de este arranque y la mantiene encendida (WSL apaga las distribuciones sin procesos).
    for ($i = 0; $i -lt 30; $i++) {
        & wsl.exe -d $Distro -u root -- systemctl is-active --quiet docker 2>$null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 2
    }
    PublicarPuertos
    & wsl.exe -d $Distro -u root -- sleep infinity
    exit 0
}

# ---------------------------------------------------- comprobaciones
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fallo "Ejecútalo como administrador (INSTALAR_EN_WINDOWS.bat lo pide solo)." }

if ($Desinstalar) {
    Paso "Quitar MADeCa de Windows"
    # La tarea en marcha mantiene la distribución encendida (sleep infinity): se para antes de quitarla.
    Stop-ScheduledTask -TaskName $Tarea -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Tarea -Confirm:$false -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match '-Arranque' -and $_.CommandLine -match [regex]::Escape("-Distro $Distro") } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if (DistroExiste) {
        foreach ($p in (PuertosInstalacion)) { & netsh.exe interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 2>$null | Out-Null }
    }
    Get-NetFirewallRule -DisplayName $ReglaCortafuegos -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Ok "Tarea de arranque, puertos y regla del cortafuegos quitados."
    if (DistroExiste) {
        Write-Host "La distribución $Distro guarda TODOS los datos (DeCA, certificados, base de datos)."
        Write-Host "Los DeCA deben conservarse al menos un año (Apartado Segundo.4 BOE-A-2026-12784): haz antes una copia."
        $r = Read-Host "Escribe BORRAR DATOS para borrar también la distribución, o pulsa Intro para conservarla"
        if ($r -eq "BORRAR DATOS") { & wsl.exe --unregister $Distro | Out-Null; Ok "Distribución $Distro borrada." }
        else { & wsl.exe --terminate $Distro | Out-Null; Ok "Distribución $Distro conservada y apagada (para volver: INSTALAR_EN_WINDOWS.bat)." }
    }
    exit 0
}

if ($Orden -ne "instalar") {
    if (-not (DistroExiste)) { Fallo "MADeCa no está instalado en este equipo (falta la distribución $Distro)." }
    & wsl.exe -d $Distro -u root -- /opt/madeca/madeca.sh $Orden @Opciones
    exit $LASTEXITCODE
}

Paso "1/5 Comprobación de Windows"
$so = Get-CimInstance Win32_OperatingSystem
Write-Host "$($so.Caption) (compilación $($so.BuildNumber))"
if (-not [Environment]::Is64BitOperatingSystem) { Fallo "Hace falta Windows de 64 bits." }
if ([int]$so.BuildNumber -lt 19041) { Fallo "Hace falta Windows 10 versión 2004 (compilación 19041) o posterior, Windows 11 o Windows Server 2022/2025." }
$memGB = [math]::Round($so.TotalVisibleMemorySize / 1MB, 1)
Write-Host "Memoria: $memGB GB"
if ($memGB -lt 4) { Aviso "Con menos de 4 GB irá justo: WSL usa como máximo la mitad de la memoria del equipo." }

Paso "2/5 WSL2"
& wsl.exe --status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Activando WSL2 (Subsistema de Windows para Linux)..."
    & wsl.exe --install --no-distribution
    if ($LASTEXITCODE -ne 0) {
        Fallo "No se pudo activar WSL2. Comprueba que la virtualización está activada en la BIOS/UEFI (o, en una máquina virtual, la virtualización anidada) y repite."
    }
    Aviso "WSL2 activado. REINICIA el equipo y vuelve a ejecutar INSTALAR_EN_WINDOWS.bat para continuar."
    exit 3
}
& wsl.exe --set-default-version 2 *> $null
Write-Host ((& wsl.exe --version 2>$null | Select-Object -First 1) -replace "`0", "")

Paso "3/5 Distribución $Distro"
$tgz = Get-ChildItem -Path $Paquete -Filter "madeca-*.tar.gz" | Sort-Object Name -Descending | Select-Object -First 1
if (-not $tgz) { Fallo "No encuentro madeca-<versión>.tar.gz en $Paquete." }
if (-not (DistroExiste)) {
    $rootfs = Get-ChildItem -Path $PSScriptRoot -Filter "ubuntu-*.rootfs.tar.gz" | Select-Object -First 1
    if (-not $rootfs) { Fallo "Falta la Ubuntu para WSL (windows\ubuntu-*.rootfs.tar.gz) en el paquete." }
    New-Item -ItemType Directory -Force -Path (Join-Path $Carpeta "wsl") | Out-Null
    Write-Host "Creando $Distro en $Carpeta\wsl desde $($rootfs.Name) (un par de minutos)..."
    & wsl.exe --import $Distro (Join-Path $Carpeta "wsl") $rootfs.FullName --version 2
    if ($LASTEXITCODE -ne 0) { Fallo "No se pudo crear la distribución $Distro." }
    # systemd para que Docker y MADeCa arranquen solos; sin la ruta de Windows para no mezclar programas.
    WslRoot "printf '[boot]\nsystemd=true\n\n[user]\ndefault=root\n\n[interop]\nappendWindowsPath=false\n\n[network]\nhostname=$($Distro.ToLower())\n' > /etc/wsl.conf"
    & wsl.exe --terminate $Distro | Out-Null
    Ok "Distribución $Distro creada."
} else {
    Write-Host "La distribución $Distro ya existe: se usa la misma (los datos se conservan)."
}
$ruta = ((& wsl.exe -d $Distro -u root -- wslpath -a ($tgz.FullName -replace '\\', '/')) -replace "`0", "").Trim()
WslRoot "rm -rf /root/paquete && mkdir -p /root/paquete && tar xzf '$ruta' -C /root/paquete"
$carpetaPaquete = ((& wsl.exe -d $Distro -u root -- bash -lc "ls -d /root/paquete/madeca-*/ | head -1") -replace "`0", "").Trim()

Paso "4/5 Instalación de MADeCa (dentro de $Distro)"
# Puertos ocupados en Windows (IIS, otro servidor web): madeca.sh solo ve los de dentro de WSL.
$todas = $Opciones -join " "
if ($todas -notmatch "--acceso\s+proxy") {
    $ph = if ($todas -match "--puerto-http\s+(\d+)") { [int]$Matches[1] } else { 80 }
    $ps = if ($todas -match "--puerto-https\s+(\d+)") { [int]$Matches[1] } else { 443 }
    foreach ($p in @($ph, $ps)) {
        $uso = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -ne 0 }
        if ($uso) {
            $proc = (Get-Process -Id $uso[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
            Fallo "El puerto $p de Windows lo usa «$proc». Libéralo o usa -Opciones '--puerto-http','N','--puerto-https','N' (o el acceso «proxy»)."
        }
    }
}
$orden = $carpetaPaquete + "madeca.sh"
& wsl.exe -d $Distro -u root -- bash $orden instalar @Opciones
if ($LASTEXITCODE -ne 0) { Fallo "La instalación dentro de $Distro no terminó. Corrige lo indicado y vuelve a ejecutar INSTALAR_EN_WINDOWS.bat." }

Paso "5/5 Arranque con el equipo y acceso desde la red"
New-Item -ItemType Directory -Force -Path $Carpeta | Out-Null
Copy-Item -Force $PSCommandPath $Script
$usuario = "$env:USERDOMAIN\$env:USERNAME"
$accion = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`" -Arranque -Distro $Distro"
$disparador = New-ScheduledTaskTrigger -AtStartup
# S4U: se ejecuta aunque nadie inicie sesión y sin guardar la contraseña. Las distribuciones de WSL son de cada usuario.
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType S4U -RunLevel Highest
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Tarea -Action $accion -Trigger $disparador -Principal $principal -Settings $ajustes -Force | Out-Null
Start-ScheduledTask -TaskName $Tarea
Start-Sleep -Seconds 5
PublicarPuertos
Ok "Tarea «$Tarea» creada: MADeCa arranca solo al encender el equipo (usuario $usuario)."
Write-Host ""
Write-Host "Desde ahora (como administrador):"
Write-Host "   INSTALAR_EN_WINDOWS.bat estado | copia | parar | arrancar | registros | desinstalar"
Write-Host "Las copias quedan dentro de $Distro en /opt/madeca/copias; para sacarlas a Windows: \\wsl.localhost\$Distro\opt\madeca\copias"
