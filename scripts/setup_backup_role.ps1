$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\foodback_backup_common.ps1")

$projectRoot = Get-FoodBackProjectRoot
Import-FoodBackEnvFile -Path (Join-Path $projectRoot ".env")

$dbName = Get-FoodBackEnvValue -Name "FOODBACK_DB_NAME" -Default "foodback_local"
$dbHost = Get-FoodBackEnvValue -Name "FOODBACK_DB_HOST" -Default "127.0.0.1"
$dbPort = Get-FoodBackEnvValue -Name "FOODBACK_DB_PORT" -Default "5432"
$migratorUser = Get-FoodBackEnvValue -Name "FOODBACK_DB_MIGRATOR_USER" -Default "foodback_migrator"
$backupUser = Get-FoodBackEnvValue -Name "FOODBACK_DB_BACKUP_USER" -Default "foodback_backup"

Assert-SafePostgresIdentifier -Value $dbName -Label "FOODBACK_DB_NAME"
Assert-SafePostgresIdentifier -Value $migratorUser -Label "FOODBACK_DB_MIGRATOR_USER"
Assert-SafePostgresIdentifier -Value $backupUser -Label "FOODBACK_DB_BACKUP_USER"

$psql = Resolve-PostgresTool -Name "psql"

$adminUser = Read-Host "Usuario administrador PostgreSQL [postgres]"
if ([string]::IsNullOrWhiteSpace($adminUser)) {
    $adminUser = "postgres"
}
Assert-SafePostgresIdentifier -Value $adminUser -Label "Usuario administrador"

$adminSecure = Read-Host "Password $adminUser" -AsSecureString
$backupSecure1 = Read-Host "Nueva password $backupUser" -AsSecureString
$backupSecure2 = Read-Host "Repite password $backupUser" -AsSecureString

$adminPassword = $null
$backupPassword1 = $null
$backupPassword2 = $null
$previousPgPassword = $null

try {
    $adminPassword = Convert-SecureStringToPlainText -SecureString $adminSecure
    $backupPassword1 = Convert-SecureStringToPlainText -SecureString $backupSecure1
    $backupPassword2 = Convert-SecureStringToPlainText -SecureString $backupSecure2

    if ($backupPassword1 -ne $backupPassword2) {
        throw "Las passwords del rol de backup no coinciden."
    }

    if ($backupPassword1.Length -lt 20) {
        throw "La password del rol de backup debe tener al menos 20 caracteres."
    }

    $escapedBackupPassword = $backupPassword1.Replace("'", "''")
    $quotedDb = '"' + $dbName.Replace('"', '""') + '"'
    $quotedBackup = '"' + $backupUser.Replace('"', '""') + '"'
    $quotedMigrator = '"' + $migratorUser.Replace('"', '""') + '"'

    $sql = @"
DO `$foodback`$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = '$backupUser'
    ) THEN
        CREATE ROLE $quotedBackup
            LOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            BYPASSRLS
            PASSWORD '$escapedBackupPassword';
    ELSE
        ALTER ROLE $quotedBackup
            LOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            BYPASSRLS
            PASSWORD '$escapedBackupPassword';
    END IF;
END
`$foodback`$;

ALTER ROLE $quotedBackup SET default_transaction_read_only = on;

REVOKE ALL PRIVILEGES ON DATABASE $quotedDb FROM $quotedBackup;
GRANT CONNECT ON DATABASE $quotedDb TO $quotedBackup;

REVOKE ALL PRIVILEGES ON SCHEMA public FROM $quotedBackup;
GRANT USAGE ON SCHEMA public TO $quotedBackup;

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM $quotedBackup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO $quotedBackup;

REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM $quotedBackup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO $quotedBackup;

ALTER DEFAULT PRIVILEGES FOR ROLE $quotedMigrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM $quotedBackup;
ALTER DEFAULT PRIVILEGES FOR ROLE $quotedMigrator IN SCHEMA public
    GRANT SELECT ON TABLES TO $quotedBackup;

ALTER DEFAULT PRIVILEGES FOR ROLE $quotedMigrator IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM $quotedBackup;
ALTER DEFAULT PRIVILEGES FOR ROLE $quotedMigrator IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO $quotedBackup;
"@

    $previousPgPassword = Set-TemporaryPgPassword -Password $adminPassword

    $sql | & $psql `
        -X `
        -v ON_ERROR_STOP=1 `
        -h $dbHost `
        -p $dbPort `
        -U $adminUser `
        -d $dbName

    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo configurar el rol $backupUser."
    }

    Restore-TemporaryPgPassword -PreviousValue $previousPgPassword
    $previousPgPassword = Set-TemporaryPgPassword -Password $backupPassword1

    $verification = & $psql `
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
        throw "El rol fue configurado, pero no pudo verificarse iniciando sesión."
    }

    $verification = ($verification | Select-Object -Last 1).Trim()

    if ($verification -ne "$backupUser|f|t|on") {
        throw "Configuración inesperada del rol de backup: $verification"
    }

    Write-Host ""
    Write-Host "Rol de backup configurado correctamente: $backupUser" -ForegroundColor Green
    Write-Host "- NOSUPERUSER"
    Write-Host "- solo lectura por defecto"
    Write-Host "- SELECT en tablas/secuencias"
    Write-Host "- BYPASSRLS únicamente para obtener backups completos de todos los tenants"
    Write-Host ""
    Write-Host "Agrega estas variables a tu .env local (la password real NO va al repo):"
    Write-Host "FOODBACK_DB_BACKUP_USER=$backupUser"
    Write-Host "FOODBACK_DB_BACKUP_PASSWORD=<la password que acabas de elegir>"
}
finally {
    Restore-TemporaryPgPassword -PreviousValue $previousPgPassword

    $adminSecure = $null
    $backupSecure1 = $null
    $backupSecure2 = $null
    $adminPassword = $null
    $backupPassword1 = $null
    $backupPassword2 = $null
    $escapedBackupPassword = $null
}
