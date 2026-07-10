param(
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [string]$RepoUrl = "https://github.com/WillikPimenta/PPLID.git"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

$envMap = @{
    MAIN = @{ RepoName = "PPLID_MAIN"; Branch = "main" }
    DEV  = @{ RepoName = "PPLID_DEV"; Branch = "dev" }
    HOM  = @{ RepoName = "PPLID_HOM"; Branch = "hom" }
}

if (-not $Environment) {
    throw "Informe -Environment MAIN, DEV ou HOM."
}

$spec = $envMap[$Environment]
$reposDir = Get-PplidReposDir
$logDir = Get-PplidLogDir
$repoDir = Get-PplidRepoDir -Name $spec.RepoName
$logFile = Join-Path $logDir "PPLID_$Environment.log"
$deployLog = Join-Path $logDir "PPLID_$Environment.deploy.log"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$timestamp] $Message" -Encoding UTF8
}

Write-Log "=================================================="
Write-Log "Iniciando verificacao..."

if (-not (Test-Path $reposDir)) {
    Write-Log "Criando pasta repos..."
    New-Item -ItemType Directory -Path $reposDir -Force | Out-Null
}

if (-not (Test-Path (Join-Path $repoDir ".git"))) {
    Write-Log "Repositorio nao encontrado. Realizando clone da branch $($spec.Branch)..."
    Push-Location $reposDir
    try {
        git clone -b $spec.Branch $RepoUrl $spec.RepoName 2>&1 | Out-File -FilePath $logFile -Append -Encoding UTF8
    } finally {
        Pop-Location
    }
}

Push-Location $repoDir
try {
    $phaseScript = Join-Path $repoDir "scripts\deploy\set_deploy_phase.ps1"
    & powershell -ExecutionPolicy Bypass -File $phaseScript `
        -Environment $Environment -Phase syncing `
        -EventType sync_started -EventMessage "Verificacao git iniciada" 2>> $logFile

    Write-Log "Executando git fetch..."
    git fetch origin 2>&1 | Out-File -FilePath $logFile -Append -Encoding UTF8

    $local = (git rev-parse HEAD).Trim()
    $remote = (git rev-parse "origin/$($spec.Branch)").Trim()

    if ($local -ne $remote) {
        & powershell -ExecutionPolicy Bypass -File $phaseScript `
            -Environment $Environment -Phase syncing -OriginSha $remote `
            -UpdatePending -EventType sync_pending `
            -EventMessage "Atualizacao pendente no GitHub" 2>> $logFile
    } else {
        & powershell -ExecutionPolicy Bypass -File $phaseScript `
            -Environment $Environment -Phase syncing -OriginSha $remote `
            -EventMessage "Repositorio em dia com origin" 2>> $logFile
    }

    if ($local -ne $remote) {
        Write-Log "Atualizacao encontrada. Executando git pull..."
        git pull origin $spec.Branch 2>&1 | Out-File -FilePath $logFile -Append -Encoding UTF8
        Write-Log "Atualizado com sucesso. Iniciando deploy..."

        & powershell -ExecutionPolicy Bypass -File $phaseScript `
            -Environment $Environment -Phase syncing -MarkSyncComplete `
            -EventType sync_pulled -EventMessage "Pull concluido, iniciando deploy" 2>> $logFile

        $deployScript = Join-Path $repoDir "scripts\deploy\deploy_env.ps1"
        & powershell -ExecutionPolicy Bypass -File $deployScript -Environment $Environment `
            >> $deployLog 2>&1
    } else {
        Write-Log "Sem alteracoes."
        & powershell -ExecutionPolicy Bypass -File $phaseScript `
            -Environment $Environment -Phase idle -MarkSyncComplete `
            -EventType sync_idle -EventMessage "Sem alteracoes no repositorio" 2>> $logFile
    }

    Write-Log "Finalizado."
} finally {
    Pop-Location
}
