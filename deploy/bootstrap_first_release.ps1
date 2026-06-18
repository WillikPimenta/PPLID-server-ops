param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [string]$Sha = "",
    [string]$ShaFull = ""
)

$ErrorActionPreference = "Stop"
$opsRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $opsRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_paths.ps1")
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path $PSScriptRoot "lib\env_spec.ps1")
. (Join-Path $PSScriptRoot "lib\junction.ps1")
. (Join-Path $PSScriptRoot "lib\git_invoke.ps1")
. (Join-Path $opsRoot "lib\version_drift.ps1")
. (Join-Path $PSScriptRoot "lib\python_invoke.ps1")

Initialize-PplidGitSafeDirectories
Initialize-PplidDeployLayout -Environment $Environment

$spec = Get-PplidEnvSpec -Environment $Environment
$paths = Get-PplidDeployEnvPaths -Environment $Environment
$logFile = Join-Path (Get-PplidLogDir) "PPLID_$Environment.log"

function Invoke-PplidDeployScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [hashtable]$Arguments = @{}
    )

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $ScriptPath @Arguments 2>&1 | Out-Null
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }

    if ($code -ne 0) {
        throw "Script falhou (exit $code): $ScriptPath"
    }
}

function Invoke-EnsureDatabaseSafe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DeployScript,
        [Parameter(Mandatory = $true)]
        [string]$AppRoot
    )

    $backendDir = Join-Path $AppRoot "backend"
    $venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
    $ensureScript = Join-Path $DeployScript "ensure_database.py"
    if (-not (Test-Path $venvPython)) {
        throw "venv nao encontrado para ensure_database."
    }

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $out = & $venvPython $ensureScript $backendDir 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }

    if ($code -ne 0) {
        $detail = if ($out) { ($out | Out-String).Trim() } else { "" }
        if ($detail) {
            throw "ensure_database falhou: $detail"
        }
        throw "ensure_database falhou."
    }

    Log ("Banco: " + (($out | Out-String).Trim()))
}

function Log([string]$msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$ts] [bootstrap_first_release] $msg" -Encoding UTF8
    Write-Host $msg
}

$deployedFile = Join-Path (Get-PplidLogDir) "PPLID_$Environment.deployed.json"
$targetSha = $Sha.Trim()
$targetShaFull = $ShaFull.Trim()

if (-not $targetSha) {
    $targetSha = Get-PplidDeployedSha -Environment $Environment
}

if (-not $targetSha) {
    throw "Nenhum SHA implantado encontrado para $Environment."
}

if (-not $targetShaFull) {
    $status = Get-PplidDeployStatusEnvironment -Environment $Environment
    if ($status -and $status.deployedShaFull) {
        $targetShaFull = [string]$status.deployedShaFull
    } elseif (Test-Path $deployedFile) {
        $payload = Get-Content $deployedFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $targetShaFull = [string]$payload.shaFull
    }
}

if (-not $targetShaFull) {
    Push-Location $paths.Mirror
    try {
        if (-not (Test-Path (Join-Path $paths.Mirror ".git"))) {
            throw "Mirror ausente em $($paths.Mirror)."
        }
        Invoke-PplidGit -Args @("fetch", "origin") -FailMessage "git fetch falhou." | Out-Null
        $revLines = Invoke-PplidGit -Args @("rev-parse", $targetSha) -FailMessage "git rev-parse falhou."
        $targetShaFull = ($revLines -join "`n").Trim()
    } finally {
        Pop-Location
    }
}

$releaseDir = Get-PplidReleaseDir -Environment $Environment -Sha $targetSha
$metaFile = Join-Path $releaseDir "meta.json"
$deployScript = Join-Path $spec.RepoDir "scripts\deploy"

Log "Materializando release $targetSha para $Environment"

if (-not (Test-Path (Join-Path $paths.Mirror ".git"))) {
    Log "Clonando mirror $($spec.Branch)..."
    $repoUrl = "https://github.com/WillikPimenta/PPLID.git"
    Invoke-PplidGit -Args @("clone", "-b", $spec.Branch, $repoUrl, $paths.Mirror) -FailMessage "git clone falhou." | Out-Null
}

if (-not (Test-Path $releaseDir)) {
    Push-Location $paths.Mirror
    try {
        Invoke-PplidGit -Args @("fetch", "origin") -FailMessage "git fetch falhou." | Out-Null
        Invoke-PplidGit -Args @("worktree", "add", $releaseDir, $targetShaFull) -FailMessage "git worktree add falhou." | Out-Null
    } finally {
        Pop-Location
    }
    Log "Worktree criado em $releaseDir"
}

$venvPython = Join-Path $releaseDir "backend\.venv\Scripts\python.exe"
$backendDir = Join-Path $releaseDir "backend"
$frontendDir = Join-Path $releaseDir "frontend"
$frontendDist = Join-Path $frontendDir "dist"

if (-not (Test-Path $venvPython)) {
    Log "Criando venv..."
    & python -m venv (Join-Path $backendDir ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar venv." }
}

if (-not (Test-Path $venvPython)) {
    throw "venv ausente apos criacao."
}

if (-not (Test-Path (Join-Path $backendDir ".venv\Scripts\waitress-serve.exe"))) {
    Log "pip install backend..."
    & $venvPython -m pip install -r (Join-Path $backendDir "requirements.txt") --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install falhou." }

    $automacoesDir = Join-Path $releaseDir "automacoes"
    if (Test-Path (Join-Path $automacoesDir "pyproject.toml")) {
        Log "pip install -e automacoes..."
        & $venvPython -m pip install -e $automacoesDir --quiet
        if ($LASTEXITCODE -ne 0) { throw "pip install -e automacoes falhou." }
    } else {
        Log "AVISO: automacoes/ ausente na release; central de automacao nao funcionara."
    }
}

if (-not (Test-Path $metaFile)) {
    @{
        sha     = $targetSha
        shaFull = $targetShaFull
        built   = $true
        builtAt = (Get-Date).ToString("o")
        branch  = $spec.Branch
        source  = "bootstrap_first_release"
    } | ConvertTo-Json | Set-Content -Path $metaFile -Encoding UTF8
}

if (-not (Test-Path $paths.Current)) {
    Set-DirectoryJunction -LinkPath $paths.Current -TargetPath $releaseDir
    Log "current junction -> $targetSha"
} else {
    Set-DirectoryJunction -LinkPath $paths.Current -TargetPath $releaseDir
    Log "current junction atualizado -> $targetSha"
}

$env:PPLID_APP_ROOT = $paths.Current
$repoBackendEnv = Join-Path $spec.RepoDir "backend\.env"
$releaseBackendEnv = Join-Path $releaseDir "backend\.env"
if (Test-Path $repoBackendEnv) {
    Copy-Item $repoBackendEnv $releaseBackendEnv -Force
    Log "backend/.env copiado do repo."
}

Log "Sincronizando .env..."
Invoke-PplidDeployScript -ScriptPath (Join-Path $deployScript "sync_env_files.ps1") -Arguments @{ Environment = $Environment }
Invoke-EnsureDatabaseSafe -DeployScript $deployScript -AppRoot $releaseDir

Push-Location $backendDir
try {
    Log "migrate..."
    Invoke-PplidPython -Python $venvPython -Args @("manage.py", "migrate", "--noinput", "--skip-checks") -FailMessage "migrate falhou."
    Log "collectstatic..."
    Invoke-PplidPython -Python $venvPython -Args @("manage.py", "collectstatic", "--noinput", "--skip-checks") -FailMessage "collectstatic falhou."
} finally {
    Pop-Location
}

if (-not (Test-Path $frontendDist)) {
    Log "npm build frontend..."
    Push-Location $frontendDir
    try {
        if (Test-Path "package-lock.json") { npm ci } else { npm install }
        if ($LASTEXITCODE -ne 0) { throw "npm install falhou." }
        npm run build:deploy
        if ($LASTEXITCODE -ne 0) { throw "npm run build:deploy falhou." }
    } finally {
        Pop-Location
    }
}

Invoke-PplidDeployScript -ScriptPath (Join-Path $deployScript "stop_env.ps1") -Arguments @{ Environment = $Environment }
Invoke-PplidDeployScript -ScriptPath (Join-Path $deployScript "start_env.ps1") -Arguments @{ Environment = $Environment }

Log "Aguardando servicos apos start..."
$healthScript = Join-Path $deployScript "health_check.ps1"
$healthy = $false
for ($attempt = 1; $attempt -le 8; $attempt++) {
    Start-Sleep -Seconds 10
    try {
        Invoke-PplidDeployScript -ScriptPath $healthScript -Arguments @{ Environment = $Environment }
        $healthy = $true
        break
    } catch {
        Log "Health tentativa $attempt falhou: $($_.Exception.Message)"
    }
}

if (-not $healthy) {
    throw "Health check falhou apos bootstrap_first_release."
}

Set-DeployState -Environment $Environment -Updates @{
    status      = "idle"
    activeSha   = $targetSha
    lastGoodSha = $targetSha
    lastError   = $null
    targetSha   = $null
    runId       = $null
    startedAt   = $null
    finishedAt  = (Get-Date).ToString("o")
}

if (Test-Path (Join-Path $deployScript "lib.ps1")) {
    . (Join-Path $deployScript "lib.ps1")
    Set-DeployedShaFile -Environment $Environment -ShaShort $targetSha -ShaFull $targetShaFull
    Update-DeployStatus -Environment $Environment -Updates @{
        phase         = "healthy"
        deployedSha   = $targetSha
        deployedShaFull = $targetShaFull
        updatePending = $false
        lastDeployResult = "success"
        lastDeployMessage = "bootstrap_first_release"
        lastDeployFinishedAt = (Get-Date).ToString("o")
    } -EventType "deploy_success" -EventMessage "bootstrap_first_release $targetSha"
}

Log "bootstrap_first_release concluido para $Environment ($targetSha)."

. (Join-Path $PSScriptRoot "lib\sync_workspace_repo.ps1")
Sync-PplidWorkspaceRepo -Environment $Environment -TargetShaFull $targetShaFull -TargetSha $targetSha -LogFile $logFile
