[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\foodback_backup_common.ps1")

$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
$file = Get-Item -LiteralPath $resolvedBackup

if ($file.Length -lt 1024) {
    throw "El backup es demasiado pequeño para considerarse válido: $($file.Length) bytes."
}

$pgRestore = Resolve-PostgresTool -Name "pg_restore"

$toc = & $pgRestore --list $resolvedBackup
if ($LASTEXITCODE -ne 0) {
    throw "pg_restore no pudo leer el catálogo del backup."
}

if (-not $toc -or $toc.Count -eq 0) {
    throw "El catálogo del backup está vacío."
}

$requiredObjects = @(
    "django_migrations",
    "pedidos_tenant",
    "pedidos_auditevent",
    "pedidos_securityincident"
)

foreach ($objectName in $requiredObjects) {
    $found = $toc | Select-String -SimpleMatch $objectName
    if (-not $found) {
        throw "El backup no contiene el objeto esperado: $objectName"
    }
}

$hash = (Get-FileHash -LiteralPath $resolvedBackup -Algorithm SHA256).Hash.ToLowerInvariant()
$metadataPath = $resolvedBackup + ".json"

if (Test-Path -LiteralPath $metadataPath) {
    $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json

    if (
        -not [string]::IsNullOrWhiteSpace([string]$metadata.sha256) -and
        ([string]$metadata.sha256).ToLowerInvariant() -ne $hash
    ) {
        throw "El SHA-256 del backup no coincide con su metadata. Posible corrupción o modificación."
    }
}
else {
    Write-Warning "No se encontró metadata JSON. El catálogo es válido, pero no puede verificarse el hash esperado."
}

Write-Host "Backup verificado correctamente." -ForegroundColor Green
Write-Host "Archivo: $resolvedBackup"
Write-Host "Tamaño: $($file.Length) bytes"
Write-Host "SHA-256: $hash"
Write-Host "Objetos críticos encontrados: $($requiredObjects -join ', ')"
