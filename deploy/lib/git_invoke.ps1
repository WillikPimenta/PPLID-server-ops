function Convert-PplidGitOutputLines {
    param($RawOutput)

    $lines = @()
    foreach ($item in @($RawOutput)) {
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
    return @($lines | Where-Object { $_ })
}

function Invoke-PplidGit {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args,
        [string]$FailMessage = "git falhou.",
        [scriptblock]$OnLine = $null
    )

    $env:GIT_TERMINAL_PROMPT = "0"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $raw = & git @Args 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }

    $lines = Convert-PplidGitOutputLines -RawOutput $raw
    if ($OnLine) {
        foreach ($line in $lines) {
            & $OnLine $line
        }
    }

    if ($code -ne 0) {
        $detail = if ($lines.Count -gt 0) { ($lines -join "`n").Trim() } else { "" }
        if ($detail) {
            throw "$FailMessage $detail"
        }
        throw $FailMessage
    }

    return $lines
}
