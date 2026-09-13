$ErrorActionPreference = "Stop"

$password = Read-Host "Password foodback_test" -AsSecureString
$plainPassword = [System.Net.NetworkCredential]::new("", $password).Password

try {
    $env:FOODBACK_DB_MODE = "test"
    $env:FOODBACK_DB_TEST_USER = "foodback_test"
    $env:FOODBACK_DB_TEST_PASSWORD = $plainPassword

    python manage.py test pedidos

    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Remove-Item Env:FOODBACK_DB_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:FOODBACK_DB_TEST_USER -ErrorAction SilentlyContinue
    Remove-Item Env:FOODBACK_DB_TEST_PASSWORD -ErrorAction SilentlyContinue

    $plainPassword = $null
    $password = $null
}