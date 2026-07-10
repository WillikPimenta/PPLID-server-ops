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
    $spec.RepoDir = Get-PplidRepoDir -Name $spec.RepoName
    $spec.RepoUrl = "https://github.com/WillikPimenta/PPLID.git"
    return [PSCustomObject]$spec
}
