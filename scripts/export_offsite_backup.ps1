[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,

    [string]$Destination = "",

    [switch]$AllowSameVolumeForDevelopmentTest
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\foodback_backup_common.ps1")

$projectRoot = Get-FoodBackProjectRoot
Import-FoodBackEnvFile -Path (Join-Path $projectRoot ".env")

$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
$metadataPath = $resolvedBackup + ".json"

& (Join-Path $PSScriptRoot "verify_backup.ps1") -BackupPath $resolvedBackup

if (-not (Test-Path -LiteralPath $metadataPath)) {
    throw "El backup no tiene metadata JSON. No se exportará offsite."
}

if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Get-FoodBackEnvValue `
        -Name "FOODBACK_BACKUP_OFFSITE_DIR" `
        -Default ""
}

if ([string]::IsNullOrWhiteSpace($Destination)) {
    throw (
        "Debes indicar -Destination o configurar FOODBACK_BACKUP_OFFSITE_DIR. " +
        "La ubicación debe estar fuera del proyecto."
    )
}

$destinationFull = [System.IO.Path]::GetFullPath($Destination)

if (Test-FoodBackPathInside -ParentPath $projectRoot -ChildPath $destinationFull) {
    throw "La copia offsite no puede almacenarse dentro del proyecto FoodBack."
}

$sourceRoot = [System.IO.Path]::GetPathRoot($resolvedBackup)
$destinationRoot = [System.IO.Path]::GetPathRoot($destinationFull)

if (
    -not $AllowSameVolumeForDevelopmentTest -and
    -not [string]::IsNullOrWhiteSpace($sourceRoot) -and
    $sourceRoot.Equals(
        $destinationRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw (
        "La ubicación destino está en el mismo volumen que el backup local. " +
        "Eso no protege contra pérdida del disco. Usa otro volumen, red o almacenamiento sincronizado/cifrado. " +
        "Solo para probar el script puedes usar -AllowSameVolumeForDevelopmentTest."
    )
}

$now = [DateTime]::UtcNow
$archiveDir = Join-Path $destinationFull ("postgres\{0}\{1}" -f $now.ToString("yyyy"), $now.ToString("MM"))
New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null

$sourceFile = Get-Item -LiteralPath $resolvedBackup
$sourceHash = (Get-FileHash -LiteralPath $resolvedBackup -Algorithm SHA256).Hash.ToLowerInvariant()

$targetBackup = Join-Path $archiveDir $sourceFile.Name
$targetMetadata = $targetBackup + ".json"
$tempBackup = $targetBackup + ".partial"
$tempMetadata = $targetMetadata + ".partial"

try {
    if (Test-Path -LiteralPath $targetBackup) {
        $existingHash = (Get-FileHash -LiteralPath $targetBackup -Algorithm SHA256).Hash.ToLowerInvariant()

        if ($existingHash -ne $sourceHash) {
            throw "Ya existe un archivo offsite con el mismo nombre pero SHA-256 diferente."
        }

        Write-Host "La copia offsite ya existe y coincide por SHA-256."
    }
    else {
        Copy-Item -LiteralPath $resolvedBackup -Destination $tempBackup

        $copiedHash = (Get-FileHash -LiteralPath $tempBackup -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($copiedHash -ne $sourceHash) {
            throw "La copia temporal offsite no coincide con el SHA-256 del origen."
        }

        Move-Item -LiteralPath $tempBackup -Destination $targetBackup
    }

    Copy-Item -LiteralPath $metadataPath -Destination $tempMetadata -Force
    Move-Item -LiteralPath $tempMetadata -Destination $targetMetadata -Force

    $finalHash = (Get-FileHash -LiteralPath $targetBackup -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($finalHash -ne $sourceHash) {
        throw "La copia offsite final no coincide con el SHA-256 del origen."
    }

    $statusDir = Get-FoodBackStatusDirectory -ProjectRoot $projectRoot
    $offsiteStatusPath = Join-Path $statusDir "last_offsite_export.json"

    $offsiteStatus = [ordered]@{
        status = "ok"
        completed_at_utc = [DateTime]::UtcNow.ToString("o")
        source_backup = $resolvedBackup
        destination_backup = $targetBackup
        sha256 = $sourceHash
        size_bytes = $sourceFile.Length
        same_volume_test_override = [bool]$AllowSameVolumeForDevelopmentTest
    }

    Write-FoodBackJsonAtomic `
        -Path $offsiteStatusPath `
        -Data $offsiteStatus

    Write-Host ""
    Write-Host "OFFSITE COPY OK" -ForegroundColor Green
    Write-Host "Destino: $targetBackup"
    Write-Host "Metadata: $targetMetadata"
    Write-Host "SHA-256: $sourceHash"

    if ($AllowSameVolumeForDevelopmentTest) {
        Write-Warning (
            "Esta copia se permitió en el mismo volumen SOLO para probar el flujo. " +
            "No cuenta como copia offsite real."
        )
    }
}
finally {
    foreach ($partial in @($tempBackup, $tempMetadata)) {
        if (Test-Path -LiteralPath $partial) {
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        }
    }
}
