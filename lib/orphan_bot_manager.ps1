[CmdletBinding()]
param(
    [ValidateSet('scan','cleanup')][string]$Mode = 'cleanup',
    [string]$BaseDir = 'C:\PPLID',
    [string]$StatePath = '',
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Stop'
$now = (Get-Date).ToUniversalTime().ToString('o')
if (-not $StatePath) { $StatePath = Join-Path $BaseDir 'ops\data\orphan-bots.json' }
$stateDir = Split-Path -Parent $StatePath
if (-not (Test-Path -LiteralPath $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
}

function Get-Text($value) {
    if ($null -eq $value) { return '' }
    return [string]$value
}

function Get-CreationIso($process) {
    if ($null -eq $process -or $null -eq $process.CreationDate) { return '' }
    try { return ([datetime]$process.CreationDate).ToUniversalTime().ToString('o') } catch { return (Get-Text $process.CreationDate) }
}

function Get-BotMode($process) {
    $command = Get-Text $process.CommandLine
    if ($command -notmatch '(?i)app\.orchestration\.robot_runner') { return '' }
    $match = [regex]::Match($command, '(?i)--mode\s+["'']?([^\s"'']+)')
    if ($match.Success) { return $match.Groups[1].Value.ToLowerInvariant() }
    return ''
}

function Test-IsBot($process) { return [bool](Get-BotMode $process) }

function Get-ActiveReleases {
    $active = @{}
    foreach ($environment in @('MAIN','DEV','HOM')) {
        $sha = ''
        $current = Join-Path $BaseDir "deploy\$environment\current"
        try {
            $target = @((Get-Item -LiteralPath $current -Force -ErrorAction Stop).Target)[0]
            if ($target) { $sha = Split-Path -Leaf (Get-Text $target) }
        } catch { }
        if (-not $sha) {
            try {
                $state = Get-Content -LiteralPath (Join-Path $BaseDir "deploy\$environment\deploy-state.json") -Raw | ConvertFrom-Json
                $sha = Get-Text $state.activeSha
            } catch { }
        }
        if ($sha) { $active[$environment] = $sha.ToLowerInvariant() }
    }
    return $active
}

$script:activeReleases = Get-ActiveReleases

function Get-ReleaseInfo($process) {
    $text = ((Get-Text $process.CommandLine) + ' ' + (Get-Text $process.ExecutablePath)).Replace('/','\')
    $release = [regex]::Match($text, '(?i)deploy\\(MAIN|DEV|HOM)\\releases\\([^\\\s"'']+)\\')
    if ($release.Success) {
        return [pscustomobject]@{
            environment = $release.Groups[1].Value.ToUpperInvariant()
            release = $release.Groups[2].Value.ToLowerInvariant()
            source = 'release'
        }
    }
    $current = [regex]::Match($text, '(?i)deploy\\(MAIN|DEV|HOM)\\current\\')
    if ($current.Success) {
        $environment = $current.Groups[1].Value.ToUpperInvariant()
        return [pscustomobject]@{
            environment = $environment
            release = Get-Text $script:activeReleases[$environment]
            source = 'current'
        }
    }
    $repo = [regex]::Match($text, '(?i)repos\\PPLID_(MAIN|DEV|HOM)\\')
    if ($repo.Success) {
        return [pscustomobject]@{
            environment = $repo.Groups[1].Value.ToUpperInvariant()
            release = 'workspace'
            source = 'workspace'
        }
    }
    return $null
}

function Test-IsApprovedProcess($process) {
    $text = ((Get-Text $process.CommandLine) + ' ' + (Get-Text $process.ExecutablePath) + ' ' + (Get-Text $process.Name)).ToLowerInvariant().Replace('/','\')
    if ($text -match '(?i)(--pplid-temporary|pplid[_-]temporary|pplid[_-]supervised)') { return $true }
    $info = Get-ReleaseInfo $process
    if ($null -eq $info) { return $false }
    if ($info.source -in @('current','workspace')) { return $true }
    $activeSha = Get-Text $script:activeReleases[$info.environment]
    return [bool]($activeSha -and $info.release -eq $activeSha.ToLowerInvariant())
}

function Set-ProcessSnapshot {
    $script:raw = @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate)
    $script:byPid = @{}
    $script:children = @{}
    foreach ($process in $script:raw) {
        $pidValue = [int]$process.ProcessId
        $parentValue = [int]$process.ParentProcessId
        $script:byPid[$pidValue] = $process
        if (-not $script:children.ContainsKey($parentValue)) { $script:children[$parentValue] = @() }
        $script:children[$parentValue] += $pidValue
    }
    $script:approvedPids = @{}
    foreach ($process in $script:raw) {
        if (Test-IsApprovedProcess $process) { $script:approvedPids[[int]$process.ProcessId] = $true }
    }
    try {
        $machine = Get-Content -LiteralPath (Join-Path $BaseDir 'machine.config.json') -Raw | ConvertFrom-Json
        $ports = @($machine.MAIN.backendPort,$machine.DEV.backendPort,$machine.HOM.backendPort) | Where-Object { $_ }
        foreach ($connection in @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
            if ($ports -contains $connection.LocalPort -and $connection.OwningProcess) {
                $script:approvedPids[[int]$connection.OwningProcess] = $true
            }
        }
    } catch { }
}

function Get-Chain([int]$ProcessId) {
    $seen = @{}
    $chain = @()
    $currentPid = $ProcessId
    while ($currentPid -and -not $seen.ContainsKey($currentPid)) {
        $seen[$currentPid] = $true
        $chain += $currentPid
        if (-not $script:byPid.ContainsKey($currentPid)) { break }
        $currentPid = [int]$script:byPid[$currentPid].ParentProcessId
    }
    return @($chain)
}

function Test-HasApprovedAncestor([int]$ProcessId) {
    foreach ($ancestor in @(Get-Chain $ProcessId)) {
        if ($script:approvedPids.ContainsKey([int]$ancestor)) { return $true }
    }
    return $false
}

function Get-Descendants([int]$RootPid) {
    $result = @()
    $queue = @($RootPid)
    $seen = @{}
    while ($queue.Count) {
        $currentPid = [int]$queue[0]
        $queue = @($queue | Select-Object -Skip 1)
        if ($seen.ContainsKey($currentPid)) { continue }
        $seen[$currentPid] = $true
        if ($script:children.ContainsKey($currentPid)) {
            foreach ($child in $script:children[$currentPid]) {
                $result += [int]$child
                $queue += [int]$child
            }
        }
    }
    return @($result)
}

function Get-ChainReleaseInfo([int[]]$Chain) {
    foreach ($pidValue in $Chain) {
        if (-not $script:byPid.ContainsKey([int]$pidValue)) { continue }
        $info = Get-ReleaseInfo $script:byPid[[int]$pidValue]
        if ($null -ne $info) { return $info }
    }
    return $null
}

function Get-BotItems {
    $botProcesses = @($script:raw | Where-Object { Test-IsBot $_ })
    $botPids = @{}
    foreach ($process in $botProcesses) { $botPids[[int]$process.ProcessId] = $true }
    $roots = @($botProcesses | Where-Object { -not $botPids.ContainsKey([int]$_.ParentProcessId) })
    $items = @()
    foreach ($process in $roots) {
        $pidValue = [int]$process.ProcessId
        $chain = @(Get-Chain $pidValue)
        $managed = Test-HasApprovedAncestor $pidValue
        $releaseInfo = Get-ChainReleaseInfo $chain
        $classification = if ($managed) { 'managed' } else { 'orphan' }
        $environment = if ($null -ne $releaseInfo) { Get-Text $releaseInfo.environment } else { '' }
        $release = if ($null -ne $releaseInfo) { Get-Text $releaseInfo.release } else { '' }
        $activeRelease = if ($environment) { Get-Text $script:activeReleases[$environment] } else { '' }
        if ($managed) {
            $reason = 'release atual ou cadeia ligada a supervisor PPLID aprovado'
        } elseif ($release -and $activeRelease -and $release -ne $activeRelease) {
            $reason = "release anterior $release; release ativa $activeRelease"
        } else {
            $reason = 'processo sem vínculo com uma release ativa ou supervisor PPLID'
        }
        $items += [pscustomobject][ordered]@{
            pid = $pidValue
            parentPid = [int]$process.ParentProcessId
            environment = $environment
            release = $release
            activeRelease = $activeRelease
            mode = Get-BotMode $process
            creationDate = Get-CreationIso $process
            classification = $classification
            chain = @($chain)
            descendants = if ($managed) { @() } else { @(Get-Descendants $pidValue) }
            reason = $reason
            result = 'preserved'
        }
    }
    return @($items)
}

function Write-Result($payload) {
    $json = $payload | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::WriteAllText($StatePath, ($payload | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
    if ($LogPath) {
        $line = "[$($payload.scannedAt)] orphan-bots mode=$($payload.mode) detected=$($payload.detected) stopped=$($payload.stopped) failed=$($payload.failed) pids=$((@($payload.items | ForEach-Object pid) -join ','))"
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    }
    Write-Output $json
}

try {
    Set-ProcessSnapshot
    $items = @(Get-BotItems)
    $orphanItems = @($items | Where-Object { $_.classification -eq 'orphan' })
    $detectedBefore = $orphanItems.Count

    if ($Mode -eq 'cleanup') {
        foreach ($item in $orphanItems) {
            try {
                $script:activeReleases = Get-ActiveReleases
                Set-ProcessSnapshot
                if (-not $script:byPid.ContainsKey([int]$item.pid)) {
                    $item.result = 'already-stopped'
                    continue
                }
                $live = $script:byPid[[int]$item.pid]
                $sameProcess = (Get-CreationIso $live) -eq (Get-Text $item.creationDate)
                $stillOrphan = (Test-IsBot $live) -and -not (Test-HasApprovedAncestor ([int]$item.pid))
                if (-not $sameProcess -or -not $stillOrphan) {
                    $item.result = 'preserved-after-revalidation'
                    $item.reason = 'identidade ou classificação mudou antes do encerramento'
                    continue
                }
                $taskOutput = & taskkill.exe /PID ([int]$item.pid) /T /F 2>&1
                if ($LASTEXITCODE -eq 0) {
                    $item.result = 'stopped'
                } else {
                    $item.result = 'failed'
                    $item.reason += "; taskkill: $($taskOutput -join ' ')"
                }
            } catch {
                $item.result = 'failed'
                $item.reason += "; $($_.Exception.Message)"
            }
        }
    }

    $stopped = @($items | Where-Object result -eq 'stopped').Count
    $failed = @($items | Where-Object result -eq 'failed').Count
    $stalePidFiles = @()
    if ($Mode -eq 'cleanup') {
        try {
            foreach ($pidFile in @(Get-ChildItem -LiteralPath (Join-Path $BaseDir 'logs') -Filter '*.robot_runner.pid' -File -Recurse -ErrorAction SilentlyContinue)) {
                $value = 0
                [int]::TryParse((Get-Content -LiteralPath $pidFile.FullName -Raw), [ref]$value) | Out-Null
                if ($value -gt 0 -and -not $script:byPid.ContainsKey($value)) {
                    Remove-Item -LiteralPath $pidFile.FullName -Force -ErrorAction SilentlyContinue
                    if (-not (Test-Path -LiteralPath $pidFile.FullName)) { $stalePidFiles += $pidFile.FullName }
                }
            }
        } catch { }
    }
    $result = [ordered]@{
        ok = ($failed -eq 0)
        mode = $Mode
        scannedAt = $now
        detected = $detectedBefore
        stopped = $stopped
        failed = $failed
        stalePidFiles = @($stalePidFiles)
        items = @($items)
    }
    Write-Result $result
    exit 0
} catch {
    $failure = [ordered]@{
        ok = $false
        mode = $Mode
        scannedAt = $now
        detected = 0
        stopped = 0
        failed = 1
        stalePidFiles = @()
        items = @()
        error = $_.Exception.Message
    }
    try { Write-Result $failure } catch { Write-Output ($failure | ConvertTo-Json -Depth 4 -Compress) }
    exit 1
}
