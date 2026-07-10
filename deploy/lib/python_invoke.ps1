function Invoke-PplidPython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Python,
        [Parameter(Mandatory = $true)]
        [string[]]$Args,
        [string]$WorkingDirectory = "",
        [string]$FailMessage = "comando python falhou."
    )

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        if ($WorkingDirectory) {
            Push-Location $WorkingDirectory
        }
        $raw = & $Python @Args 2>&1
        $code = $LASTEXITCODE
    } finally {
        if ($WorkingDirectory) {
            Pop-Location
        }
        $ErrorActionPreference = $prevEap
    }

    $lines = @()
    foreach ($item in @($raw)) {
        if ($null -eq $item) { continue }
        if ($item -is [System.Management.Automation.ErrorRecord]) {
            if ($item.TargetObject) {
                $lines += "$($item.TargetObject)".Trim()
            } elseif ($item.Exception -and $item.Exception.Message) {
                $lines += $item.Exception.Message.Trim()
            }
        } else {
            $lines += "$item".Trim()
        }
    }

    if ($code -ne 0) {
        $detail = if ($lines.Count -gt 0) { ($lines -join "`n").Trim() } else { "" }
        if ($detail) {
            throw "$FailMessage`n$detail"
        }
        throw $FailMessage
    }

    return $lines
}
