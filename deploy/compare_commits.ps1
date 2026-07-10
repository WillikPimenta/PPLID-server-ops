param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment,
    [string]$RemoteSha = "",
    [string]$ActiveSha = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\deploy_state.ps1")
. (Join-Path (Split-Path $PSScriptRoot -Parent) "lib\version_drift.ps1")

$state = Get-DeployState -Environment $Environment
if (-not $ActiveSha) { $ActiveSha = $state.activeSha }
if (-not $RemoteSha) { throw "Informe RemoteSha." }

if (-not $ActiveSha) {
    return "deploy"
}
if (Test-PplidShaMatch -Left $RemoteSha -Right $ActiveSha) {
    return "noop"
}
return "deploy"
