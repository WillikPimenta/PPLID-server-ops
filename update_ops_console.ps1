param(
    [switch]$CheckOnly,
    [switch]$Apply,
    [switch]$RestartOnly,
    [string]$OpsRepoDir = "",
    [string]$ConfigPath = "",
    [int]$DelaySeconds = 2
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

function Write-UpdateJson {
    param([hashtable]$Payload)
    Write-Output ($Payload | ConvertTo-Json -Compress -Depth 5)
}

function Invoke-GitInRepo {
    param(
        [string]$RepoDir,
        [string[]]$GitArgs
    )
    $output = & git -C $RepoDir @GitArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        $text = ($output | Out-String).Trim()
        throw "git $($GitArgs -join ' ') falhou: $text"
    }
    return ($output | Out-String).Trim()
}

function Get-LocalConsoleGitInfo {
    param([string]$RepoDir)

    if (-not (Test-Path (Join-Path $RepoDir ".git"))) {
        return @{
            ok = $false
            supported = $false
            reason = "Repositorio Git nao encontrado em $RepoDir"
        }
    }

    $branch = (Invoke-GitInRepo -RepoDir $RepoDir -GitArgs @("rev-parse", "--abbrev-ref", "HEAD")).Trim()
    $currentFull = (Invoke-GitInRepo -RepoDir $RepoDir -GitArgs @("rev-parse", "HEAD")).Trim()
    $currentShort = (Invoke-GitInRepo -RepoDir $RepoDir -GitArgs @("rev-parse", "--short", "HEAD")).Trim()
    $dirty = [bool](Invoke-GitInRepo -RepoDir $RepoDir -GitArgs @("status", "--porcelain"))

    return @{
        ok = $true
        supported = $true
        dirty = $dirty
        branch = $branch
        currentSha = $currentShort
        currentShaFull = $currentFull
    }
}

function Get-ConsoleUpdateStatus {
    param([string]$RepoDir)

    $local = Get-LocalConsoleGitInfo -RepoDir $RepoDir
    if (-not $local.ok) {
        return $local
    }

    if ($local.dirty) {
        return @{
            ok = $false
            supported = $true
            dirty = $true
            branch = $local.branch
            currentSha = $local.currentSha
            currentShaFull = $local.currentShaFull
            reason = "Working tree com alteracoes locais. Resolva manualmente antes de atualizar."
        }
    }

    try {
        Invoke-GitInRepo -RepoDir $RepoDir -GitArgs @("fetch", "origin") | Out-Null
    } catch {
        return @{
            ok = $false
            supported = $true
            dirty = $false
            branch = $local.branch
            currentSha = $local.currentSha
            currentShaFull = $local.currentShaFull
            reason = $_.Exception.Message
        }
    }

    $remoteRef = ""
    try {
        $remoteRef = (Invoke-GitInRepo -RepoDir $RepoDir -GitArgs @("rev-parse", "@{u}")).Trim()
    } catch {
        return @{
            ok = $false
            supported = $true
            dirty = $false
            branch = $local.branch
            currentSha = $local.currentSha
            currentShaFull = $local.currentShaFull
            reason = "Branch sem upstream configurado (git branch -u origin/$($local.branch))."
        }
    }

    $remoteShort = (Invoke-GitInRepo -RepoDir $RepoDir -GitArgs @("rev-parse", "--short", $remoteRef)).Trim()
    $updateAvailable = $local.currentShaFull -ne $remoteRef
    $commitsBehind = 0
    if ($updateAvailable) {
        try {
            $countText = (Invoke-GitInRepo -RepoDir $RepoDir -GitArgs @("rev-list", "--count", "HEAD..@{u}")).Trim()
            if ($countText -match '^\d+$') {
                $commitsBehind = [int]$countText
            }
        } catch {
            $commitsBehind = 0
        }
    }

    return @{
        ok = $true
        supported = $true
        dirty = $false
        branch = $local.branch
        currentSha = $local.currentSha
        currentShaFull = $local.currentShaFull
        remoteSha = $remoteShort
        remoteShaFull = $remoteRef
        updateAvailable = [bool]$updateAvailable
        commitsBehind = $commitsBehind
    }
}

if (-not $OpsRepoDir) {
    $OpsRepoDir = Get-PplidOpsDir -ScriptRoot $PSScriptRoot
}
$OpsRepoDir = (Resolve-Path $OpsRepoDir).Path

if (-not $ConfigPath) {
    $ConfigPath = Get-PplidEnvConfigPath -ScriptRoot $PSScriptRoot
}

$opsConsoleDir = Get-PplidOpsConsoleDir -ScriptRoot $PSScriptRoot
$lockPath = Join-Path (Get-PplidLogDir) "console-update.lock"

function Clear-ConsoleUpdateLock {
    if (Test-Path $lockPath) {
        Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
    }
}

if ($CheckOnly) {
    try {
        $status = Get-ConsoleUpdateStatus -RepoDir $OpsRepoDir
        Write-UpdateJson -Payload $status
        if (-not $status.ok) { exit 1 }
        exit 0
    } catch {
        Write-UpdateJson -Payload @{
            ok = $false
            supported = $true
            reason = $_.Exception.Message
        }
        exit 1
    }
}

if ($RestartOnly) {
    if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }
    $restartArgs = @("-Restart")
    if ($ConfigPath -and (Test-Path $ConfigPath)) {
        $restartArgs += @("-ConfigPath", $ConfigPath)
    }
    & (Join-Path $PSScriptRoot "start_ops_console.ps1") @restartArgs
    exit $LASTEXITCODE
}

if ($Apply) {
    try {
    $status = Get-ConsoleUpdateStatus -RepoDir $OpsRepoDir
    if (-not $status.ok) {
        Clear-ConsoleUpdateLock
        Write-UpdateJson -Payload $status
        exit 1
    }
    if (-not $status.updateAvailable) {
        Clear-ConsoleUpdateLock
        Write-UpdateJson -Payload (@{
            ok = $true
            applied = $false
            restarting = $false
            message = "Console ja esta atualizado."
        } + $status)
        exit 0
    }

    $previousSha = $status.currentSha
    $targetSha = $status.remoteSha
    $requirements = Join-Path $opsConsoleDir "requirements.txt"
    $requirementsBefore = $null
    if (Test-Path $requirements) {
        $requirementsBefore = Get-FileHash $requirements -Algorithm SHA256
    }

    try {
        Invoke-GitInRepo -RepoDir $OpsRepoDir -GitArgs @("pull", "--ff-only", "origin", $status.branch) | Out-Null
    } catch {
        Clear-ConsoleUpdateLock
        Write-UpdateJson -Payload @{
            ok = $false
            applied = $false
            restarting = $false
            error = $_.Exception.Message
            previousSha = $previousSha
            targetSha = $targetSha
        }
        exit 1
    }

    $newStatus = Get-ConsoleUpdateStatus -RepoDir $OpsRepoDir
    $requirementsAfter = $null
    if (Test-Path $requirements) {
        $requirementsAfter = Get-FileHash $requirements -Algorithm SHA256
    }
    $depsChanged = $requirementsBefore -and $requirementsAfter -and ($requirementsBefore.Hash -ne $requirementsAfter.Hash)

    if ($depsChanged) {
        $venvPython = Join-Path $opsConsoleDir ".venv\Scripts\python.exe"
        if (Test-Path $venvPython) {
            & $venvPython -m pip install --disable-pip-version-check -r $requirements
            if ($LASTEXITCODE -ne 0) {
                Clear-ConsoleUpdateLock
                Write-UpdateJson -Payload @{
                    ok = $false
                    applied = $true
                    restarting = $false
                    error = "git pull ok, mas pip install falhou."
                    previousSha = $previousSha
                    targetSha = $newStatus.currentSha
                }
                exit 1
            }
        }
    }

    Clear-ConsoleUpdateLock
    Write-UpdateJson -Payload @{
        ok = $true
        applied = $true
        restarting = $true
        previousSha = $previousSha
        targetSha = $newStatus.currentSha
        branch = $newStatus.branch
        depsChanged = [bool]$depsChanged
    }

    $workerArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $PSScriptRoot "update_ops_console.ps1"),
        "-RestartOnly",
        "-OpsRepoDir", $OpsRepoDir,
        "-DelaySeconds", "2"
    )
    if ($ConfigPath -and (Test-Path $ConfigPath)) {
        $workerArgs += @("-ConfigPath", $ConfigPath)
    }

    Start-Process -FilePath "powershell.exe" -ArgumentList $workerArgs -WindowStyle Hidden
    exit 0
    } catch {
        Clear-ConsoleUpdateLock
        Write-UpdateJson -Payload @{
            ok = $false
            applied = $false
            restarting = $false
            error = $_.Exception.Message
        }
        exit 1
    }
}

Write-Error "Informe -CheckOnly, -Apply ou -RestartOnly."
exit 1
