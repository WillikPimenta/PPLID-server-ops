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
. (Join-Path $PSScriptRoot "lib\git_invoke.ps1")
. (Join-Path $PSScriptRoot "lib\backend_deploy.ps1")

$spec = Get-PplidEnvSpec -Environment $Environment
$paths = Get-PplidDeployEnvPaths -Environment $Environment
$releaseDir = Get-PplidReleaseDir -Environment $Environment -Sha $TargetSha
$metaFile = Join-Path $releaseDir "meta.json"

function Log([string]$msg) {
    Write-RunLog -Environment $Environment -RunId $RunId -Message $msg -LogName "build.log"
}

function Remove-ReleaseWorktree {
    param([string]$Dir)

    if (-not (Test-Path $Dir)) { return }

    Push-Location $paths.Mirror
    try {
        Invoke-PplidGit -Args @("worktree", "remove", $Dir, "--force") -FailMessage "worktree remove falhou." -OnLine {
            param($line)
            Log ("git worktree remove: " + $line)
        }
    } catch {
        Log "worktree remove: $($_.Exception.Message); removendo pasta."
        Pop-Location
        $item = Get-Item $Dir -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            cmd /c rmdir "$Dir" 2>$null | Out-Null
        } else {
            Remove-Item -LiteralPath $Dir -Recurse -Force -ErrorAction SilentlyContinue
        }
        Push-Location $paths.Mirror
        Invoke-PplidGit -Args @("worktree", "prune") -FailMessage "worktree prune falhou." -OnLine {
            param($line)
            Log ("git worktree prune: " + $line)
        }
    } finally {
        Pop-Location
    }
}

Log "Build iniciado para $TargetSha ($TargetShaFull)"

$state = Get-DeployState -Environment $Environment
$fromSha = [string]$state.activeSha
if (-not $fromSha) {
    $fromSha = [string]$state.lastGoodSha
}
$backendChanged = Test-PplidBackendChanged -MirrorDir $paths.Mirror -FromSha $fromSha -ToSha $TargetShaFull
$backendPaths = @()
if ($backendChanged) {
    $backendPaths = Get-PplidBackendDiffPaths -MirrorDir $paths.Mirror -FromSha $fromSha -ToSha $TargetShaFull
    Log ("Backend alterado ($($backendPaths.Count) arquivos): " + ($backendPaths -join ", "))
}

if (Test-Path $metaFile) {
    $meta = Get-Content $metaFile -Raw | ConvertFrom-Json
    if ($meta.built -eq $true) {
        if ($backendChanged) {
            Log "Release $TargetSha marcada built, mas backend mudou - rebuild forcado."
        } else {
            Log "Release $TargetSha ja construida, skip build."
            return
        }
    }
}

if (Test-Path $releaseDir) {
    Log "Limpando release incompleta $TargetSha..."
    Remove-ReleaseWorktree -Dir $releaseDir
}

Push-Location $paths.Mirror
try {
    Invoke-PplidGit -Args @("fetch", "origin") -FailMessage "git fetch falhou." -OnLine {
        param($line)
        Log ("git fetch origin: " + $line)
    }
    Invoke-PplidGit -Args @("worktree", "add", $releaseDir, $TargetShaFull) -FailMessage "git worktree add falhou." -OnLine {
        param($line)
        Log ("git worktree add: " + $line)
    }
} finally {
    Pop-Location
}

Log "Worktree criado em $releaseDir"

$backendDir = Join-Path $releaseDir "backend"
$frontendDir = Join-Path $releaseDir "frontend"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Log "Criando venv..."
    & python -m venv (Join-Path $backendDir ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar venv." }
}

Log "pip install..."
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

Log "manage.py check..."
Push-Location $backendDir
try {
    & $venvPython manage.py check
    if ($LASTEXITCODE -ne 0) { throw "manage.py check falhou." }
} finally {
    Pop-Location
}

$deployScript = Join-Path $spec.RepoDir "scripts\deploy"
$repoBackendEnv = Join-Path $spec.RepoDir "backend\.env"
$releaseBackendEnv = Join-Path $releaseDir "backend\.env"
if (Test-Path $repoBackendEnv) {
    Copy-Item $repoBackendEnv $releaseBackendEnv -Force
    Log "backend/.env copiado do repo."
}
$env:PPLID_APP_ROOT = $releaseDir
Log "Sincronizando .env para build..."
& (Join-Path $deployScript "sync_env_files.ps1") -Environment $Environment
if ($LASTEXITCODE -ne 0) { throw "sync_env_files falhou." }

Log "npm ci + build..."
Push-Location $frontendDir
try {
    if (Test-Path "package-lock.json") { npm ci } else { npm install }
    if ($LASTEXITCODE -ne 0) { throw "npm install falhou." }
    npm run build:deploy
    if ($LASTEXITCODE -ne 0) { throw "npm run build:deploy falhou." }
} finally {
    Pop-Location
}

$dist = Join-Path $frontendDir "dist"
if (-not (Test-Path $dist)) {
    throw "dist/ nao encontrado apos build."
}

@{
    sha            = $TargetSha
    shaFull        = $TargetShaFull
    built          = $true
    builtAt        = (Get-Date).ToString("o")
    branch         = $spec.Branch
    backendChanged = [bool]$backendChanged
    backendPaths   = @($backendPaths)
} | ConvertTo-Json -Depth 4 | Set-Content -Path $metaFile -Encoding UTF8

Log "Build concluido."
