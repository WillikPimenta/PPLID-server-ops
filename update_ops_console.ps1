param(
    [switch]$CheckOnly,
    [switch]$Apply,
    [switch]$RestartOnly,
    [string]$OpsRepoDir = "",
    [string]$ConfigPath = "",
    [int]$DelaySeconds = 2
)

$ErrorActionPreference = "Stop"
$env:GIT_TERMINAL_PROMPT = "0"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

function Write-UpdateJson {
    param([hashtable]$Payload)
    Write-Output ($Payload | ConvertTo-Json -Compress -Depth 5)
}

function Write-ConsoleUpdateResult {
    param(
        [hashtable]$Payload,
        [string]$ResultPath
    )
    if (-not $Payload.ContainsKey("finishedAt") -or -not $Payload["finishedAt"]) {
        $Payload["finishedAt"] = (Get-Date).ToUniversalTime().ToString("o")
    }
    $json = $Payload | ConvertTo-Json -Compress -Depth 5
    if ($ResultPath) {
        $dir = Split-Path -Parent $ResultPath
        if ($dir -and -not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Set-Content -Path $ResultPath -Value $json -Encoding UTF8
    }
    Write-Output $json
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
$resultPath = Join-Path (Get-PplidLogDir) "console-update.result.json"
$logPath = Join-Path (Get-PplidLogDir) "console-update.log"

function Write-ConsoleUpdateLog {
    param(
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level = "INFO",
        [string]$Phase = ""
    )
    try {
        $phaseText = if ($Phase) { " [$Phase]" } else { "" }
        $line = "[{0}] [{1}]{2} {3}" -f ((Get-Date).ToUniversalTime().ToString("o")), $Level, $phaseText, $Message
        Add-Content -Path $logPath -Value $line -Encoding UTF8
    } catch {
        # O log e auxiliar e nunca pode interromper o update.
    }
}

function Clear-ConsoleUpdateLock {
    if (Test-Path $lockPath) {
        Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
    }
}

function Write-ApplyResult {
    param([hashtable]$Payload)
    Write-ConsoleUpdateResult -Payload $Payload -ResultPath $resultPath
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
    # O servidor grava "started" antes de criar o worker. Registre a entrada
    # do worker antes da segunda consulta ao Git para que o painel não pareça
    # congelado caso fetch/status demore na máquina destino.
    $workerStartedAt = (Get-Date).ToUniversalTime().ToString("o")
    Write-ConsoleUpdateLog -Message "Worker iniciado; verificando o repositorio Git." -Phase "pulling"
    $previousResult = $null
    if (Test-Path $resultPath) {
        try { $previousResult = Get-Content $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $previousResult = $null }
    }
    Write-ApplyResult -Payload @{
        ok = $true
        accepted = $true
        applied = $false
        restarting = $false
        phase = "pulling"
        previousSha = if ($previousResult) { [string]$previousResult.previousSha } else { "" }
        targetSha = if ($previousResult) { [string]$previousResult.targetSha } else { "" }
        branch = if ($previousResult) { [string]$previousResult.branch } else { "" }
        startedAt = if ($previousResult -and $previousResult.startedAt) { [string]$previousResult.startedAt } else { $workerStartedAt }
        workerStartedAt = $workerStartedAt
        message = "Worker de atualizacao iniciado; verificando Git."
    }
    $status = Get-ConsoleUpdateStatus -RepoDir $OpsRepoDir
    if (-not $status.ok) {
        Write-ConsoleUpdateLog -Message ([string]($status.reason)) -Level "ERROR" -Phase "failed"
        Clear-ConsoleUpdateLock
        Write-ApplyResult -Payload $status
        exit 1
    }
    if (-not $status.updateAvailable) {
        Write-ConsoleUpdateLog -Message "Nenhuma atualização pendente." -Phase "done"
        Clear-ConsoleUpdateLock
        Write-ApplyResult -Payload (@{
            ok = $true
            applied = $false
            restarting = $false
            phase = "done"
            message = "Console ja esta atualizado."
        } + $status)
        exit 0
    }

    $previousSha = $status.currentSha
    $targetSha = $status.remoteSha
    $startedAt = (Get-Date).ToUniversalTime().ToString("o")
    Write-ApplyResult -Payload @{
        ok = $true
        accepted = $true
        applied = $false
        restarting = $false
        phase = "pulling"
        previousSha = $previousSha
        targetSha = $targetSha
        branch = $status.branch
        startedAt = $startedAt
    }
    $requirements = Join-Path $opsConsoleDir "requirements.txt"
    $nativeBackendReq = Join-Path $opsConsoleDir "automation-native\backend\requirements.txt"
    $nativeBotsReq = Join-Path $opsConsoleDir "automation-native\automacoes\requirements.txt"
    $requirementsBefore = $null
    if (Test-Path $requirements) {
        $requirementsBefore = Get-FileHash $requirements -Algorithm SHA256
    }
    $nativeBefore = @()
    foreach ($reqPath in @($nativeBackendReq, $nativeBotsReq)) {
        if (Test-Path $reqPath) {
            $nativeBefore += (Get-FileHash $reqPath -Algorithm SHA256).Hash
        }
    }

    try {
        Write-ConsoleUpdateLog -Message ("Executando git pull de origin/{0}." -f $status.branch) -Phase "pulling"
        Invoke-GitInRepo -RepoDir $OpsRepoDir -GitArgs @("pull", "--ff-only", "origin", $status.branch) | Out-Null
        Write-ConsoleUpdateLog -Message "git pull concluído." -Phase "dependencies"
    } catch {
        Write-ConsoleUpdateLog -Message $_.Exception.Message -Level "ERROR" -Phase "failed"
        Clear-ConsoleUpdateLock
        Write-ApplyResult -Payload @{
            ok = $false
            applied = $false
            restarting = $false
            phase = "failed"
            error = $_.Exception.Message
            previousSha = $previousSha
            targetSha = $targetSha
        }
        exit 1
    }

    $newStatus = Get-ConsoleUpdateStatus -RepoDir $OpsRepoDir
    Write-ApplyResult -Payload @{
        ok = $true
        accepted = $true
        applied = $true
        restarting = $false
        phase = "dependencies"
        previousSha = $previousSha
        targetSha = $newStatus.currentSha
        branch = $newStatus.branch
        startedAt = $startedAt
    }
    $requirementsAfter = $null
    if (Test-Path $requirements) {
        $requirementsAfter = Get-FileHash $requirements -Algorithm SHA256
    }
    $depsChanged = $requirementsBefore -and $requirementsAfter -and ($requirementsBefore.Hash -ne $requirementsAfter.Hash)
    $nativeAfter = @()
    foreach ($reqPath in @($nativeBackendReq, $nativeBotsReq)) {
        if (Test-Path $reqPath) {
            $nativeAfter += (Get-FileHash $reqPath -Algorithm SHA256).Hash
        }
    }
    $automationDepsChanged = ($nativeBefore -join "|") -ne ($nativeAfter -join "|")

    if ($depsChanged) {
        Write-ConsoleUpdateLog -Message "Alteração de dependências detectada; instalando requirements." -Phase "dependencies"
        $venvPython = Join-Path $opsConsoleDir ".venv\Scripts\python.exe"
        if (Test-Path $venvPython) {
            & $venvPython -m pip install --disable-pip-version-check -r $requirements
            if ($LASTEXITCODE -ne 0) {
                Write-ConsoleUpdateLog -Message "pip install falhou." -Level "ERROR" -Phase "failed"
                Clear-ConsoleUpdateLock
                Write-ApplyResult -Payload @{
                    ok = $false
                    applied = $true
                    restarting = $false
                    phase = "failed"
                    error = "git pull ok, mas pip install falhou."
                    previousSha = $previousSha
                    targetSha = $newStatus.currentSha
                }
                exit 1
            }
        }
    }

    if ($automationDepsChanged -or $depsChanged) {
        Write-ConsoleUpdateLog -Message "Atualizando runtime das automações." -Phase "automation_runtime"
        Write-ApplyResult -Payload @{
            ok = $true
            accepted = $true
            applied = $true
            restarting = $false
            phase = "automation_runtime"
            previousSha = $previousSha
            targetSha = $newStatus.currentSha
            branch = $newStatus.branch
            startedAt = $startedAt
        }
        $venvPython = Join-Path $opsConsoleDir ".venv\Scripts\python.exe"
        $bootstrap = Join-Path $opsConsoleDir "tools\bootstrap_automation_runtime.py"
        if ((Test-Path $venvPython) -and (Test-Path $bootstrap)) {
            $cfg = if ($ConfigPath) { $ConfigPath } else { "" }
            if ($cfg) {
                & $venvPython $bootstrap $cfg --force
            } else {
                & $venvPython $bootstrap --force
            }
            if ($LASTEXITCODE -ne 0) {
                Write-ConsoleUpdateLog -Message "Bootstrap do runtime das automações falhou." -Level "ERROR" -Phase "failed"
                Clear-ConsoleUpdateLock
                Write-ApplyResult -Payload @{
                    ok = $false
                    applied = $true
                    restarting = $false
                    phase = "failed"
                    error = "git pull ok, mas bootstrap do runtime de automacoes falhou."
                    previousSha = $previousSha
                    targetSha = $newStatus.currentSha
                }
                exit 1
            }
        }
    }

    Write-ApplyResult -Payload @{
        ok = $true
        accepted = $true
        applied = $true
        restarting = $true
        phase = "restarting"
        previousSha = $previousSha
        targetSha = $newStatus.currentSha
        branch = $newStatus.branch
        startedAt = $startedAt
    }
    Clear-ConsoleUpdateLock
    Write-ApplyResult -Payload @{
        ok = $true
        applied = $true
        restarting = $true
        phase = "restarting"
        previousSha = $previousSha
        targetSha = $newStatus.currentSha
        branch = $newStatus.branch
        depsChanged = [bool]$depsChanged
        automationDepsChanged = [bool]$automationDepsChanged
    }
    Write-ConsoleUpdateLog -Message "Código aplicado; solicitando reinício do Console." -Phase "restarting"

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
        Write-ConsoleUpdateLog -Message $_.Exception.Message -Level "ERROR" -Phase "failed"
        Clear-ConsoleUpdateLock
        Write-ApplyResult -Payload @{
            ok = $false
            applied = $false
            restarting = $false
            phase = "failed"
            error = $_.Exception.Message
        }
        exit 1
    }
}

Write-Error "Informe -CheckOnly, -Apply ou -RestartOnly."
exit 1
