[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,

    [switch]$KeepDatabase
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\foodback_backup_common.ps1")

$projectRoot = Get-FoodBackProjectRoot
Import-FoodBackEnvFile -Path (Join-Path $projectRoot ".env")

$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
& (Join-Path $PSScriptRoot "verify_backup.ps1") -BackupPath $resolvedBackup

$dbHost = Get-FoodBackEnvValue -Name "FOODBACK_DB_HOST" -Default "127.0.0.1"
$dbPort = Get-FoodBackEnvValue -Name "FOODBACK_DB_PORT" -Default "5432"
$sourceDb = Get-FoodBackEnvValue -Name "FOODBACK_DB_NAME" -Default "foodback_local"
$testUser = Get-FoodBackEnvValue -Name "FOODBACK_DB_TEST_USER" -Default "foodback_test"

Assert-SafePostgresIdentifier -Value $sourceDb -Label "FOODBACK_DB_NAME"
Assert-SafePostgresIdentifier -Value $testUser -Label "FOODBACK_DB_TEST_USER"

$testPassword = Get-FoodBackSecretOrPrompt `
    -EnvName "FOODBACK_DB_TEST_PASSWORD" `
    -Prompt "Password $testUser"

$createdb = Resolve-PostgresTool -Name "createdb"
$dropdb = Resolve-PostgresTool -Name "dropdb"
$pgRestore = Resolve-PostgresTool -Name "pg_restore"
$psql = Resolve-PostgresTool -Name "psql"

$targetDb = "foodback_restore_test_" + [DateTime]::UtcNow.ToString("yyyyMMddHHmmss")
Assert-SafePostgresIdentifier -Value $targetDb -Label "Base temporal de restore"

if ($targetDb -eq $sourceDb) {
    throw "Protección activada: la base temporal nunca puede ser la base origen."
}

$previousPgPassword = $null
$databaseCreated = $false
$restoreSucceeded = $false
$cleanupSucceeded = $false
$migrationsRestored = 0
$tenantsRestored = 0
$backupSha256 = (Get-FileHash -LiteralPath $resolvedBackup -Algorithm SHA256).Hash.ToLowerInvariant()

try {
    $previousPgPassword = Set-TemporaryPgPassword -Password $testPassword

    Write-Host "Creando base temporal: $targetDb"

    & $createdb `
        -h $dbHost `
        -p $dbPort `
        -U $testUser `
        -T template0 `
        -E UTF8 `
        $targetDb

    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo crear la base temporal de restauración."
    }

    $databaseCreated = $true

    Write-Host "Restaurando backup en la base temporal..."

    & $pgRestore `
        -h $dbHost `
        -p $dbPort `
        -U $testUser `
        -d $targetDb `
        --exit-on-error `
        --no-owner `
        --no-acl `
        $resolvedBackup

    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore falló con código $LASTEXITCODE."
    }

    $criticalTablesSql = @"
SELECT
    (to_regclass('public.django_migrations') IS NOT NULL)::int || '|' ||
    (to_regclass('public.pedidos_tenant') IS NOT NULL)::int || '|' ||
    (to_regclass('public.pedidos_auditevent') IS NOT NULL)::int || '|' ||
    (to_regclass('public.pedidos_securityincident') IS NOT NULL)::int || '|' ||
    (SELECT COUNT(*) FROM public.django_migrations) || '|' ||
    (SELECT COUNT(*) FROM public.pedidos_tenant);
"@

    $verification = & $psql `
        -X `
        -t `
        -A `
        -h $dbHost `
        -p $dbPort `
        -U $testUser `
        -d $targetDb `
        -c $criticalTablesSql

    if ($LASTEXITCODE -ne 0) {
        throw "La restauración terminó, pero la verificación SQL falló."
    }

    $verification = ($verification | Select-Object -Last 1).Trim()
    $parts = $verification.Split('|')

    if ($parts.Length -ne 6) {
        throw "Respuesta inesperada durante verificación: $verification"
    }

    if ($parts[0] -ne "1" -or $parts[1] -ne "1" -or $parts[2] -ne "1" -or $parts[3] -ne "1") {
        throw "Faltan tablas críticas después del restore: $verification"
    }

    if ([int]$parts[4] -le 0) {
        throw "django_migrations quedó vacío después del restore."
    }

    $migrationsRestored = [int]$parts[4]
    $tenantsRestored = [int]$parts[5]
    $restoreSucceeded = $true
}
finally {
    if ($databaseCreated -and -not $KeepDatabase) {
        Write-Host "Eliminando base temporal de restauración..."

        & $dropdb `
            -h $dbHost `
            -p $dbPort `
            -U $testUser `
            --if-exists `
            --force `
            $targetDb

        if ($LASTEXITCODE -ne 0) {
            Write-Warning "No se pudo eliminar automáticamente $targetDb. Elimínala manualmente."
        }
        else {
            $cleanupSucceeded = $true
        }
    }
    elseif ($databaseCreated -and $KeepDatabase) {
        Write-Warning "La base temporal se conservó por -KeepDatabase: $targetDb"
        $cleanupSucceeded = $true
    }

    Restore-TemporaryPgPassword -PreviousValue $previousPgPassword
    $testPassword = $null
}

if ($restoreSucceeded) {
    $statusDir = Get-FoodBackStatusDirectory -ProjectRoot $projectRoot
    $restoreStatusPath = Join-Path $statusDir "last_restore_test.json"

    $restoreStatus = [ordered]@{
        status = "ok"
        completed_at_utc = [DateTime]::UtcNow.ToString("o")
        backup_file = $resolvedBackup
        backup_sha256 = $backupSha256
        temporary_database = $targetDb
        migrations_restored = $migrationsRestored
        tenants_restored = $tenantsRestored
        critical_tables_ok = $true
        temporary_database_cleanup_ok = $cleanupSucceeded
        kept_database = [bool]$KeepDatabase
    }

    Write-FoodBackJsonAtomic `
        -Path $restoreStatusPath `
        -Data $restoreStatus

    Write-Host ""
    Write-Host "RESTORE TEST OK" -ForegroundColor Green
    Write-Host "Base temporal: $targetDb"
    Write-Host "Migraciones restauradas: $migrationsRestored"
    Write-Host "Tenants restaurados: $tenantsRestored"
    Write-Host "Tablas críticas: OK"
    Write-Host "Cleanup base temporal: $cleanupSucceeded"
    Write-Host "Estado DR: $restoreStatusPath"
}
