$script:PplidDefaultBaseDir = "C:\PPLID"

function Get-PplidMachineConfigCandidates {
    return @(
        (Join-Path $script:PplidDefaultBaseDir "machine.config.json")
        (Join-Path $env:ProgramData "PPLID\machine.config.json")
    )
}

function Get-PplidMachineConfigPath {
    foreach ($path in (Get-PplidMachineConfigCandidates)) {
        if (Test-Path $path) {
            return $path
        }
    }
    return (Get-PplidMachineConfigCandidates)[0]
}

function Get-LanIPv4 {
    param(
        [string]$Fallback = ""
    )

    $candidates = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.PrefixOrigin -ne "WellKnown" -and
            $_.IPAddress -notlike "169.254.*"
        } |
        Sort-Object -Property InterfaceMetric

    if ($candidates) {
        return $candidates[0].IPAddress
    }

    if ($Fallback) {
        return $Fallback
    }

    return "127.0.0.1"
}

function Get-PplidMachineConfig {
    $path = Get-PplidMachineConfigPath
    if (Test-Path $path) {
        return Get-Content $path -Raw | ConvertFrom-Json
    }

    return [PSCustomObject]@{
        baseDir = $script:PplidDefaultBaseDir
        lanIp   = $null
    }
}

function Get-PplidBaseDir {
    $cfg = Get-PplidMachineConfig
    if ($cfg.baseDir) {
        return $cfg.baseDir
    }
    return $script:PplidDefaultBaseDir
}

function Get-PplidReposDir {
    Join-Path (Get-PplidBaseDir) "repos"
}

function Get-PplidLogDir {
    Join-Path (Get-PplidBaseDir) "logs"
}

function Get-PplidStatusFile {
    Join-Path (Get-PplidLogDir) "deploy-status.json"
}

function Get-PplidRepoDir {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    Join-Path (Get-PplidReposDir) $Name
}

function Get-PplidOpsDir {
    param(
        [string]$ScriptRoot = $PSScriptRoot
    )

    $fromScript = Split-Path $ScriptRoot -Parent
    if (Test-Path (Join-Path $fromScript "lib\paths.ps1")) {
        return $fromScript
    }

    $installed = Join-Path (Get-PplidBaseDir) "ops"
    if (Test-Path (Join-Path $installed "lib\paths.ps1")) {
        return $installed
    }

    return $fromScript
}

function Import-PplidPathsModule {
    param(
        [string]$ScriptRoot = $PSScriptRoot
    )

    $opsDir = Get-PplidOpsDir -ScriptRoot $ScriptRoot
    $pathsFile = Join-Path $opsDir "lib\paths.ps1"
    if (Test-Path $pathsFile) {
        . $pathsFile
        return
    }

    throw "Modulo paths.ps1 nao encontrado. Execute setup.ps1 ou clone PPLID-server-ops em C:\PPLID\ops."
}

function Save-PplidMachineConfig {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Config
    )

    $baseDir = if ($Config.baseDir) { $Config.baseDir } else { $script:PplidDefaultBaseDir }
    $targetDir = Split-Path (Get-PplidMachineConfigCandidates)[0] -Parent
    if (-not (Test-Path $targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }

    $output = [ordered]@{
        baseDir = $baseDir
    }
    if ($Config.lanIp) {
        $output.lanIp = $Config.lanIp
    }

    $path = Join-Path $baseDir "machine.config.json"
    $json = $output | ConvertTo-Json -Depth 3
    Set-Content -Path $path -Value $json -Encoding UTF8
    return $path
}

function Initialize-PplidDirectories {
    param(
        [string]$BaseDir = (Get-PplidBaseDir)
    )

    foreach ($sub in @("repos", "logs", "ops")) {
        $dir = Join-Path $BaseDir $sub
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }
}

function Resolve-PplidDeployConfig {
    param(
        [Parameter(Mandatory = $true)]
        $RootConfig
    )

    $machine = Get-PplidMachineConfig
    $baseDir = if ($machine.baseDir) { $machine.baseDir } else { $script:PplidDefaultBaseDir }
    $reposDir = Join-Path $baseDir "repos"
    $logDir = Join-Path $baseDir "logs"

    if ($machine.lanIp -and -not $RootConfig.lanIp) {
        $RootConfig.lanIp = $machine.lanIp
    }

    $RootConfig | Add-Member -NotePropertyName logDir -NotePropertyValue $logDir -Force
    $RootConfig | Add-Member -NotePropertyName statusFile -NotePropertyValue (Join-Path $logDir "deploy-status.json") -Force

    foreach ($envName in @("MAIN", "DEV", "HOM")) {
        $envConfig = $RootConfig.$envName
        if (-not $envConfig) {
            continue
        }

        if (-not $envConfig.repoDir) {
            $repoName = if ($envConfig.repoName) { $envConfig.repoName } else { "PPLID_$envName" }
            $envConfig | Add-Member -NotePropertyName repoDir -NotePropertyValue (Join-Path $reposDir $repoName) -Force
        }
    }

    return $RootConfig
}
