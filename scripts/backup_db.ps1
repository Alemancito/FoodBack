[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [int]$RetentionDays = 0,
    [switch]$SkipRetention
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\foodback_backup_common.ps1")

$projectRoot = Get-FoodBackProjectRoot
Import-FoodBackEnvFile -Path (Join-Path $projectRoot ".env")

$dbName = Get-FoodBackEnvValue -Name "FOODBACK_DB_NAME" -Default "foodback_local"
$dbHost = Get-FoodBackEnvValue -Name "FOODBACK_DB_HOST" -Default "127.0.0.1"
$dbPort = Get-FoodBackEnvValue -Name "FOODBACK_DB_PORT" -Default "5432"
$backupUser = Get-FoodBackEnvValue -Name "FOODBACK_DB_BACKUP_USER" -Default "foodback_backup"

Assert-SafePostgresIdentifier -Value $dbName -Label "FOODBACK_DB_NAME"
Assert-SafePostgresIdentifier -Value $backupUser -Label "FOODBACK_DB_BACKUP_USER"

if ($RetentionDays -le 0) {
    $RetentionDays = [int](Get-FoodBackEnvValue -Name "FOODBACK_BACKUP_RETENTION_DAYS" -Default "14")
}

if ($RetentionDays -lt 1 -or $RetentionDays -gt 3650) {
    throw "RetentionDays debe estar entre 1 y 3650."
}

$backupDir = Get-FoodBackBackupDirectory `
    -ProjectRoot $projectRoot `
    -Override $OutputDirectory

$pgDump = Resolve-PostgresTool -Name "pg_dump"
$pgRestore = Resolve-PostgresTool -Name "pg_restore"
$psql = Resolve-PostgresTool -Name "psql"

$backupPassword = Get-FoodBackSecretOrPrompt `
    -EnvName "FOODBACK_DB_BACKUP_PASSWORD" `
    -Prompt "Password $backupUser"

$previousPgPassword = $null
$tempPath = $null

try {
    $previousPgPassword = Set-TemporaryPgPassword -Password $backupPassword

    $roleStatus = & $psql `
        -X `
        -t `
        -A `
        -F '|' `
        -h $dbHost `
        -p $dbPort `
        -U $backupUser `
        -d $dbName `
        -c "SELECT current_user, rolsuper, rolbypassrls, current_setting('default_transaction_read_only') FROM pg_roles WHERE rolname = current_user;"

    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo validar el rol de backup."
    }

    $roleStatus = ($roleStatus | Select-Object -Last 1).Trim()
    if ($roleStatus -ne "$backupUser|f|t|on") {
        throw (
            "El rol de backup no cumple el perfil seguro requerido. " +
            "Esperado: $backupUser|f|t|on. Recibido: $roleStatus. " +
            "Ejecuta .\\scripts\\setup_backup_role.ps1."
        )
    }

    $timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $safeDbName = $dbName -replace '[^A-Za-z0-9._-]', '_'
    $fileName = "foodback_${safeDbName}_${timestamp}.backup"
    $finalPath = Join-Path $backupDir $fileName
    $tempPath = $finalPath + ".partial"

    if (Test-Path -LiteralPath $finalPath) {
        throw "Ya existe un backup con el mismo nombre: $finalPath"
    }

    Write-Host "Creando backup consistente de PostgreSQL..."

    & $pgDump `
        -h $dbHost `
        -p $dbPort `
        -U $backupUser `
        -d $dbName `
        --format=custom `
        --compress=6 `
        --no-owner `
        --no-acl `
        --file $tempPath

    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump terminó con código $LASTEXITCODE."
    }

    if (-not (Test-Path -LiteralPath $tempPath)) {
        throw "pg_dump terminó sin crear el archivo temporal."
    }

    $tempFile = Get-Item -LiteralPath $tempPath
    if ($tempFile.Length -lt 1024) {
        throw "El dump generado es sospechosamente pequeño: $($tempFile.Length) bytes."
    }

    & $pgRestore --list $tempPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "El dump fue creado, pero pg_restore no puede leer su catálogo."
    }

    Move-Item -LiteralPath $tempPath -Destination $finalPath
    $tempPath = $null

    $finalFile = Get-Item -LiteralPath $finalPath
    $sha256 = (Get-FileHash -LiteralPath $finalPath -Algorithm SHA256).Hash.ToLowerInvariant()

    $gitCommit = ""
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -ne $git) {
        $gitCommit = (& git -C $projectRoot rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -ne 0) {
            $gitCommit = ""
        }
        else {
            $gitCommit = ([string]$gitCommit).Trim()
        }
    }

    $pgDumpVersion = (& $pgDump --version | Select-Object -First 1).Trim()

    $metadata = [ordered]@{
        format_version = 1
        created_at_utc = [DateTime]::UtcNow.ToString("o")
        database = $dbName
        environment = (Get-FoodBackEnvValue -Name "FOODBACK_ENVIRONMENT" -Default "development")
        backup_file = $finalFile.Name
        size_bytes = $finalFile.Length
        sha256 = $sha256
        pg_dump_version = $pgDumpVersion
        git_commit = $gitCommit
        contains_secrets_file = $false
    }

    $metadataPath = $finalPath + ".json"
    $metadata | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $metadataPath -Encoding UTF8

    & (Join-Path $PSScriptRoot "verify_backup.ps1") -BackupPath $finalPath

    $statusDir = Get-FoodBackStatusDirectory -ProjectRoot $projectRoot
    $backupStatusPath = Join-Path $statusDir "last_backup.json"

    $backupStatus = [ordered]@{
        status = "ok"
        completed_at_utc = [DateTime]::UtcNow.ToString("o")
        database = $dbName
        environment = (Get-FoodBackEnvValue -Name "FOODBACK_ENVIRONMENT" -Default "development")
        backup_file = $finalPath
        metadata_file = $metadataPath
        size_bytes = $finalFile.Length
        sha256 = $sha256
        git_commit = $gitCommit
    }

    Write-FoodBackJsonAtomic `
        -Path $backupStatusPath `
        -Data $backupStatus

    if (-not $SkipRetention) {
        $cutoff = [DateTime]::UtcNow.AddDays(-$RetentionDays)
        $deleted = 0

        Get-ChildItem -LiteralPath $backupDir -File -Filter "foodback_*.backup" |
            Where-Object {
                $_.FullName -ne $finalPath -and
                $_.LastWriteTimeUtc -lt $cutoff
            } |
            ForEach-Object {
                $oldBackup = $_.FullName
                $oldMetadata = $oldBackup + ".json"

                Remove-Item -LiteralPath $oldBackup -Force
                if (Test-Path -LiteralPath $oldMetadata) {
                    Remove-Item -LiteralPath $oldMetadata -Force
                }

                $deleted += 1
            }

        Write-Host "Retención aplicada: $RetentionDays días. Backups antiguos eliminados: $deleted"
    }

    Write-Host ""
    Write-Host "BACKUP OK" -ForegroundColor Green
    Write-Host "Ruta: $finalPath"
    Write-Host "Metadata: $metadataPath"
    Write-Host "SHA-256: $sha256"
}
catch {
    if ($null -ne $tempPath -and (Test-Path -LiteralPath $tempPath)) {
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    }

    throw
}
finally {
    Restore-TemporaryPgPassword -PreviousValue $previousPgPassword
    $backupPassword = $null
}
