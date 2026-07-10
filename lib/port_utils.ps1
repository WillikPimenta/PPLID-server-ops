# Resolucao de portas via netstat (Get-NetTCPConnection pode travar em alguns hosts Windows).

$script:NetstatSnapshotLines = $null
$script:NetstatSnapshotAt = $null
$script:NetstatSnapshotTtlSec = 1.5

function Clear-NetstatSnapshot {
    $script:NetstatSnapshotLines = $null
    $script:NetstatSnapshotAt = $null
}

function Get-NetstatSnapshot {
    $now = Get-Date
    if ($script:NetstatSnapshotLines -and $script:NetstatSnapshotAt) {
        $ageSec = ($now - $script:NetstatSnapshotAt).TotalSeconds
        if ($ageSec -lt $script:NetstatSnapshotTtlSec) {
            return $script:NetstatSnapshotLines
        }
    }

    $script:NetstatSnapshotLines = @(netstat -ano 2>$null)
    $script:NetstatSnapshotAt = $now
    return $script:NetstatSnapshotLines
}

function Get-PortConnectionPids {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [switch]$ListenOnly
    )

    $portToken = ":$Port "
    $pids = [System.Collections.Generic.HashSet[int]]::new()
    $lines = Get-NetstatSnapshot
    if (-not $lines) {
        return @()
    }

    foreach ($line in $lines) {
        if ($line -notmatch "^\s*TCP\s+") { continue }
        if ($line -notlike "*$portToken*") { continue }

        if ($ListenOnly) {
            if ($line -notmatch "\sLISTENING\s+(\d+)\s*$") { continue }
            $processId = [int]$Matches[1]
        } else {
            if ($line -notmatch "\s(LISTENING|ESTABLISHED)\s+(\d+)\s*$") { continue }
            $processId = [int]$Matches[2]
        }

        if ($processId -gt 0) {
            [void]$pids.Add($processId)
        }
    }

    return @($pids)
}

function Test-PortListening {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    return [bool](Get-PortConnectionPids -Port $Port -ListenOnly)
}

function Test-BackendPortListening {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    return Test-PortListening -Port $Port
}

function Test-TcpPortOpen {
    param(
        [string]$HostName = "127.0.0.1",
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [int]$TimeoutMs = 2000
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($HostName, $Port, $null, $null)
        $completed = $connect.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $completed) {
            return $false
        }
        $client.EndConnect($connect)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-PortListenOwnerPid {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $pids = Get-PortConnectionPids -Port $Port -ListenOnly
    if ($pids.Count -eq 0) {
        return $null
    }
    return $pids[0]
}

function Stop-PortProcess {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $killed = @()
    foreach ($processId in (Get-PortConnectionPids -Port $Port -ListenOnly)) {
        if ($processId -in $killed) { continue }
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            $killed += $processId
        } catch {
            # Processo de outro usuario/SYSTEM ou ja encerrado
        }
    }

    Clear-NetstatSnapshot
    return $killed
}

function Stop-PortListeners {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    return Stop-PortProcess -Port $Port
}

function Get-PostgresEndpointFromEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvFilePath
    )

    $hostName = "127.0.0.1"
    $port = 5432
    if (-not (Test-Path $EnvFilePath)) {
        return @{
            HostName = $hostName
            Port     = $port
            EnvPath  = $EnvFilePath
            EnvFound = $false
        }
    }

    foreach ($line in (Get-Content $EnvFilePath -ErrorAction SilentlyContinue)) {
        if ($line -match '^\s*POSTGRES_HOST\s*=\s*(.+)\s*$') {
            $parsedHost = $Matches[1].Trim()
            if ($parsedHost -eq "localhost") {
                $hostName = "127.0.0.1"
            } else {
                $hostName = $parsedHost
            }
        }
        if ($line -match '^\s*POSTGRES_PORT\s*=\s*(\d+)\s*$') {
            $port = [int]$Matches[1]
        }
    }

    return @{
        HostName = $hostName
        Port     = $port
        EnvPath  = $EnvFilePath
        EnvFound = $true
    }
}

function Test-PostgresAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BackendDir,
        [int]$TimeoutMs = 2000
    )

    $envPath = Join-Path $BackendDir ".env"
    $endpoint = Get-PostgresEndpointFromEnvFile -EnvFilePath $envPath
    $open = Test-TcpPortOpen -HostName $endpoint.HostName -Port $endpoint.Port -TimeoutMs $TimeoutMs
    return @{
        Open     = [bool]$open
        HostName = $endpoint.HostName
        Port     = $endpoint.Port
        EnvPath  = $endpoint.EnvPath
        EnvFound = $endpoint.EnvFound
    }
}
