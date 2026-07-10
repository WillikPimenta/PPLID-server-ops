. (Join-Path $PSScriptRoot "deploy_paths.ps1")

function New-DeployRunId {
    return (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
}

function Initialize-DeployRun {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$RunId,
        [string]$Trigger = "manual",
        [string]$TargetSha = ""
    )

    $runDir = Get-PplidDeployRunDir -Environment $Environment -RunId $RunId
    if (-not (Test-Path $runDir)) {
        New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    }

    $manifest = @{
        runId     = $RunId
        environment = $Environment
        trigger   = $Trigger
        targetSha = $TargetSha
        pid       = $PID
        startedAt = (Get-Date).ToString("o")
    }
    $manifestPath = Join-Path $runDir "manifest.json"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding UTF8

    $latest = Join-Path (Get-PplidDeployEnvPaths -Environment $Environment).Logs "latest"
    if (Test-Path -LiteralPath $latest) {
        $item = Get-Item -LiteralPath $latest -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            cmd /c rmdir "$latest" 2>$null | Out-Null
        } else {
            Remove-Item -LiteralPath $latest -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    cmd /c mklink /J "$latest" "$runDir" 2>$null | Out-Null
    if (-not (Test-Path -LiteralPath $latest)) {
        New-Item -ItemType Junction -Path $latest -Target $runDir -Force -ErrorAction SilentlyContinue | Out-Null
    }

    return $runDir
}

function Write-RunLog {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$RunId,
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [string]$LogName = "pipeline.log"
    )

    $runDir = Get-PplidDeployRunDir -Environment $Environment -RunId $RunId
    if (-not (Test-Path $runDir)) {
        New-Item -ItemType Directory -Path $runDir -Force | Out-Null
    }
    $logFile = Join-Path $runDir $LogName
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$timestamp] $Message" -Encoding UTF8
}
