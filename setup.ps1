<#
.SYNOPSIS
    Bootstrap inicial do servidor PPLID em C:\PPLID.

.DESCRIPTION
    Cria diretorios, machine.config.json, copia/clona ops e repositorios PPLID.
    Execute como Administrador para registrar tasks como SYSTEM.
#>
param(
    [string]$BaseDir = "C:\PPLID",
    [string]$LanIp = "",
    [string]$RepoUrl = "https://github.com/WillikPimenta/PPLID.git",
    [string]$OpsRepoUrl = "",
    [string]$LegacyReposDir = "",
    [string]$LegacyLogsDir = "",
    [switch]$MigrateLegacy,
    [switch]$SkipClone,
    [switch]$SkipTasks,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$script:PplidDefaultBaseDir = $BaseDir
. (Join-Path $PSScriptRoot "lib\paths.ps1")

function Test-IsAdministrator {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Copy-TreeIfMissing {
    param(
        [string]$Source,
        [string]$Destination
    )

    if (-not (Test-Path $Source)) {
        return $false
    }

    if ((Test-Path $Destination) -and -not $Force) {
        Write-Host "  Ja existe: $Destination (use -Force para sobrescrever)"
        return $true
    }

    Write-Host "  Copiando $Source -> $Destination"
    if (Test-Path $Destination) {
        Remove-Item $Destination -Recurse -Force
    }
    Copy-Item -Path $Source -Destination $Destination -Recurse -Force
    return $true
}

Write-Host "=== PPLID Server Setup ==="
Write-Host "Base: $BaseDir"

Initialize-PplidDirectories -BaseDir $BaseDir

if (-not $LanIp) {
    $LanIp = Get-LanIPv4
    Write-Host "LAN IP detectado: $LanIp"
}

Save-PplidMachineConfig -Config @{
    baseDir = $BaseDir
    lanIp   = $LanIp
}

$opsTarget = Join-Path $BaseDir "ops"
$reposDir = Join-Path $BaseDir "repos"
$logsDir = Join-Path $BaseDir "logs"

if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}

# Instala scripts ops (copia desta pasta se nao for clone remoto)
if ($OpsRepoUrl) {
    if (-not (Test-Path (Join-Path $opsTarget ".git"))) {
        Write-Host "Clonando PPLID-server-ops..."
        git clone $OpsRepoUrl $opsTarget
    } else {
        Push-Location $opsTarget
        git pull
        Pop-Location
    }
} else {
    Write-Host "Copiando scripts ops locais..."
    Copy-TreeIfMissing -Source $PSScriptRoot -Destination $opsTarget
}

# Detecta legado
if (-not $LegacyReposDir) {
    $candidate = Join-Path $env:USERPROFILE "repos"
    if ((Test-Path $candidate) -and ($candidate -ne $reposDir)) {
        $LegacyReposDir = $candidate
    }
}
if (-not $LegacyLogsDir) {
    $candidate = Join-Path $env:USERPROFILE "logs"
    if ((Test-Path $candidate) -and ($candidate -ne $logsDir)) {
        $LegacyLogsDir = $candidate
    }
}

if ($MigrateLegacy -or ($LegacyReposDir -and (Test-Path $LegacyReposDir))) {
    Write-Host "Migrando repositorios legados de $LegacyReposDir..."
    foreach ($name in @("PPLID_MAIN", "PPLID_DEV", "PPLID_HOM")) {
        $src = Join-Path $LegacyReposDir $name
        $dst = Join-Path $reposDir $name
        Copy-TreeIfMissing -Source $src -Destination $dst
    }
}

if ($LegacyLogsDir -and (Test-Path $LegacyLogsDir) -and ($MigrateLegacy -or -not (Test-Path (Join-Path $logsDir "deploy-status.json")))) {
    Write-Host "Migrando logs de $LegacyLogsDir..."
    Get-ChildItem $LegacyLogsDir -File | ForEach-Object {
        $target = Join-Path $logsDir $_.Name
        if (-not (Test-Path $target) -or $Force) {
            Copy-Item $_.FullName $target -Force
        }
    }
}

if (-not $SkipClone) {
    $envBranches = @{
        PPLID_MAIN = "main"
        PPLID_DEV  = "dev"
        PPLID_HOM  = "hom"
    }

    foreach ($entry in $envBranches.GetEnumerator()) {
        $repoPath = Join-Path $reposDir $entry.Key
        if (-not (Test-Path (Join-Path $repoPath ".git"))) {
            Write-Host "Clonando $($entry.Key) (branch $($entry.Value))..."
            Push-Location $reposDir
            git clone -b $entry.Value $RepoUrl $entry.Key
            Pop-Location
        }
    }
}

if (-not $SkipTasks) {
    $opsScripts = Join-Path $opsTarget "install_scheduled_task.ps1"
    $consoleTask = Join-Path $opsTarget "install_ops_console_task.ps1"

    if (Test-IsAdministrator) {
        Write-Host "Registrando tasks (conta SYSTEM)..."
        & $opsScripts -IntervalMinutes 1
        & $consoleTask
    } else {
        Write-Warning "Sem privilegio de administrador: tasks nao registradas."
        Write-Host "Execute como Admin:"
        Write-Host "  powershell -File `"$opsScripts`""
        Write-Host "  powershell -File `"$consoleTask`""
    }
}

Write-Host ""
Write-Host "Setup concluido."
Write-Host "  Base:     $BaseDir"
Write-Host "  Repos:    $reposDir"
Write-Host "  Logs:     $logsDir"
Write-Host "  Ops:      $opsTarget"
Write-Host "  Config:   $(Join-Path $BaseDir 'machine.config.json')"
Write-Host ""
Write-Host "Comandos uteis:"
Write-Host "  powershell -File `"$(Join-Path $opsTarget 'start_ops_console.ps1')`""
Write-Host "  powershell -File `"$(Join-Path $opsTarget 'deploy_all.ps1')`""
Write-Host "  powershell -File `"$(Join-Path $opsTarget 'update_all.ps1')`""
