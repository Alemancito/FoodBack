$ErrorActionPreference = "Stop"

function Get-FoodBackProjectRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

function Import-FoodBackEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $trimmed = $line.Trim()

        if (
            [string]::IsNullOrWhiteSpace($trimmed) -or
            $trimmed.StartsWith("#")
        ) {
            continue
        }

        $separatorIndex = $trimmed.IndexOf("=")
        if ($separatorIndex -lt 1) {
            continue
        }

        $name = $trimmed.Substring(0, $separatorIndex).Trim()
        $value = $trimmed.Substring($separatorIndex + 1).Trim()

        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)

            if (
                ($first -eq '"' -and $last -eq '"') -or
                ($first -eq "'" -and $last -eq "'")
            ) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        $existing = Get-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue
        if ($null -eq $existing) {
            Set-Item -Path ("Env:" + $name) -Value $value
        }
    }
}

function Get-FoodBackEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [string]$Default = "",

        [switch]$Required
    )

    $item = Get-Item -Path ("Env:" + $Name) -ErrorAction SilentlyContinue
    $value = if ($null -ne $item) { [string]$item.Value } else { $Default }

    if ($Required -and [string]::IsNullOrWhiteSpace($value)) {
        throw "Falta la variable requerida $Name. Revisa tu .env local."
    }

    return $value
}

function Resolve-PostgresTool {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    foreach ($candidateName in @($Name + ".exe", $Name)) {
        $command = Get-Command $candidateName -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }

    $roots = @()

    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $roots += (Join-Path $env:ProgramFiles "PostgreSQL")
    }

    $programFilesX86 = ${env:ProgramFiles(x86)}
    if (-not [string]::IsNullOrWhiteSpace($programFilesX86)) {
        $roots += (Join-Path $programFilesX86 "PostgreSQL")
    }

    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) {
            continue
        }

        $versions = Get-ChildItem -LiteralPath $root -Directory |
            Sort-Object {
                try {
                    [version]$_.Name
                }
                catch {
                    [version]"0.0"
                }
            } -Descending

        foreach ($versionDir in $versions) {
            $exePath = Join-Path $versionDir.FullName ("bin\" + $Name + ".exe")
            if (Test-Path -LiteralPath $exePath) {
                return $exePath
            }
        }
    }

    throw (
        "No se encontró $Name. Agrega PostgreSQL\\bin al PATH " +
        "o instala las herramientas cliente de PostgreSQL."
    )
}

function Convert-SecureStringToPlainText {
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString]$SecureString
    )

    return [System.Net.NetworkCredential]::new(
        "",
        $SecureString
    ).Password
}

function Get-FoodBackSecretOrPrompt {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvName,

        [Parameter(Mandatory = $true)]
        [string]$Prompt
    )

    $item = Get-Item -Path ("Env:" + $EnvName) -ErrorAction SilentlyContinue
    if (
        $null -ne $item -and
        -not [string]::IsNullOrWhiteSpace([string]$item.Value)
    ) {
        return [string]$item.Value
    }

    $secure = Read-Host $Prompt -AsSecureString
    try {
        return Convert-SecureStringToPlainText -SecureString $secure
    }
    finally {
        $secure = $null
    }
}

function Assert-SafePostgresIdentifier {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if ($Value -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "$Label contiene caracteres no permitidos: $Value"
    }
}

function Get-FoodBackBackupDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,

        [string]$Override = ""
    )

    $configured = $Override
    if ([string]::IsNullOrWhiteSpace($configured)) {
        $configured = Get-FoodBackEnvValue `
            -Name "FOODBACK_BACKUP_DIR" `
            -Default "backups/postgres"
    }

    if ([System.IO.Path]::IsPathRooted($configured)) {
        $resolved = $configured
    }
    else {
        $resolved = Join-Path $ProjectRoot $configured
    }

    if (-not (Test-Path -LiteralPath $resolved)) {
        New-Item -ItemType Directory -Path $resolved -Force | Out-Null
    }

    return (Resolve-Path -LiteralPath $resolved).Path
}

function Set-TemporaryPgPassword {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Password
    )

    $previous = Get-Item -Path "Env:PGPASSWORD" -ErrorAction SilentlyContinue
    $previousValue = if ($null -ne $previous) { [string]$previous.Value } else { $null }

    $env:PGPASSWORD = $Password
    return $previousValue
}

function Restore-TemporaryPgPassword {
    param(
        [AllowNull()]
        [string]$PreviousValue
    )

    if ($null -eq $PreviousValue) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    else {
        $env:PGPASSWORD = $PreviousValue
    }
}

function Get-FoodBackStatusDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $statusDir = Join-Path $ProjectRoot "backups\status"

    if (-not (Test-Path -LiteralPath $statusDir)) {
        New-Item -ItemType Directory -Path $statusDir -Force | Out-Null
    }

    return (Resolve-Path -LiteralPath $statusDir).Path
}

function Write-FoodBackJsonAtomic {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        $Data
    )

    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent) -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $tempPath = $Path + ".partial"

    try {
        $Data |
            ConvertTo-Json -Depth 8 |
            Set-Content -LiteralPath $tempPath -Encoding UTF8

        Move-Item -LiteralPath $tempPath -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-FoodBackPathInside {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ParentPath,

        [Parameter(Mandatory = $true)]
        [string]$ChildPath
    )

    $comparison = [System.StringComparison]::OrdinalIgnoreCase
    $separator = [System.IO.Path]::DirectorySeparatorChar

    $parentFull = [System.IO.Path]::GetFullPath($ParentPath).TrimEnd('\', '/')
    $childFull = [System.IO.Path]::GetFullPath($ChildPath).TrimEnd('\', '/')

    if ($childFull.Equals($parentFull, $comparison)) {
        return $true
    }

    return $childFull.StartsWith(
        $parentFull + $separator,
        $comparison
    )
}
