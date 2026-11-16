@echo off
chcp 65001 >nul
setlocal
title MADeCa - instalacion en Windows (WSL2)
rem Instala o gestiona MADeCa en Windows 10/11 o Windows Server sobre WSL2, sin Docker Desktop.
rem   INSTALAR_EN_WINDOWS.bat                    instalar
rem   INSTALAR_EN_WINDOWS.bat estado ^| copia ^| parar ^| arrancar ^| registros ^| desinstalar
rem   INSTALAR_EN_WINDOWS.bat instalar --acceso prueba --dominio madeca.local   (opciones de madeca.sh)
rem Despliegue desatendido: todas las opciones de madeca.sh, incluida --acepto-condiciones.
net session >nul 2>&1
if errorlevel 1 (
  echo Pidiendo permisos de administrador...
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%*'"
  exit /b
)
set "MADECA_ARGS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\madeca_windows.ps1"
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
