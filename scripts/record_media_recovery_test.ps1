[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Provider,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 1000000000)]
    [int]$RecoveredAssetCount,

    [Parameter(Mandatory = $true)]
    [ValidateLength(3, 500)]
    [string]$Evidence,

    [Parameter(Mandatory = $true)]
    [switch]$Confirmed
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\foodback_backup_common.ps1")

if (-not $Confirmed) {
    throw "Debes usar -Confirmed únicamente después de realizar una recuperación real de media y verificar los archivos restaurados."
}

$projectRoot = Get-FoodBackProjectRoot
Import-FoodBackEnvFile -Path (Join-Path $projectRoot ".env")
$statusDir = Get-FoodBackStatusDirectory -ProjectRoot $projectRoot
$statusPath = Join-Path $statusDir "last_media_recovery_test.json"

# Este registro es una evidencia operacional, no ejecuta ni simula una restauración.
# `Evidence` debe ser una referencia NO secreta (ticket interno, nota, ruta de informe,
# etc.). Nunca incluir API keys, URLs firmadas, passwords o tokens.
$status = [ordered]@{
    status = "ok"
    tested_at_utc = [DateTime]::UtcNow.ToString("o")
    environment = (Get-FoodBackEnvValue -Name "FOODBACK_ENVIRONMENT" -Default "development")
    provider = $Provider.Trim()
    recovered_asset_count = $RecoveredAssetCount
    evidence = $Evidence.Trim()
    confirmed_real_recovery = $true
}

Write-FoodBackJsonAtomic `
    -Path $statusPath `
    -Data $status

Write-Host ""
Write-Host "MEDIA RECOVERY TEST RECORDED" -ForegroundColor Green
Write-Host "Proveedor: $($status.provider)"
Write-Host "Assets recuperados/verificados: $RecoveredAssetCount"
Write-Host "Estado: $statusPath"
Write-Host ""
Write-Host "IMPORTANTE: este comando solo registra una prueba REAL ya realizada; no sustituye la recuperación del proveedor."
