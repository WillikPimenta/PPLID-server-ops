function Get-PplidEnvSpec {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment
    )

    $map = @{
        MAIN = @{
            RepoName     = "PPLID_MAIN"
            Branch       = "main"
            BackendPort  = 8000
            FrontendPort = 5173
            PostgresDb   = "pplid_main"
        }
        DEV = @{
            RepoName     = "PPLID_DEV"
            Branch       = "dev"
            BackendPort  = 8001
            FrontendPort = 5174
            PostgresDb   = "pplid_dev"
        }
        HOM = @{
            RepoName     = "PPLID_HOM"
            Branch       = "hom"
            BackendPort  = 8002
            FrontendPort = 5175
            PostgresDb   = "pplid_hom"
        }
    }

    $spec = $map[$Environment]
    . (Join-Path (Split-Path $PSScriptRoot -Parent) "..\lib\paths.ps1")

    # Sobrescrever portas a partir de ops/config/env.config.json quando disponivel.
    $envConfigPath = Get-PplidEnvConfigPath
    if ($envConfigPath -and (Test-Path $envConfigPath)) {
        try {
            $root = Get-Content $envConfigPath -Raw | ConvertFrom-Json
            $envCfg = $root.$Environment
            if ($envCfg) {
                if ($null -ne $envCfg.backendPort) { $spec.BackendPort = [int]$envCfg.backendPort }
                if ($null -ne $envCfg.frontendPort) { $spec.FrontendPort = [int]$envCfg.frontendPort }
                if ($envCfg.postgresDb) { $spec.PostgresDb = [string]$envCfg.postgresDb }
                if ($envCfg.repoName) { $spec.RepoName = [string]$envCfg.repoName }
                if ($envCfg.branch) { $spec.Branch = [string]$envCfg.branch }
            }
        } catch {
            # Mantem defaults do mapa se o JSON estiver invalido.
        }
    }

    $spec.RepoDir = Get-PplidRepoDir -Name $spec.RepoName
    $spec.RepoUrl = "https://github.com/WillikPimenta/PPLID.git"
    return [PSCustomObject]$spec
}
