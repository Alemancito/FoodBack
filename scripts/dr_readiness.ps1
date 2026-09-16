[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\foodback_backup_common.ps1")

$projectRoot = Get-FoodBackProjectRoot
Import-FoodBackEnvFile -Path (Join-Path $projectRoot ".env")

$environment = Get-FoodBackEnvValue -Name "FOODBACK_ENVIRONMENT" -Default "development"
$backupMaxAgeHours = [int](Get-FoodBackEnvValue -Name "FOODBACK_BACKUP_MAX_AGE_HOURS" -Default "26")
$restoreMaxAgeDays = [int](Get-FoodBackEnvValue -Name "FOODBACK_RESTORE_TEST_MAX_AGE_DAYS" -Default "35")
$offsiteMaxAgeHours = [int](Get-FoodBackEnvValue -Name "FOODBACK_OFFSITE_MAX_AGE_HOURS" -Default "30")
$mediaRecoveryMaxAgeDays = [int](Get-FoodBackEnvValue -Name "FOODBACK_MEDIA_RECOVERY_TEST_MAX_AGE_DAYS" -Default "180")

if ($backupMaxAgeHours -lt 1 -or $backupMaxAgeHours -gt 720) {
    throw "FOODBACK_BACKUP_MAX_AGE_HOURS fuera de rango seguro."
}

if ($restoreMaxAgeDays -lt 1 -or $restoreMaxAgeDays -gt 365) {
    throw "FOODBACK_RESTORE_TEST_MAX_AGE_DAYS fuera de rango seguro."
}

if ($offsiteMaxAgeHours -lt 1 -or $offsiteMaxAgeHours -gt 2160) {
    throw "FOODBACK_OFFSITE_MAX_AGE_HOURS fuera de rango seguro."
}

if ($mediaRecoveryMaxAgeDays -lt 1 -or $mediaRecoveryMaxAgeDays -gt 730) {
    throw "FOODBACK_MEDIA_RECOVERY_TEST_MAX_AGE_DAYS fuera de rango seguro."
}

$statusDir = Get-FoodBackStatusDirectory -ProjectRoot $projectRoot
$now = [DateTime]::UtcNow
$failures = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]
$checks = New-Object System.Collections.Generic.List[object]

function Add-Check {
    param(
        [string]$Name,
        [string]$Status,
        [string]$Detail
    )

    $checks.Add([ordered]@{
        name = $Name
        status = $Status
        detail = $Detail
    }) | Out-Null

    $prefix = switch ($Status) {
        "PASS" { "[PASS]" }
        "WARN" { "[WARN]" }
        default { "[FAIL]" }
    }

    Write-Host "$prefix $Name - $Detail"
}

$backupStatusPath = Join-Path $statusDir "last_backup.json"
if (-not (Test-Path -LiteralPath $backupStatusPath)) {
    Add-Check -Name "Último backup" -Status "FAIL" -Detail "No existe last_backup.json."
    $failures.Add("last_backup_missing") | Out-Null
}
else {
    $backupStatus = Get-Content -LiteralPath $backupStatusPath -Raw | ConvertFrom-Json
    $backupTime = [DateTime]::Parse([string]$backupStatus.completed_at_utc).ToUniversalTime()
    $backupAgeHours = ($now - $backupTime).TotalHours

    if (-not (Test-Path -LiteralPath ([string]$backupStatus.backup_file))) {
        Add-Check -Name "Último backup" -Status "FAIL" -Detail "El archivo reportado ya no existe."
        $failures.Add("last_backup_file_missing") | Out-Null
    }
    elseif ($backupAgeHours -gt $backupMaxAgeHours) {
        Add-Check -Name "Último backup" -Status "FAIL" -Detail ("Antigüedad {0:N1}h > límite {1}h." -f $backupAgeHours, $backupMaxAgeHours)
        $failures.Add("last_backup_stale") | Out-Null
    }
    else {
        & (Join-Path $PSScriptRoot "verify_backup.ps1") -BackupPath ([string]$backupStatus.backup_file) | Out-Null
        Add-Check -Name "Último backup" -Status "PASS" -Detail ("Verificado; antigüedad {0:N1}h." -f $backupAgeHours)
    }
}

$restoreStatusPath = Join-Path $statusDir "last_restore_test.json"
if (-not (Test-Path -LiteralPath $restoreStatusPath)) {
    Add-Check -Name "Restore test" -Status "FAIL" -Detail "Todavía no existe un estado de restore-test registrado por el script actual."
    $failures.Add("restore_test_missing") | Out-Null
}
else {
    $restoreStatus = Get-Content -LiteralPath $restoreStatusPath -Raw | ConvertFrom-Json
    $restoreTime = [DateTime]::Parse([string]$restoreStatus.completed_at_utc).ToUniversalTime()
    $restoreAgeDays = ($now - $restoreTime).TotalDays

    if ($restoreAgeDays -gt $restoreMaxAgeDays) {
        Add-Check -Name "Restore test" -Status "FAIL" -Detail ("Antigüedad {0:N1}d > límite {1}d." -f $restoreAgeDays, $restoreMaxAgeDays)
        $failures.Add("restore_test_stale") | Out-Null
    }
    elseif (-not [bool]$restoreStatus.critical_tables_ok) {
        Add-Check -Name "Restore test" -Status "FAIL" -Detail "El último restore no validó tablas críticas."
        $failures.Add("restore_test_critical_tables") | Out-Null
    }
    elseif (-not [bool]$restoreStatus.temporary_database_cleanup_ok) {
        Add-Check -Name "Restore test" -Status "WARN" -Detail "Restore correcto, pero cleanup de BD temporal requiere revisión."
        $warnings.Add("restore_test_cleanup") | Out-Null
    }
    else {
        Add-Check -Name "Restore test" -Status "PASS" -Detail ("Probado hace {0:N1} días; tablas críticas OK." -f $restoreAgeDays)
    }
}

$offsiteStatusPath = Join-Path $statusDir "last_offsite_export.json"
if (-not (Test-Path -LiteralPath $offsiteStatusPath)) {
    if ($environment -eq "production") {
        Add-Check -Name "Copia offsite" -Status "FAIL" -Detail "Producción requiere una copia fuera del mismo proveedor/volumen."
        $failures.Add("offsite_missing") | Out-Null
    }
    else {
        Add-Check -Name "Copia offsite" -Status "WARN" -Detail "No configurada todavía en desarrollo."
        $warnings.Add("offsite_missing_dev") | Out-Null
    }
}
else {
    $offsiteStatus = Get-Content -LiteralPath $offsiteStatusPath -Raw | ConvertFrom-Json
    $offsiteTime = [DateTime]::Parse([string]$offsiteStatus.completed_at_utc).ToUniversalTime()
    $offsiteAgeHours = ($now - $offsiteTime).TotalHours
    $sameVolumeOverride = [bool]$offsiteStatus.same_volume_test_override

    if ($sameVolumeOverride) {
        Add-Check -Name "Copia offsite" -Status "WARN" -Detail "Última exportación fue una prueba en el mismo volumen; no cuenta como offsite real."
        $warnings.Add("offsite_same_volume_test") | Out-Null
    }
    elseif (-not (Test-Path -LiteralPath ([string]$offsiteStatus.destination_backup))) {
        Add-Check -Name "Copia offsite" -Status "FAIL" -Detail "El archivo offsite registrado no está accesible."
        $failures.Add("offsite_file_missing") | Out-Null
    }
    elseif ($offsiteAgeHours -gt $offsiteMaxAgeHours) {
        Add-Check -Name "Copia offsite" -Status "FAIL" -Detail ("Antigüedad {0:N1}h > límite {1}h." -f $offsiteAgeHours, $offsiteMaxAgeHours)
        $failures.Add("offsite_stale") | Out-Null
    }
    else {
        Add-Check -Name "Copia offsite" -Status "PASS" -Detail ("Accesible; antigüedad {0:N1}h." -f $offsiteAgeHours)
    }
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $git) {
    Add-Check -Name "Secretos en Git" -Status "WARN" -Detail "Git no disponible para comprobar .env."
    $warnings.Add("git_unavailable") | Out-Null
}
else {
    # `git ls-files --error-unmatch .env` devuelve código != 0 cuando
    # .env NO está versionado. Eso es el resultado seguro esperado,
    # pero con $ErrorActionPreference = "Stop" PowerShell puede tratar
    # el stderr de git.exe como una excepción y abortar el gate DR.
    #
    # En su lugar listamos únicamente archivos tracked. Si .env no está
    # versionado, Git devuelve salida vacía con código 0 y sin error.
    $trackedEnv = @(
        & git -C $projectRoot ls-files --cached -- .env
    )

    if ($LASTEXITCODE -ne 0) {
        Add-Check -Name "Secretos en Git" -Status "WARN" -Detail "Git no pudo comprobar si .env está versionado."
        $warnings.Add("git_check_failed") | Out-Null
    }
    elseif ($trackedEnv.Count -gt 0) {
        Add-Check -Name "Secretos en Git" -Status "FAIL" -Detail ".env está siendo versionado."
        $failures.Add("env_tracked") | Out-Null
    }
    else {
        Add-Check -Name "Secretos en Git" -Status "PASS" -Detail ".env no está versionado."
    }
}


# Recuperación del código: el backup de PostgreSQL no sirve por sí solo si no podemos
# reconstruir una versión compatible de la aplicación. En producción exigimos un
# repositorio Git válido, commit resoluble, remoto configurado y working tree limpio.
$gitRepoOk = $false
$currentGitCommit = ""
$currentGitBranch = ""
$gitDirty = $false
$gitRemoteCount = 0

if ($null -eq $git) {
    if ($environment -eq "production") {
        Add-Check -Name "Código / Git" -Status "FAIL" -Detail "Git no está disponible para validar recuperación del código."
        $failures.Add("git_unavailable_production") | Out-Null
    }
    else {
        Add-Check -Name "Código / Git" -Status "WARN" -Detail "Git no disponible; no se puede validar el commit recuperable."
        $warnings.Add("git_unavailable_code") | Out-Null
    }
}
elseif (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".git"))) {
    if ($environment -eq "production") {
        Add-Check -Name "Código / Git" -Status "FAIL" -Detail "El proyecto no contiene metadata Git local."
        $failures.Add("git_repo_missing") | Out-Null
    }
    else {
        Add-Check -Name "Código / Git" -Status "WARN" -Detail "No se detectó .git; revisar recuperación del código."
        $warnings.Add("git_repo_missing_dev") | Out-Null
    }
}
else {
    $currentGitCommit = ((& git -C $projectRoot rev-parse HEAD) | Select-Object -First 1).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($currentGitCommit)) {
        Add-Check -Name "Código / Git" -Status "FAIL" -Detail "No se pudo resolver HEAD."
        $failures.Add("git_head_unresolvable") | Out-Null
    }
    else {
        $gitRepoOk = $true
        $currentGitBranch = ((& git -C $projectRoot branch --show-current) | Select-Object -First 1).Trim()
        $remoteNames = @(& git -C $projectRoot remote)
        if ($LASTEXITCODE -eq 0) {
            $gitRemoteCount = $remoteNames.Count
        }

        $dirtyLines = @(& git -C $projectRoot status --porcelain --untracked-files=normal)
        if ($LASTEXITCODE -eq 0 -and $dirtyLines.Count -gt 0) {
            $gitDirty = $true
        }

        if ($gitRemoteCount -lt 1) {
            if ($environment -eq "production") {
                Add-Check -Name "Código / Git" -Status "FAIL" -Detail "No hay remoto Git configurado para recuperación del código."
                $failures.Add("git_remote_missing") | Out-Null
            }
            else {
                Add-Check -Name "Código / Git" -Status "WARN" -Detail "HEAD válido, pero no hay remoto Git configurado."
                $warnings.Add("git_remote_missing_dev") | Out-Null
            }
        }
        elseif ($gitDirty) {
            if ($environment -eq "production") {
                Add-Check -Name "Código / Git" -Status "FAIL" -Detail "Working tree con cambios no versionados; producción debe ser reproducible desde un commit."
                $failures.Add("git_dirty_production") | Out-Null
            }
            else {
                Add-Check -Name "Código / Git" -Status "WARN" -Detail "Remoto y HEAD disponibles, pero hay cambios locales sin commit."
                $warnings.Add("git_dirty_dev") | Out-Null
            }
        }
        else {
            Add-Check -Name "Código / Git" -Status "PASS" -Detail "HEAD resoluble, remoto configurado y working tree limpio."
        }
    }
}

# Compatibilidad entre dump y código. El backup registra el commit existente en el
# momento del pg_dump. Un mismatch no invalida el dump, pero debe revisarse antes de
# restaurarlo con una versión distinta de la aplicación.
if ($gitRepoOk -and (Test-Path -LiteralPath $backupStatusPath)) {
    if ($null -eq $backupStatus) {
        $backupStatus = Get-Content -LiteralPath $backupStatusPath -Raw | ConvertFrom-Json
    }

    $backupGitCommit = [string]$backupStatus.git_commit
    if ([string]::IsNullOrWhiteSpace($backupGitCommit)) {
        Add-Check -Name "Backup / commit" -Status "WARN" -Detail "El último backup no registró commit Git."
        $warnings.Add("backup_git_commit_missing") | Out-Null
    }
    elseif ($backupGitCommit -ne $currentGitCommit) {
        if ($environment -eq "production") {
            Add-Check -Name "Backup / commit" -Status "FAIL" -Detail "El backup fue creado con otro commit; revisar compatibilidad antes de recuperación."
            $failures.Add("backup_git_commit_mismatch") | Out-Null
        }
        else {
            Add-Check -Name "Backup / commit" -Status "WARN" -Detail "El backup corresponde a otro commit; normal durante desarrollo, pero debe revisarse al restaurar."
            $warnings.Add("backup_git_commit_mismatch_dev") | Out-Null
        }
    }
    else {
        Add-Check -Name "Backup / commit" -Status "PASS" -Detail "El último backup está asociado al HEAD actual."
    }
}

# PostgreSQL no contiene los binarios almacenados por Cloudinary/u otro proveedor.
# El gate solo da PASS cuando existe evidencia explícita de una recuperación real.
$mediaStatusPath = Join-Path $statusDir "last_media_recovery_test.json"
if (-not (Test-Path -LiteralPath $mediaStatusPath)) {
    if ($environment -eq "production") {
        Add-Check -Name "Media externa" -Status "FAIL" -Detail "No existe evidencia de una recuperación real de media externa."
        $failures.Add("media_recovery_missing") | Out-Null
    }
    else {
        Add-Check -Name "Media externa" -Status "WARN" -Detail "Prueba real de recuperación de Cloudinary/media diferida hasta preparar producción."
        $warnings.Add("media_recovery_missing_dev") | Out-Null
    }
}
else {
    $mediaStatus = Get-Content -LiteralPath $mediaStatusPath -Raw | ConvertFrom-Json
    $mediaTime = [DateTime]::Parse([string]$mediaStatus.tested_at_utc).ToUniversalTime()
    $mediaAgeDays = ($now - $mediaTime).TotalDays

    if (-not [bool]$mediaStatus.confirmed_real_recovery) {
        Add-Check -Name "Media externa" -Status "FAIL" -Detail "El último registro no confirma una recuperación real."
        $failures.Add("media_recovery_not_confirmed") | Out-Null
    }
    elseif ($mediaAgeDays -gt $mediaRecoveryMaxAgeDays) {
        Add-Check -Name "Media externa" -Status "FAIL" -Detail ("Prueba hace {0:N1}d > límite {1}d." -f $mediaAgeDays, $mediaRecoveryMaxAgeDays)
        $failures.Add("media_recovery_stale") | Out-Null
    }
    else {
        Add-Check -Name "Media externa" -Status "PASS" -Detail ("Recuperación real registrada hace {0:N1}d; proveedor: {1}." -f $mediaAgeDays, [string]$mediaStatus.provider)
    }
}

$overall = if ($failures.Count -gt 0) {
    "FAIL"
}
elseif ($warnings.Count -gt 0) {
    "WARN"
}
else {
    "PASS"
}

$readinessStatusPath = Join-Path $statusDir "dr_readiness.json"
$readiness = [ordered]@{
    status = $overall
    checked_at_utc = $now.ToString("o")
    environment = $environment
    git = [ordered]@{
        repo_ok = $gitRepoOk
        commit = $currentGitCommit
        branch = $currentGitBranch
        dirty = $gitDirty
        remote_count = $gitRemoteCount
    }
    checks = $checks
    failures = $failures
    warnings = $warnings
}

Write-FoodBackJsonAtomic `
    -Path $readinessStatusPath `
    -Data $readiness

Write-Host ""
Write-Host "DR READINESS: $overall"
Write-Host "Estado: $readinessStatusPath"

if ($overall -eq "FAIL") {
    exit 1
}

exit 0
