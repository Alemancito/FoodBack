param(
    [Parameter(Mandatory = $false)]
    [string]$PackagePath = ""
)

$ErrorActionPreference = "Stop"

$failures = 0
$warnings = 0

function Pass([string]$Message) {
    Write-Host "[PASS] $Message"
}

function Fail([string]$Message) {
    $script:failures += 1
    Write-Host "[FAIL] $Message"
}

function Warn([string]$Message) {
    $script:warnings += 1
    Write-Host "[WARN] $Message"
}

function Git-LsFiles([string[]]$Arguments) {
    $output = & git @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Git no pudo inspeccionar el repositorio."
    }
    return @($output | Where-Object { $_ -and $_.Trim() })
}

Write-Host "FoodBack Security Preflight"
Write-Host "=========================="

$insideRepo = (& git rev-parse --is-inside-work-tree 2>$null)
if ($LASTEXITCODE -ne 0 -or $insideRepo -ne "true") {
    Fail "Debe ejecutarse dentro del repositorio Git de FoodBack."
}
else {
    Pass "Repositorio Git detectado."
}

if ($failures -eq 0) {
    $trackedSensitive = @()
    $patterns = @(
        ".env",
        "*.env",
        "*.sqlite3",
        "*.backup",
        "*.dump",
        "*.sql.gz",
        "*.bak",
        "*.code-workspace"
    )

    foreach ($pattern in $patterns) {
        $trackedSensitive += Git-LsFiles @("ls-files", $pattern)
    }

    $trackedSensitive = @($trackedSensitive | Sort-Object -Unique)

    if ($trackedSensitive.Count -eq 0) {
        Pass "No hay secretos, bases locales, dumps o workspaces locales versionados."
    }
    else {
        Fail ("Archivos locales/sensibles versionados: " + ($trackedSensitive -join ", "))
    }

    $envTracked = Git-LsFiles @("ls-files", ".env")
    if ($envTracked.Count -eq 0) {
        Pass ".env no está versionado."
    }
    else {
        Fail ".env aparece versionado."
    }

    & git check-ignore -q .env
    if ($LASTEXITCODE -eq 0) {
        Pass ".env está cubierto por .gitignore."
    }
    else {
        Fail ".env no está cubierto por .gitignore."
    }

    & git check-ignore -q db.sqlite3
    if ($LASTEXITCODE -eq 0) {
        Pass "db.sqlite3 está cubierto por .gitignore."
    }
    else {
        Fail "db.sqlite3 no está cubierto por .gitignore."
    }

    $workspaceRule = Select-String -Path .gitignore -Pattern '^\*\.code-workspace$' -Quiet
    if ($workspaceRule) {
        Pass "Los workspaces locales de VS Code están ignorados."
    }
    else {
        Fail "Falta ignorar *.code-workspace."
    }

    $exampleTracked = Git-LsFiles @("ls-files", ".env.example")
    if ($exampleTracked.Count -eq 1) {
        Pass ".env.example permanece versionado como plantilla."
    }
    else {
        Warn ".env.example no está versionado; revisar onboarding/configuración."
    }
}

if ($PackagePath) {
    if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
        Fail "El paquete indicado no existe."
    }
    elseif ([System.IO.Path]::GetExtension($PackagePath).ToLowerInvariant() -ne ".zip") {
        Fail "PackagePath debe apuntar a un ZIP."
    }
    else {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $PackagePath))
        try {
            $forbidden = @(
                '.env',
                'db.sqlite3'
            )

            $badEntries = @()
            foreach ($entry in $zip.Entries) {
                $normalized = $entry.FullName.Replace('\\', '/').TrimStart('/')
                $leaf = [System.IO.Path]::GetFileName($normalized)

                if (
                    $forbidden -contains $leaf -or
                    $normalized -match '(^|/)\.git(/|$)' -or
                    $normalized -match '(^|/)\.venv(/|$)' -or
                    $normalized -match '(^|/)backups(/|$)' -or
                    $leaf -like '*.backup' -or
                    $leaf -like '*.dump' -or
                    $leaf -like '*.sql.gz' -or
                    $leaf -like '*.code-workspace'
                ) {
                    $badEntries += $normalized
                }
            }

            $badEntries = @($badEntries | Sort-Object -Unique)
            if ($badEntries.Count -eq 0) {
                Pass "El ZIP no contiene secretos ni artefactos locales prohibidos."
            }
            else {
                Fail ("El ZIP contiene archivos prohibidos: " + ($badEntries -join ", "))
            }
        }
        finally {
            $zip.Dispose()
        }
    }
}
else {
    Warn "No se indicó -PackagePath; se validó el repositorio, no un ZIP de entrega."
}

Write-Host ""
if ($failures -gt 0) {
    Write-Host "SECURITY PREFLIGHT: FAIL ($failures fallo(s), $warnings advertencia(s))"
    exit 1
}

Write-Host "SECURITY PREFLIGHT: PASS ($warnings advertencia(s))"
exit 0
