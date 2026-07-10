# Diagnostico: servicos off vs console errado (plano de avaliacao)
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

$lanIp = "127.0.0.1"
$machinePath = Join-Path (Get-PplidBaseDir) "machine.config.json"
if (Test-Path $machinePath) {
    try {
        $machine = Get-Content $machinePath -Raw | ConvertFrom-Json
        if ($machine.lanIp) { $lanIp = $machine.lanIp }
    } catch { }
}

$envPorts = @{
    MAIN = @{ Backend = 8000; Frontend = 5173 }
    DEV  = @{ Backend = 8001; Frontend = 5174 }
    HOM  = @{ Backend = 8002; Frontend = 5175 }
}

function Test-Url {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return @{ Ok = $true; Detail = "HTTP $($r.StatusCode)" }
    } catch {
        return @{ Ok = $false; Detail = $_.Exception.Message }
    }
}

Write-Host "=== Fase 2: localhost vs LAN ($lanIp) ==="
Write-Host ""

$rows = @()
foreach ($env in @("MAIN", "DEV", "HOM")) {
    $p = $envPorts[$env]
    foreach ($kind in @("BackendHealth", "Frontend")) {
        $path = if ($kind -eq "BackendHealth") { "/api/v1/health/" } else { "/" }
        $port = if ($kind -eq "BackendHealth") { $p.Backend } else { $p.Frontend }
        $loop = Test-Url -Url "http://127.0.0.1:${port}${path}"
        $lan = Test-Url -Url "http://${lanIp}:${port}${path}"
        $rows += [PSCustomObject]@{
            Environment = $env
            Check       = $kind
            Localhost   = if ($loop.Ok) { "OK $($loop.Detail)" } else { "FAIL $($loop.Detail)" }
            LAN         = if ($lan.Ok) { "OK $($lan.Detail)" } else { "FAIL $($lan.Detail)" }
        }
    }
}
$rows | Format-Table -AutoSize

Write-Host "=== Fase 3: portas LISTENING ==="
netstat -ano | findstr "LISTENING" | findstr ":8000 :8001 :8002 :5173 :5174 :5175 :5190"

Write-Host ""
Write-Host "=== Fase 4: overview via build_overview (Python) ==="
$opsConsoleDir = Get-PplidOpsConsoleDir -ScriptRoot $PSScriptRoot
$envConfigPath = Get-PplidEnvConfigPath -ScriptRoot $PSScriptRoot
$py = @"
import json, sys
sys.path.insert(0, r'$opsConsoleDir')
from pathlib import Path
from server import build_overview, load_config
cfg = load_config(Path(r'$envConfigPath'))
ov = build_overview(cfg)
out = {}
for env in ('MAIN','DEV','HOM'):
    e = ov['environments'].get(env, {})
    out[env] = {
        'displayPhase': e.get('displayPhase'),
        'lastDeployResult': e.get('lastDeployResult'),
        'runtime': e.get('runtime'),
        'availability': e.get('availability'),
        'hasAvailabilityKey': 'availability' in e,
    }
print(json.dumps(out, indent=2, ensure_ascii=False))
"@
python -c $py
