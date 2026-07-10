param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [Parameter(Mandatory = $true)]
    [string]$TargetSha,
    [Parameter(Mandatory = $true)]
    [string]$TargetShaFull,
    [Parameter(Mandatory = $true)]
    [string]$RunId
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")
. (Join-Path $PSScriptRoot "lib\run_log.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_logging.ps1")
. (Join-Path $PSScriptRoot "lib\git_invoke.ps1")
. (Join-Path $PSScriptRoot "lib\backend_deploy.ps1")
. (Join-Path $PSScriptRoot "lib\deps_cache.ps1")
. (Join-Path $PSScriptRoot "lib\python_invoke.ps1")
. (Join-Path $PSScriptRoot "lib\release_cleanup.ps1")

$spec = Get-PplidEnvSpec -Environment $Environment
$paths = Get-PplidDeployEnvPaths -Environment $Environment
$releaseDir = Get-PplidReleaseDir -Environment $Environment -Sha $TargetSha
$metaFile = Join-Path $releaseDir "meta.json"
$logName = "build.log"
$script:CrossEnvArtifactsCopied = $false
$script:FrontendDistReady = $false

function LogInfo([string]$msg) { Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function LogOk([string]$msg) { Write-DeployLogOk -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function LogWarn([string]$msg) { Write-DeployLogWarn -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function LogErr([string]$msg) { Write-DeployLogError -Environment $Environment -RunId $RunId -Message $msg -LogName $logName }
function Log([string]$msg) { LogInfo $msg }

function Get-NpmOutputTail {
    param([string]$OutputFile, [int]$Tail = 20)
    if (-not (Test-Path $OutputFile)) { return @() }
    try {
        return @(Get-Content -Path $OutputFile -Tail $Tail -ErrorAction Stop)
    } catch {
        return @()
    }
}

function Strip-Ansi {
    param([string]$Text)
    if (-not $Text) { return $Text }
    return ($Text -replace '\x1b\[[0-9;]*m', '')
}

function Get-NpmErrorDetail {
    param([string[]]$Lines, [int]$Tail = 30)
    if (-not $Lines -or $Lines.Count -eq 0) { return @() }

    $clean = @($Lines | ForEach-Object { Strip-Ansi $_ } | Where-Object { $_ -and $_.Trim() })
    if ($clean.Count -eq 0) { return @() }

    $interesting = @($clean | Where-Object {
        $_ -match '(?i)error|failed|\[vite\]|\.vue|\.ts|unexpected|cannot find|rollup failed'
    })
    if ($interesting.Count -gt 0) {
        $start = [Math]::Max(0, $interesting.Count - $Tail)
        return @($interesting[$start..($interesting.Count - 1)])
    }

    $start = [Math]::Max(0, $clean.Count - $Tail)
    return @($clean[$start..($clean.Count - 1)])
}

function Invoke-NpmCommand {
    param(
        [Parameter(Mandatory = $true)][string[]]$NpmArgs,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    $outputFile = Join-Path $env:TEMP ("pplid-npm-{0}-{1}.log" -f $RunId, ([guid]::NewGuid().ToString("N").Substring(0, 8)))
    try {
        Remove-Item Env:npm_config_devdir -ErrorAction SilentlyContinue
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            & npm @NpmArgs 2>&1 | Tee-Object -FilePath $outputFile
            $npmCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $prevEap
        }
        if ($npmCode -ne 0) {
            $allLines = @()
            if (Test-Path $outputFile) {
                $allLines = @(Get-Content -Path $outputFile -ErrorAction SilentlyContinue)
            }
            $detailLines = Get-NpmErrorDetail -Lines $allLines -Tail 25
            foreach ($line in $detailLines) {
                if ($line) { LogErr (Strip-Ansi $line) }
            }
            $detail = ($detailLines | Where-Object { $_ }) -join "`n"
            if ($detail) {
                throw ("{0}`n{1}" -f $FailureMessage, $detail)
            }
            throw $FailureMessage
        }
    } finally {
        if (Test-Path $outputFile) {
            Remove-Item $outputFile -Force -ErrorAction SilentlyContinue
        }
    }
}

function Fail-BuildStep {
    param([string]$StepId, [string]$Message)
    $clean = Strip-Ansi $Message
    $truncated = $clean
    if ($truncated.Length -gt 2048) {
        $truncated = $truncated.Substring($truncated.Length - 2048)
    }
    Complete-DeployStep -Environment $Environment -RunId $RunId -StepId $StepId -Status "error" -ErrorMessage $truncated
    throw $clean
}

function Remove-ReleaseWorktree {
    param([string]$Dir)

    if (-not $Dir) { return }

    $releasesRoot = $paths.Releases
    $shaLeaf = Split-Path $Dir -Leaf
    $toRemove = @()
    if (Test-Path $Dir) {
        $toRemove += $Dir
    }

    foreach ($orphan in @(Get-ChildItem -Path $releasesRoot -Directory -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like "_wt_${shaLeaf}*" -or $_.Name -eq $shaLeaf
    })) {
        if ($toRemove -notcontains $orphan.FullName) {
            $toRemove += $orphan.FullName
        }
    }

    Push-Location $paths.Mirror
    try {
        $listLines = @(Invoke-PplidGit -Args @("worktree", "list", "--porcelain") -FailMessage "git worktree list falhou.")
        foreach ($line in $listLines) {
            if ($line -notmatch '^worktree\s+(.+)$') { continue }
            $currentPath = $Matches[1].Trim()
            $normPath = ($currentPath -replace '\\', '/').TrimEnd('/')
            $normReleases = ($releasesRoot -replace '\\', '/').TrimEnd('/')
            if ($normPath -notlike "$normReleases/*" -and $normPath -ne $normReleases) { continue }
            $leaf = Split-Path $currentPath -Leaf
            if ($leaf -eq $shaLeaf -or $leaf -like "_wt_${shaLeaf}*") {
                if ($toRemove -notcontains $currentPath) {
                    $toRemove += $currentPath
                }
            }
        }
    } catch {
        LogWarn "worktree list: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }

    foreach ($releasePath in @($toRemove | Select-Object -Unique)) {
        LogInfo "Limpando release/worktree: $releasePath"
        try {
            Remove-PplidReleaseDir -Environment $Environment -ReleaseDir $releasePath
        } catch {
            LogWarn "Remove-PplidReleaseDir: $($_.Exception.Message)"
        }
    }
}

LogInfo "Build iniciado para $TargetSha ($TargetShaFull)"

$state = Get-DeployState -Environment $Environment
$fromSha = [string]$state.activeSha
if (-not $fromSha) {
    $fromSha = [string]$state.lastGoodSha
}
$backendChanged = Test-PplidBackendChanged -MirrorDir $paths.Mirror -FromSha $fromSha -ToSha $TargetShaFull
$backendPaths = @()
if ($backendChanged) {
    $backendPaths = Get-PplidBackendDiffPaths -MirrorDir $paths.Mirror -FromSha $fromSha -ToSha $TargetShaFull
    LogInfo ("Backend alterado ($($backendPaths.Count) arquivos): " + ($backendPaths -join ", "))
}

if (Test-Path $metaFile) {
    $meta = Get-Content $metaFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $metaSha = [string]$meta.sha
    if ($meta.built -eq $true -and $metaSha -eq $TargetSha) {
        $fingerprint = Get-PplidDepsFingerprintFromDir -ReleaseDir $releaseDir
        $storedFp = [string]$meta.depsFingerprint
        if (-not $storedFp -and $fingerprint) {
            $storedFp = $fingerprint
            @{
                sha              = $meta.sha
                shaFull          = $meta.shaFull
                built            = $meta.built
                builtAt          = $meta.builtAt
                branch           = $meta.branch
                backendChanged   = $meta.backendChanged
                backendPaths     = @($meta.backendPaths)
                depsFingerprint  = $fingerprint
            } | ConvertTo-Json -Depth 4 | Set-Content -Path $metaFile -Encoding UTF8
        }
        $skipBuild = ($storedFp -and $fingerprint -eq $storedFp)
        if ($skipBuild) {
            if ($backendChanged) {
                LogInfo "Release $TargetSha ja construida; promote-only (backend mudou vs ativo, artifact OK)."
            } else {
                LogOk "Release $TargetSha ja construida, skip build."
            }
            Start-DeployStep -Environment $Environment -RunId $RunId -StepId "git_fetch"
            Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "git_fetch" -Status "skipped"
            Start-DeployStep -Environment $Environment -RunId $RunId -StepId "deps_backend"
            Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "deps_backend" -Status "skipped"
            Start-DeployStep -Environment $Environment -RunId $RunId -StepId "build_backend"
            Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "build_backend" -Status "skipped"
            Start-DeployStep -Environment $Environment -RunId $RunId -StepId "build_frontend"
            Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "build_frontend" -Status "skipped"
            return
        }
        LogWarn "Release $TargetSha built, mas fingerprint de deps mudou - rebuild."
    }
}

if (Test-Path $releaseDir) {
    LogInfo "Limpando release incompleta $TargetSha..."
    Remove-ReleaseWorktree -Dir $releaseDir
}

Start-DeployStep -Environment $Environment -RunId $RunId -StepId "git_fetch"
try {
    LogInfo "Iniciando git fetch..."
    Push-Location $paths.Mirror
    try {
        Invoke-PplidGit -Args @("fetch", "origin") -FailMessage "git fetch falhou." -OnLine {
            param($line)
            LogInfo ("git fetch origin: " + $line)
        }
        Invoke-PplidGit -Args @("worktree", "add", $releaseDir, $TargetShaFull) -FailMessage "git worktree add falhou." -OnLine {
            param($line)
            LogInfo ("git worktree add: " + $line)
        }
    } finally {
        Pop-Location
    }
    LogOk "git fetch/pull concluido"
    Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "git_fetch" -Status "success"
} catch {
    Fail-BuildStep -StepId "git_fetch" -Message $_.Exception.Message
}

LogInfo "Worktree criado em $releaseDir"

$promoteSource = [string]$env:PPLID_PROMOTE_SOURCE
if ($promoteSource -and $promoteSource -ne $Environment) {
  if (Copy-PplidCrossEnvArtifacts -SourceEnvironment $promoteSource -TargetSha $TargetSha -TargetReleaseDir $releaseDir) {
    $script:CrossEnvArtifactsCopied = $true
    LogOk "Artefatos copiados de $promoteSource (venv/dist)."
  }
}

$backendDir = Join-Path $releaseDir "backend"
$frontendDir = Join-Path $releaseDir "frontend"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
$depsFingerprint = Get-PplidDepsFingerprintFromDir -ReleaseDir $releaseDir
$script:FrontendDistReady = Test-Path (Join-Path $frontendDir "dist")

function Test-PplidVenvHealthy {
    param([Parameter(Mandatory = $true)][string]$VenvPython)
    if (-not (Test-Path $VenvPython)) { return $false }
    try {
        & $VenvPython -m pip --version *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Reset-PplidBackendVenv {
    param([Parameter(Mandatory = $true)][string]$BackendDir)
    $venvPath = Join-Path $BackendDir ".venv"
    if (Test-Path $venvPath) {
        LogWarn "Removendo venv corrompido/incompleto..."
        Remove-Item -LiteralPath $venvPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    LogInfo "Criando venv..."
    & python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar venv." }
}

function Throw-PipInstallFailure {
    param(
        [hashtable]$PipResult,
        [string]$FallbackMessage = "pip install falhou."
    )

    if ($PipResult.errorDetail) {
        Save-DeployErrorDetail -Environment $Environment -RunId $RunId -ErrorDetail $PipResult.errorDetail -FailedStep "deps_backend"
    }
    $msg = $FallbackMessage
    if ($PipResult.exitCode -eq 124) {
        $msg = "pip install excedeu o tempo limite (3600s)."
    } elseif ($PipResult.errorDetail -and $PipResult.errorDetail.message) {
        $msg = [string]$PipResult.errorDetail.message
    }
    $tail = @($PipResult.outputTail | Where-Object { $_ })
    if ($tail.Count -gt 0) {
        throw ("{0}`n{1}" -f $msg, ($tail -join "`n"))
    }
    throw $msg
}

Start-DeployStep -Environment $Environment -RunId $RunId -StepId "deps_backend"
try {
    Initialize-PplidPipCache | Out-Null
    $depsSkipped = $false

    if (-not (Test-Path $venvPython)) {
        $venvSource = Find-PplidVenvSourceForFingerprint -Environment $Environment -Fingerprint $depsFingerprint
        if ($venvSource) {
            LogInfo "Clonando venv de release anterior (deps inalteradas)..."
            if (Copy-PplidVenv -SourceVenv $venvSource -TargetVenv (Join-Path $backendDir ".venv")) {
                LogOk "venv_clone concluido."
            }
        }
        if (-not (Test-Path $venvPython)) {
            Reset-PplidBackendVenv -BackendDir $backendDir
        }
    }

    if ((Test-Path $venvPython) -and -not (Test-PplidVenvHealthy -VenvPython $venvPython)) {
        Reset-PplidBackendVenv -BackendDir $backendDir
    }

    if ((Test-Path $venvPython) -and ($script:CrossEnvArtifactsCopied -or (Find-PplidVenvSourceForFingerprint -Environment $Environment -Fingerprint $depsFingerprint))) {
        $srcFp = Find-PplidVenvSourceForFingerprint -Environment $Environment -Fingerprint $depsFingerprint
        if ($script:CrossEnvArtifactsCopied -or $srcFp) {
            LogOk "deps_skip: dependencias inalteradas, venv reutilizado."
            $depsSkipped = $true
        }
    }

    if (-not $depsSkipped) {
        LogInfo "pip_requirements: instalando dependencias do backend..."
        $pipResult = Invoke-PplidPipInstall -VenvPython $venvPython -Args @("-r", (Join-Path $backendDir "requirements.txt")) `
            -TimeoutSec 3600 -Environment $Environment -RunId $RunId -LogName $logName
        if ($pipResult.exitCode -ne 0) { Throw-PipInstallFailure -PipResult $pipResult }

        $automacoesDir = Join-Path $releaseDir "automacoes"
        if (Test-Path (Join-Path $automacoesDir "pyproject.toml")) {
            LogInfo "pip_automacoes: instalando automacoes (pip install -e)..."
            $pipResult = Invoke-PplidPipInstall -VenvPython $venvPython -Args @("-e", $automacoesDir) `
                -TimeoutSec 180 -Environment $Environment -RunId $RunId -LogName $logName
            if ($pipResult.exitCode -ne 0) { Throw-PipInstallFailure -PipResult $pipResult -FallbackMessage "pip install -e automacoes falhou." }
        } else {
            LogWarn "automacoes/ ausente na release; central de automacao nao funcionara."
        }
    }

    # Venv clonado mantem path absoluto do editable; sempre relink para esta release.
    if (Test-Path $venvPython) {
        $autoToml = Join-Path $releaseDir "automacoes\pyproject.toml"
        if (-not (Test-Path $autoToml)) {
            LogWarn "automacoes/ ausente na release; relink ignorado."
        } else {
            LogInfo "pip_automacoes: relink automacoes para release atual..."
            $relinkResult = Install-PplidAutomacoesEditable -ReleaseDir $releaseDir -VenvPython $venvPython `
                -TimeoutSec 180 -Environment $Environment -RunId $RunId -LogName $logName
            if ($relinkResult.exitCode -eq -1) {
                LogWarn "automacoes/ ausente na release; relink ignorado."
            } elseif ($relinkResult.exitCode -eq 124) {
                throw "pip install -e automacoes (relink) excedeu o tempo limite (180s)."
            } elseif ($relinkResult.exitCode -ne 0) {
                Throw-PipInstallFailure -PipResult $relinkResult -FallbackMessage "pip install -e automacoes (relink) falhou."
            } else {
                LogOk "pip_automacoes: relink concluido."
            }
        }
    }

    LogOk "Dependencias do backend instaladas"
    Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "deps_backend" -Status "success"
} catch {
    Fail-BuildStep -StepId "deps_backend" -Message $_.Exception.Message
}

Start-DeployStep -Environment $Environment -RunId $RunId -StepId "build_backend"
try {
    LogInfo "Executando manage.py check..."
    try {
        $checkLines = Invoke-PplidPython -Python $venvPython -Args @("manage.py", "check") `
            -WorkingDirectory $backendDir -FailMessage "manage.py check falhou."
        foreach ($line in @($checkLines)) {
            if ($line) { LogInfo "manage.py check: $line" }
        }
    } catch {
        LogErr $_.Exception.Message
        throw
    }

    $deployScript = Join-Path $spec.RepoDir "scripts\deploy"
    $repoBackendEnv = Join-Path $spec.RepoDir "backend\.env"
    $releaseBackendEnv = Join-Path $releaseDir "backend\.env"
    if (Test-Path $repoBackendEnv) {
        Copy-Item $repoBackendEnv $releaseBackendEnv -Force
        LogInfo "backend/.env copiado do repo."
    }
    $env:PPLID_APP_ROOT = $releaseDir
    LogInfo "Sincronizando .env para build..."
    & (Join-Path $deployScript "sync_env_files.ps1") -Environment $Environment
    if ($LASTEXITCODE -ne 0) { throw "sync_env_files falhou." }
    LogOk "Build backend concluido"
    Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "build_backend" -Status "success"
} catch {
    Fail-BuildStep -StepId "build_backend" -Message $_.Exception.Message
}

Start-DeployStep -Environment $Environment -RunId $RunId -StepId "build_frontend"
try {
    $dist = Join-Path $frontendDir "dist"
    if ($script:FrontendDistReady -and (Test-Path $dist)) {
        LogOk "frontend_skip: dist reutilizado (deps inalteradas)."
    } else {
        LogInfo "Executando build do frontend (npm)..."
        Push-Location $frontendDir
        try {
            if (Test-Path "package-lock.json") {
                Invoke-NpmCommand -NpmArgs @("ci") -FailureMessage "npm install falhou."
            } else {
                Invoke-NpmCommand -NpmArgs @("install") -FailureMessage "npm install falhou."
            }
            Invoke-NpmCommand -NpmArgs @("run", "build:deploy") -FailureMessage "npm run build:deploy falhou."
        } finally {
            Pop-Location
        }
    }

    if (-not (Test-Path $dist)) {
        throw "dist/ nao encontrado apos build."
    }
    LogOk "Build do frontend concluido"
    Complete-DeployStep -Environment $Environment -RunId $RunId -StepId "build_frontend" -Status "success"
} catch {
    Fail-BuildStep -StepId "build_frontend" -Message $_.Exception.Message
}

@{
    sha              = $TargetSha
    shaFull          = $TargetShaFull
    built            = $true
    builtAt          = (Get-Date).ToString("o")
    branch           = $spec.Branch
    backendChanged   = [bool]$backendChanged
    backendPaths     = @($backendPaths)
    depsFingerprint  = (Get-PplidDepsFingerprintFromDir -ReleaseDir $releaseDir)
} | ConvertTo-Json -Depth 4 | Set-Content -Path $metaFile -Encoding UTF8

LogOk "Build concluido."
