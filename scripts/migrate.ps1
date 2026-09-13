$ErrorActionPreference = "Stop"

$password = Read-Host "Password foodback_migrator" -AsSecureString
$plainPassword = [System.Net.NetworkCredential]::new("", $password).Password

try {
    $env:FOODBACK_DB_MODE = "migrator"
    $env:FOODBACK_DB_MIGRATOR_USER = "foodback_migrator"
    $env:FOODBACK_DB_MIGRATOR_PASSWORD = $plainPassword

    python manage.py migrate

    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Remove-Item Env:FOODBACK_DB_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:FOODBACK_DB_MIGRATOR_USER -ErrorAction SilentlyContinue
    Remove-Item Env:FOODBACK_DB_MIGRATOR_PASSWORD -ErrorAction SilentlyContinue

    $plainPassword = $null
    $password = $null
}