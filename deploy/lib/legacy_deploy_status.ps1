function Reset-PplidLegacyDeployPhase {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [string]$Message = "recovered_stuck_deploy"
    )

    . (Join-Path (Split-Path $PSScriptRoot -Parent) "..\lib\paths.ps1")
    $statusPath = Get-PplidStatusFile
    if (-not (Test-Path $statusPath)) {
        return $false
    }

    $status = Get-Content $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $envStatus = $status.environments.$Environment
    if (-not $envStatus -or $envStatus.phase -ne "deploying") {
        return $false
    }

    $envStatus.phase = "failed"
    $envStatus.lastDeployResult = "failed"
    $envStatus.lastDeployMessage = $Message
    $envStatus.lastDeployFinishedAt = (Get-Date).ToString("o")
    $status.updatedAt = (Get-Date).ToString("o")
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($statusPath, ($status | ConvertTo-Json -Depth 20), $utf8)
    return $true
}

function Get-PplidPreservedActiveSha {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [hashtable]$State = $null
    )

    . (Join-Path (Split-Path $PSScriptRoot -Parent) "..\lib\version_drift.ps1")
    . (Join-Path $PSScriptRoot "deploy_paths.ps1")

    if (-not $State) {
        . (Join-Path $PSScriptRoot "deploy_state.ps1")
        $State = Get-DeployState -Environment $Environment
    }

    foreach ($candidate in @(
            $State.activeSha,
            $State.lastGoodSha,
            (Get-PplidCurrentReleaseSha -Environment $Environment),
            (Get-PplidDeployedSha -Environment $Environment)
        )) {
        $sha = [string]$candidate
        if ($sha) {
            return $sha.Trim()
        }
    }
    return $null
}
