[CmdletBinding()]
param(
    [ValidateSet('scan','cleanup')][string]$Mode = 'cleanup',
    [string]$BaseDir = 'C:\PPLID',
    [string]$StatePath = '',
    [string]$LogPath = ''
)

# Keep the public entrypoint stable for deploy/bootstrap callers while the
# implementation lives in a testable, self-contained script.
$implementation = Join-Path $PSScriptRoot 'orphan_bot_manager.ps1'
& $implementation @PSBoundParameters
exit $LASTEXITCODE

$ErrorActionPreference = 'Stop'
$now = (Get-Date).ToUniversalTime().ToString('o')
if (-not $StatePath) { $StatePath = Join-Path $BaseDir 'ops\data\orphan-bots.json' }
$stateDir = Split-Path -Parent $StatePath
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }

function Get-Text($v) { if ($null -eq $v) { return '' }; return [string]$v }
function Get-BotMode($p) {
    $command = Get-Text $p.CommandLine
    if ($command -notmatch '(?i)app\.orchestration\.robot_runner') { return '' }
    $match = [regex]::Match($command, '(?i)--mode\s+["'']?([^\s"'']+)')
    if ($match.Success) { return $match.Groups[1].Value.ToLowerInvariant() }
    return ''
}
function Is-Bot($p) { return [bool](Get-BotMode $p) }
function Is-Managed($p) {
    $s = ((Get-Text $p.CommandLine) + ' ' + (Get-Text $p.ExecutablePath) + ' ' + (Get-Text $p.Name)).ToLower().Replace('/','\')
    if ($s -match '(?i)(--pplid-temporary|pplid[_-]temporary|pplid[_-]supervised)') { return $true }
    # Only the active current release supervisor can authorize a runner.
    if ($s -match '(?i)deploy\\(main|dev|hom)\\current\\backend') { return $true }
    if ($s -match '(?i)pplid_(main|dev|hom)') { return $true }
    return $false
}

try {
    $raw = @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate)
} catch { $raw = @() }
$byPid = @{}
foreach ($p in $raw) { $byPid[[int]$p.ProcessId] = $p }
$children = @{}
foreach ($p in $raw) { $pp = [int]$p.ParentProcessId; if (-not $children.ContainsKey($pp)) { $children[$pp] = @() }; $children[$pp] += [int]$p.ProcessId }

$managedPids = @{}
foreach ($p in $raw) { if (Is-Managed $p) { $managedPids[[int]$p.ProcessId] = $true } }
# A backend listening on a configured port is an approved supervisor even when its command line is generic.
try {
    $machine = Get-Content (Join-Path $BaseDir 'machine.config.json') -Raw | ConvertFrom-Json
    $ports = @($machine.MAIN.backendPort,$machine.DEV.backendPort,$machine.HOM.backendPort) | Where-Object { $_ }
    foreach ($c in @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
        if ($ports -contains $c.LocalPort -and $c.OwningProcess) { $managedPids[[int]$c.OwningProcess] = $true }
    }
} catch { }

function Get-Chain($processId) {
    $seen = @{}; $chain = @(); $cur = [int]$processId
    while ($cur -and -not $seen.ContainsKey($cur)) {
        $seen[$cur] = $true; $chain += $cur
        if (-not $byPid.ContainsKey($cur)) { break }
        $cur = [int]$byPid[$cur].ParentProcessId
    }
    return $chain
}
function Has-ManagedAncestor($processId) {
    foreach ($ancestor in (Get-Chain $processId)) { if ($managedPids.ContainsKey([int]$ancestor)) { return $true } }
    return $false
}
function Get-Descendants($root) {
    $result = @(); $queue = @([int]$root); $seen = @{}
    while ($queue.Count) { $cur = [int]$queue[0]; $queue = @($queue | Select-Object -Skip 1); if ($seen.ContainsKey($cur)) { continue }; $seen[$cur]=$true
        if ($children.ContainsKey($cur)) { foreach ($child in $children[$cur]) { $result += [int]$child; $queue += [int]$child } }
    }; return @($result)
}

$botProcesses = @($raw | Where-Object { Is-Bot $_ })
$botPids = @{}
foreach ($p in $botProcesses) { $botPids[[int]$p.ProcessId] = $true }
$botRoots = @($botProcesses | Where-Object { -not $botPids.ContainsKey([int]$_.ParentProcessId) })
$productionRoots = @($botRoots | Where-Object { (Get-BotMode $_) -eq 'production' } | Sort-Object CreationDate -Descending)
$newestProductionPid = if ($productionRoots.Count -gt 0) { [int]$productionRoots[0].ProcessId } else { 0 }

$items = @(); $roots = @()
foreach ($p in $botRoots) {
    $processId = [int]$p.ProcessId; $mode = Get-BotMode $p; $chain = @(Get-Chain $processId); $managed = Has-ManagedAncestor $processId
    $legacyDuplicate = ($mode -eq 'production' -and $productionRoots.Count -gt 1 -and $processId -ne $newestProductionPid)
    # Um supervisor PPLID nÃƒÆ’Ã‚Â£o torna um runner antigo vÃƒÆ’Ã‚Â¡lido: quando hÃƒÆ’Ã‚Â¡ mais de
    # um production, somente o mais novo permanece; os demais sÃƒÆ’Ã‚Â£o legados.
    $classification = if ($legacyDuplicate) { 'legacy-duplicate' } elseif ($managed) { 'managed' } else { 'orphan' }
    $item = [ordered]@{ pid=$processId; parentPid=[int]$p.ParentProcessId; mode=$mode; creationDate=$p.CreationDate; classification=$classification; chain=@($chain); descendants=@(); reason=''; result='preserved' }
    if ($classification -eq 'legacy-duplicate') { $item.reason='runner production antigo; existe outro runner production mais novo' }
    elseif ( -eq 'managed') { .reason='cadeia alcanca supervisor PPLID aprovado' }
    else { .reason='pai inexistente ou cadeia sem supervisor PPLID' }
    if ($classification -ne 'managed') { $item.descendants=@(Get-Descendants $processId); $roots += $item }
    $items += [pscustomobject]$item
}

if ($Mode -eq 'cleanup') {
    foreach ($item in $roots) {
        # Revalidate identity and ancestry immediately before taskkill.
        $live = Get-CimInstance Win32_Process -Filter "ProcessId = $($item.pid)" -ErrorAction SilentlyContinue
        $stillBot = $live -and (Is-Bot $live) -and ($item.classification -eq 'legacy-duplicate' -or -not (Has-ManagedAncestor ([int]$item.pid)))
        if (-not $stillBot) { $item.result='preserved-after-revalidation'; continue }
        $all = @([int]$item.pid) + @($item.descendants)
        $task = & taskkill.exe /PID ([int]$item.pid) /T /F 2>&1
        if ($LASTEXITCODE -eq 0) { $item.result='stopped' } else { $item.result='failed'; $item.reason += "; taskkill: $($task -join ' ')" }
    }
}
$detected = @($items | Where-Object classification -in @('orphan','legacy-duplicate')).Count
$stopped = @($items | Where-Object result -eq 'stopped').Count
$failed = @($items | Where-Object result -eq 'failed').Count
$stalePidFiles = @()
try {
    foreach ($pidFile in @(Get-ChildItem -LiteralPath (Join-Path $BaseDir 'logs') -Filter '*.robot_runner.pid' -File -Recurse -ErrorAction SilentlyContinue)) {
        $value = 0; [int]::TryParse((Get-Content -LiteralPath $pidFile.FullName -Raw), [ref]$value) | Out-Null
        if ($value -gt 0 -and -not $byPid.ContainsKey($value)) {
            Remove-Item -LiteralPath $pidFile.FullName -Force -ErrorAction SilentlyContinue
            if (-not (Test-Path -LiteralPath $pidFile.FullName)) { $stalePidFiles += $pidFile.FullName }
        }
    }
} catch { }
$result = [ordered]@{ mode=$Mode; scannedAt=$now; detected=$detected; stopped=$stopped; failed=$failed; stalePidFiles=@($stalePidFiles); items=@($items) }
$json = $result | ConvertTo-Json -Depth 8 -Compress
[IO.File]::WriteAllText($StatePath, ($result | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
if ($LogPath) { Add-Content -LiteralPath $LogPath -Value "[$now] orphan-bots mode=$Mode detected=$detected stopped=$stopped failed=$failed pids=$((@($items | % pid) -join ','))" -Encoding UTF8 }
Write-Output $json
exit 0
