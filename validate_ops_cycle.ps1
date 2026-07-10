param(
    [switch]$SkipFinalStop
)

$ErrorActionPreference = "Stop"
$opsRoot = $PSScriptRoot

function Invoke-OpsCycleStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [int]$TimeoutSec = 120
    )

    if (-not (Test-Path $ScriptPath)) {
        return [PSCustomObject]@{
            Step        = $Name
            DurationSec = 0
            ExitCode    = -1
            Status      = "FAIL"
            Detail      = "Script nao encontrado: $ScriptPath"
        }
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
    $pinfo = New-Object System.Diagnostics.ProcessStartInfo
    $pinfo.FileName = "powershell.exe"
    $pinfo.Arguments = $argList
    $pinfo.UseShellExecute = $false
    $pinfo.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($pinfo)

    $timedOut = -not $proc.WaitForExit($TimeoutSec * 1000)
    if ($timedOut) {
        try { $proc.Kill() } catch { }
        $sw.Stop()
        return [PSCustomObject]@{
            Step        = $Name
            DurationSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
            ExitCode    = -2
            Status      = "FAIL"
            Detail      = "TIMEOUT ${TimeoutSec}s"
        }
    }

    $proc.Refresh()
    $exitCode = $proc.ExitCode
    $sw.Stop()
    $ok = ($exitCode -eq 0)
    return [PSCustomObject]@{
        Step        = $Name
        DurationSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        ExitCode    = $exitCode
        Status      = if ($ok) { "OK" } else { "FAIL" }
        Detail      = if ($ok) { "" } else { "exit=$exitCode" }
    }
}

Write-Host "=== PPLID Ops Cycle Validation ==="
Write-Host ""

$steps = @(
    @{ Name = "stop_all"; Script = Join-Path $opsRoot "stop_all.ps1"; TimeoutSec = 120 }
    @{ Name = "deploy_all"; Script = Join-Path $opsRoot "deploy_all.ps1"; TimeoutSec = 600 }
    @{ Name = "start_ops_console"; Script = Join-Path $opsRoot "start_ops_console.ps1"; TimeoutSec = 30 }
    @{ Name = "verify_stack"; Script = Join-Path $opsRoot "verify_stack.ps1"; TimeoutSec = 60 }
)

if (-not $SkipFinalStop) {
    $steps += @{ Name = "stop_all_final"; Script = Join-Path $opsRoot "stop_all.ps1"; TimeoutSec = 120 }
}

$results = @()
foreach ($step in $steps) {
    Write-Host ">> $($step.Name) (timeout $($step.TimeoutSec)s)..."
    $result = Invoke-OpsCycleStep -Name $step.Name -ScriptPath $step.Script -TimeoutSec $step.TimeoutSec
    $results += $result
    Write-Host "   $($result.Status) em $($result.DurationSec)s $($result.Detail)"
    Write-Host ""
}

$results | Format-Table -AutoSize Step, DurationSec, ExitCode, Status, Detail

$failed = @($results | Where-Object { $_.Status -ne "OK" })
Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "Ciclo ops validado com sucesso."
    exit 0
}

Write-Host "$($failed.Count) etapa(s) falharam ou excederam timeout."
exit 1
