function Sync-PplidWorkspaceRepo {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [string]$TargetShaFull = "",
        [string]$TargetSha = "",
        [string]$LogFile = ""
    )

    . (Join-Path $PSScriptRoot "deploy_paths.ps1")
    . (Join-Path $PSScriptRoot "env_spec.ps1")
    . (Join-Path $PSScriptRoot "git_invoke.ps1")

    $spec = Get-PplidEnvSpec -Environment $Environment
    $repoDir = $spec.RepoDir
    $branch = $spec.Branch
    $paths = Get-PplidDeployEnvPaths -Environment $Environment

    function Write-SyncLog([string]$Message) {
        if (-not $LogFile) { return }
        $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Add-Content -Path $LogFile -Value "[$ts] $Message" -Encoding UTF8
    }

    $shaFull = $TargetShaFull.Trim()
    if (-not $shaFull -and $TargetSha) {
        $metaFile = Join-Path $paths.Releases $TargetSha "meta.json"
        if (Test-Path $metaFile) {
            try {
                $meta = Get-Content $metaFile -Raw -Encoding UTF8 | ConvertFrom-Json
                $shaFull = [string]$meta.shaFull
            } catch { }
        }
    }
    if (-not $shaFull -and $TargetSha -and (Test-Path (Join-Path $paths.Mirror ".git"))) {
        Push-Location $paths.Mirror
        try {
            $lines = Invoke-PplidGit -Args @("rev-parse", $TargetSha) -FailMessage "rev-parse falhou."
            $shaFull = ($lines -join "`n").Trim()
        } catch {
            Write-SyncLog "Workspace sync skip: rev-parse $($TargetSha) falhou."
            return
        } finally {
            Pop-Location
        }
    }
    if (-not $shaFull) {
        Write-SyncLog "Workspace sync skip: SHA completo ausente."
        return
    }

    if (-not (Test-Path (Join-Path $repoDir ".git"))) {
        Write-SyncLog "Workspace sync skip: sem git em $repoDir"
        return
    }

    Push-Location $repoDir
    try {
        Invoke-PplidGit -Args @("fetch", "origin") -FailMessage "workspace fetch falhou." | Out-Null
        Invoke-PplidGit -Args @("checkout", "-B", $branch, $shaFull) -FailMessage "workspace checkout falhou." | Out-Null
        Write-SyncLog "Workspace sync OK: $branch -> $shaFull"
    } catch {
        Write-SyncLog "Workspace sync aviso: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }
}
