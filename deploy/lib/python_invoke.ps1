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
    $raw = $null
    $code = 0
    try {
        if ($WorkingDirectory) {
            Push-Location $WorkingDirectory
        }
        # Captura imediata do exit code: pipelines/2>&1 em PS podem
        # deixar LASTEXITCODE nulo; $null -ne 0 e True e gera falso negativo.
        $raw = & $Python @Args 2>&1
        if ($null -eq $LASTEXITCODE) {
            $code = 0
        } else {
            $code = [int]$LASTEXITCODE
        }
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
            } else {
                $lines += "$item".Trim()
            }
        } else {
            $lines += "$item".Trim()
        }
    }
    $lines = @($lines | Where-Object { $_ })

    if ($code -ne 0) {
        $detail = if ($lines.Count -gt 0) { ($lines -join "`n").Trim() } else { "(sem stdout/stderr)" }
        $cmdSummary = ($Args -join " ")
        throw "$FailMessage`nexitCode=$code`ncomando: $Python $cmdSummary`n$detail"
    }

    return $lines
}
