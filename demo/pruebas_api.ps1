<#
  Pruebas de la API de MADeCa con los documentos de demostración.
  Requiere PowerShell 7 (pwsh) y la clave de API de la demo:
      $env:MADECA_API_KEY = "mad_live_..."      (la muestra scripts/cargar_demo.py)
  Uso:
      pwsh demo/pruebas_api.ps1                       # contra http://localhost:8000
      pwsh demo/pruebas_api.ps1 -Url https://deca.tudominio.es
  Cada paso muestra lo que devuelve MADeCa. No borra nada.
#>
param([string]$Url = "http://localhost:8000")

$ErrorActionPreference = "Stop"
if (-not $env:MADECA_API_KEY) { throw "Define antes `$env:MADECA_API_KEY (scripts/cargar_demo.py la muestra)." }
$cab = @{ "X-API-Key" = $env:MADECA_API_KEY }
$docs = Join-Path $PSScriptRoot "documentos"
$sufijo = Get-Date -Format "HHmmss"   # referencias únicas en cada ejecución

function Paso($titulo) { Write-Host "`n=== $titulo ===" -ForegroundColor Cyan }

Paso "1. Salud del servicio"
Invoke-RestMethod "$Url/salud"

Paso "2. Camino 1: PDF propio del ERP (se le estampa el QR)"
$pdf = Invoke-RestMethod "$Url/api/v1/expediciones" -Method Post -Headers $cab -Form @{
    referencia_externa = "ALB-DEMO-001-$sufijo"
    matricula_vehiculo = "1234BCD"
    matricula_remolque = "R5678BBC"
    fecha_transporte   = (Get-Date).AddDays(1).ToString("yyyy-MM-ddT08:00:00")
    archivo            = Get-Item (Join-Path $docs "01_albaran_erp.pdf")
}
$pdf | Format-List
Write-Host "Abre el QR/URL pública en el navegador: $($pdf.url_publica)" -ForegroundColor Green

Paso "3. Camino 1 con un escaneo (debe RECHAZARSE: un escaneo no vale como DeCA)"
try {
    Invoke-RestMethod "$Url/api/v1/expediciones" -Method Post -Headers $cab -Form @{
        referencia_externa = "ALB-DEMO-002-$sufijo"; matricula_vehiculo = "5678CDF"
        fecha_transporte = (Get-Date).AddDays(1).ToString("yyyy-MM-ddT08:00:00")
        archivo = Get-Item (Join-Path $docs "02_albaran_escaneado.pdf")
    } | Out-Null
    Write-Host "ATENCIÓN: se ha aceptado un escaneo" -ForegroundColor Red
} catch { Write-Host "Rechazado como se esperaba: $($_.ErrorDetails.Message)" -ForegroundColor Green }

Paso "4. Camino 2: datos completos (MADeCa redacta el PDF; en Windows sin GTK falla: usar Docker)"
$datos = Get-Content (Join-Path $docs "05_expedicion_datos.json") -Raw -Encoding utf8 | ConvertFrom-Json
$datos.referencia_externa = "ALB-DEMO-API-$sufijo"
try {
    $gen = Invoke-RestMethod "$Url/api/v1/expediciones/datos" -Method Post -Headers $cab -ContentType "application/json; charset=utf-8" `
        -Body ([Text.Encoding]::UTF8.GetBytes(($datos | ConvertTo-Json -Depth 5)))
    $gen | Format-List
} catch { Write-Host "Camino 2 no disponible aquí: $($_.ErrorDetails.Message)" -ForegroundColor Yellow }

Paso "5. Modificación durante el servicio (misma URL y QR)"
$mod = Get-Content (Join-Path $docs "06_modificacion.json") -Raw -Encoding utf8
Invoke-RestMethod "$Url/api/v1/expediciones/$($pdf.id)/modificaciones" -Method Post -Headers $cab `
    -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($mod)) | Format-List

Paso "6. Copia para el conductor: PDF vigente y QR"
Invoke-WebRequest "$Url/api/v1/expediciones/$($pdf.id)/pdf" -Headers $cab -OutFile (Join-Path $env:TEMP "DeCA_demo.pdf") | Out-Null
Invoke-WebRequest "$Url/api/v1/expediciones/$($pdf.id)/qr.png" -Headers $cab -OutFile (Join-Path $env:TEMP "QR_demo.png") | Out-Null
Write-Host "Guardados en $env:TEMP\DeCA_demo.pdf y QR_demo.png (el PDF tiene la página de modificación)" -ForegroundColor Green

Paso "7. Bandeja: subir un escaneo para que MADeCa lea los datos (OCR)"
Invoke-RestMethod "$Url/api/v1/borradores/documento" -Method Post -Headers $cab -Form @{
    archivo = Get-Item (Join-Path $docs "02_albaran_escaneado.png")
} | Format-List

Paso "8. Bandeja: lo que enviaría el conector local (solo datos)"
$borrador = @{ referencia_fuente = "demo-conector-$sufijo"; origen = "conector_local"; nombre_documento = "alb_demo.pdf"
               datos = $datos; avisos = @("Enviado por pruebas_api.ps1") } | ConvertTo-Json -Depth 6
Invoke-RestMethod "$Url/api/v1/borradores" -Method Post -Headers $cab -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($borrador)) | Format-List

Paso "9. Marcar como entregado (empieza la cuenta de 7 días de la URL pública)"
Invoke-RestMethod "$Url/api/v1/expediciones/$($pdf.id)/finalizar" -Method Patch -Headers $cab | Format-List

Write-Host "`nListo. Revisa en el panel: Expediciones (detalle, modificación) y Bandeja de revisión (2 borradores)." -ForegroundColor Cyan
