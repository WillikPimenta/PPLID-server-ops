function ConvertFrom-PplidBackendEnvValue {
    [CmdletBinding()]
    param(
        [AllowEmptyString()]
        [string]$Value,
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [Parameter(Mandatory = $true)]
        [int]$LineNumber
    )

    $valueText = $Value.Trim()
    if ($valueText.Length -eq 0) {
        return ""
    }

    $quote = $valueText[0]
    if ($quote -eq "'" -or $quote -eq '"') {
        if ($valueText.Length -lt 2 -or $valueText[$valueText.Length - 1] -ne $quote) {
            throw "backend.env invalido na linha $LineNumber para a chave '$Key': aspas nao fechadas."
        }

        $inner = $valueText.Substring(1, $valueText.Length - 2)
        if ($quote -eq "'") {
            return $inner
        }

        return [regex]::Replace($inner, '\\([\\"nrt])', {
            param($match)
            switch ($match.Groups[1].Value) {
                'n' { return "`n" }
                'r' { return "`r" }
                't' { return "`t" }
                '"' { return '"' }
                '\' { return '\' }
            }
        })
    }

    # In unquoted dotenv values, only a hash preceded by whitespace starts a comment.
    $commentMatch = [regex]::Match($valueText, '\s+#')
    if ($commentMatch.Success) {
        $valueText = $valueText.Substring(0, $commentMatch.Index).TrimEnd()
    }
    return $valueText
}

function Read-PplidBackendEnv {
    <#
    .SYNOPSIS
    Reads a backend.env file without writing values to the host or error stream.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string[]]$RequiredKeys = @()
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "backend.env nao encontrado: $Path"
    }

    $result = @{}
    $lineNumber = 0
    foreach ($rawLine in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $lineNumber++
        $line = ([string]$rawLine).Trim()
        if (-not $line -or $line.StartsWith('#')) {
            continue
        }
        if ($line -match '^export\s+') {
            $line = $line -replace '^export\s+', ''
        }
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            throw "backend.env invalido na linha $lineNumber; nenhum valor foi exibido."
        }

        $key = $matches[1]
        $result[$key] = ConvertFrom-PplidBackendEnvValue -Value $matches[2] -Key $key -LineNumber $lineNumber
    }

    foreach ($requiredKey in $RequiredKeys) {
        if (-not $result.ContainsKey($requiredKey) -or [string]::IsNullOrWhiteSpace([string]$result[$requiredKey])) {
            throw "Chave obrigatoria ausente ou vazia no backend.env: $requiredKey"
        }
    }

    return $result
}

function Get-PplidPostgresToolVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $output = & $Path --version 2>&1
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    $text = (@($output) | ForEach-Object { "$_" }) -join "`n"
    if ($exitCode -ne 0 -or $text -notmatch '(\d+)\.(\d+)') {
        throw "Nao foi possivel identificar a versao de: $Path"
    }

    return [PSCustomObject]@{
        Text  = $text.Trim()
        Major = [int]$matches[1]
        Minor = [int]$matches[2]
    }
}

function Resolve-PplidPostgres14Tools {
    <#
    .SYNOPSIS
    Resolves pg_dump and pg_restore and requires PostgreSQL major version 14.
    #>
    [CmdletBinding()]
    param(
        [string]$BinDirectory = "C:\Program Files\PostgreSQL\14\bin",
        [string]$PgDumpPath = "",
        [string]$PgRestorePath = ""
    )

    if (-not $PgDumpPath) {
        $candidate = Join-Path $BinDirectory 'pg_dump.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $PgDumpPath = $candidate
        } else {
            $command = Get-Command pg_dump.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $PgDumpPath = $command.Source }
        }
    }
    if (-not $PgRestorePath) {
        $candidate = Join-Path $BinDirectory 'pg_restore.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $PgRestorePath = $candidate
        } else {
            $command = Get-Command pg_restore.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $PgRestorePath = $command.Source }
        }
    }

    foreach ($tool in @(
        [PSCustomObject]@{ Name = 'pg_dump'; Path = $PgDumpPath },
        [PSCustomObject]@{ Name = 'pg_restore'; Path = $PgRestorePath }
    )) {
        if (-not $tool.Path -or -not (Test-Path -LiteralPath $tool.Path -PathType Leaf)) {
            throw "$($tool.Name) do PostgreSQL 14 nao encontrado."
        }
    }

    $dumpVersion = Get-PplidPostgresToolVersion -Path $PgDumpPath
    $restoreVersion = Get-PplidPostgresToolVersion -Path $PgRestorePath
    if ($dumpVersion.Major -ne 14 -or $restoreVersion.Major -ne 14) {
        throw "pg_dump e pg_restore devem ser da versao principal 14."
    }

    return [PSCustomObject]@{
        PgDumpPath       = (Resolve-Path -LiteralPath $PgDumpPath).Path
        PgRestorePath    = (Resolve-Path -LiteralPath $PgRestorePath).Path
        PgDumpVersion    = $dumpVersion.Text
        PgRestoreVersion = $restoreVersion.Text
        MajorVersion     = 14
    }
}

function Get-PplidPostgresEnvSettings {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$BackendEnvPath
    )

    $keys = @('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_HOST', 'POSTGRES_PORT')
    $envMap = Read-PplidBackendEnv -Path $BackendEnvPath -RequiredKeys $keys

    $port = 0
    if (-not [int]::TryParse([string]$envMap.POSTGRES_PORT, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "POSTGRES_PORT invalida no backend.env."
    }

    $settings = [PSCustomObject]@{
        Database = [string]$envMap.POSTGRES_DB
        Username = [string]$envMap.POSTGRES_USER
        HostName = [string]$envMap.POSTGRES_HOST
        Port     = $port
    }
    $settings.PSObject.TypeNames.Insert(0, 'Pplid.Postgres.BackupSettings')
    $settings | Add-Member -MemberType ScriptMethod -Name ToString -Value { 'Pplid PostgreSQL backup settings (secret redacted)' } -Force
    return $settings
}

function Test-PplidPostgresBackupPreflight {
    <#
    .SYNOPSIS
    Performs local, non-network preflight checks. It never connects to PostgreSQL.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$BackendEnvPath,
        [Parameter(Mandatory = $true)]
        [string]$DestinationPath,
        [long]$MinimumFreeBytes = 104857600,
        [string]$BinDirectory = "C:\Program Files\PostgreSQL\14\bin",
        [string]$PgDumpPath = "",
        [string]$PgRestorePath = "",
        [switch]$AllowExisting,
        [switch]$ThrowOnFailure
    )

    $checks = [System.Collections.Generic.List[object]]::new()
    $tools = $null
    $settings = $null

    try {
        $tools = Resolve-PplidPostgres14Tools -BinDirectory $BinDirectory -PgDumpPath $PgDumpPath -PgRestorePath $PgRestorePath
        $checks.Add([PSCustomObject]@{ Name = 'PostgreSQL14Tools'; Passed = $true; Detail = 'pg_dump e pg_restore 14 encontrados.' })
    } catch {
        $checks.Add([PSCustomObject]@{ Name = 'PostgreSQL14Tools'; Passed = $false; Detail = $_.Exception.Message })
    }

    try {
        $settings = Get-PplidPostgresEnvSettings -BackendEnvPath $BackendEnvPath
        Read-PplidBackendEnv -Path $BackendEnvPath -RequiredKeys @('POSTGRES_PASSWORD') | Out-Null
        $checks.Add([PSCustomObject]@{ Name = 'BackendEnv'; Passed = $true; Detail = 'Configuracao obrigatoria presente; segredo omitido.' })
    } catch {
        $checks.Add([PSCustomObject]@{ Name = 'BackendEnv'; Passed = $false; Detail = $_.Exception.Message })
    }

    $hasPartialExtension = $DestinationPath.EndsWith('.partial', [System.StringComparison]::OrdinalIgnoreCase)
    $checks.Add([PSCustomObject]@{ Name = 'PartialExtension'; Passed = $hasPartialExtension; Detail = if ($hasPartialExtension) { 'Destino termina em .partial.' } else { 'Destino deve terminar em .partial.' } })

    $parent = Split-Path -Path $DestinationPath -Parent
    if (-not $parent) { $parent = (Get-Location).Path }
    $parentExists = Test-Path -LiteralPath $parent -PathType Container
    $checks.Add([PSCustomObject]@{ Name = 'DestinationDirectory'; Passed = $parentExists; Detail = if ($parentExists) { "Diretorio existe: $parent" } else { "Diretorio nao existe: $parent" } })

    $destinationAvailable = $AllowExisting -or -not (Test-Path -LiteralPath $DestinationPath)
    $checks.Add([PSCustomObject]@{ Name = 'DestinationAvailable'; Passed = $destinationAvailable; Detail = if ($destinationAvailable) { 'Destino disponivel.' } else { 'Destino ja existe; use -AllowExisting conscientemente.' } })

    if ($parentExists) {
        try {
            $root = [System.IO.Path]::GetPathRoot((Resolve-Path -LiteralPath $parent).Path)
            $freeBytes = ([System.IO.DriveInfo]::new($root)).AvailableFreeSpace
            $enoughSpace = $freeBytes -ge $MinimumFreeBytes
            $checks.Add([PSCustomObject]@{ Name = 'FreeSpace'; Passed = $enoughSpace; Detail = "Disponivel=$freeBytes; minimo=$MinimumFreeBytes bytes." })
        } catch {
            $checks.Add([PSCustomObject]@{ Name = 'FreeSpace'; Passed = $false; Detail = 'Nao foi possivel consultar o espaco livre.' })
        }
    } else {
        $checks.Add([PSCustomObject]@{ Name = 'FreeSpace'; Passed = $false; Detail = 'Diretorio de destino indisponivel.' })
    }

    $failed = @($checks | Where-Object { -not $_.Passed })
    $result = [PSCustomObject]@{
        Passed          = ($failed.Count -eq 0)
        Checks          = $checks.ToArray()
        Tools           = $tools
        Database        = if ($settings) { $settings.Database } else { $null }
        HostName        = if ($settings) { $settings.HostName } else { $null }
        Port            = if ($settings) { $settings.Port } else { $null }
        DestinationPath = $DestinationPath
    }

    if ($ThrowOnFailure -and -not $result.Passed) {
        $names = ($failed | ForEach-Object { $_.Name }) -join ', '
        throw "Preflight de backup falhou: $names"
    }
    return $result
}

function ConvertTo-PplidNativeArgument {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Value)

    if ($Value -notmatch '[\s"]' -and $Value.Length -gt 0) {
        return $Value
    }

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Protect-PplidSecretText {
    [CmdletBinding()]
    param(
        [AllowEmptyString()][string]$Text,
        [AllowEmptyString()][string]$Secret
    )

    if (-not $Text -or -not $Secret) { return $Text }
    return $Text.Replace($Secret, '[REDACTED]')
}

function Invoke-PplidNativeProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [hashtable]$EnvironmentVariables = @{},
        [string[]]$SecretsToRedact = @(),
        [scriptblock]$Observer,
        [ValidateRange(1, 86400)][int]$ObserverIntervalSeconds = 30,
        [ValidateSet('Idle', 'BelowNormal', 'Normal', 'AboveNormal', 'High', 'RealTime')]
        [string]$PriorityClass = 'Normal'
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-PplidNativeArgument -Value $_ }) -join ' ')
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($name in $EnvironmentVariables.Keys) {
        $startInfo.EnvironmentVariables[$name] = [string]$EnvironmentVariables[$name]
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $startedAt = [DateTime]::UtcNow
    $observations = [System.Collections.Generic.List[object]]::new()
    $priorityApplied = $false
    $priorityError = $null
    $stdout = ''
    $stderr = ''
    $exitCode = $null
    try {
        if (-not $process.Start()) {
            throw "Falha ao iniciar processo externo."
        }
        try {
            $process.PriorityClass = [System.Diagnostics.ProcessPriorityClass]::$PriorityClass
            $priorityApplied = $true
        } catch {
            $priorityError = $_.Exception.Message
            foreach ($secret in $SecretsToRedact) {
                $priorityError = Protect-PplidSecretText -Text $priorityError -Secret $secret
            }
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $waitMilliseconds = [int]([Math]::Min([int64]$ObserverIntervalSeconds * 1000, [int]::MaxValue))
        while (-not $process.WaitForExit($waitMilliseconds)) {
            if ($Observer) {
                $observedAt = [DateTime]::UtcNow
                $observerSucceeded = $true
                $observerError = $null
                try {
                    & $Observer ([PSCustomObject]@{
                        ProcessId          = $process.Id
                        FilePath           = $FilePath
                        StartedAtUtc       = $startedAt
                        ObservedAtUtc      = $observedAt
                        ElapsedSeconds     = [Math]::Round(($observedAt - $startedAt).TotalSeconds, 3)
                        PriorityRequested = $PriorityClass
                        PriorityApplied   = $priorityApplied
                    }) | Out-Null
                } catch {
                    $observerSucceeded = $false
                    $observerError = $_.Exception.Message
                    foreach ($secret in $SecretsToRedact) {
                        $observerError = Protect-PplidSecretText -Text $observerError -Secret $secret
                    }
                }
                $observations.Add([PSCustomObject]@{
                    ObservedAtUtc  = $observedAt
                    ElapsedSeconds = [Math]::Round(($observedAt - $startedAt).TotalSeconds, 3)
                    Succeeded      = $observerSucceeded
                    Error          = $observerError
                })
            }
        }
        $process.WaitForExit()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        $exitCode = [int]$process.ExitCode
    } finally {
        $process.Dispose()
    }

    foreach ($secret in $SecretsToRedact) {
        $stdout = Protect-PplidSecretText -Text $stdout -Secret $secret
        $stderr = Protect-PplidSecretText -Text $stderr -Secret $secret
    }

    return [PSCustomObject]@{
        ExitCode    = $exitCode
        StandardOut = $stdout
        StandardErr = $stderr
        StartedAtUtc = $startedAt
        FinishedAtUtc = [DateTime]::UtcNow
        PriorityRequested = $PriorityClass
        PriorityApplied = $priorityApplied
        PriorityError = $priorityError
        ObserverIntervalSeconds = $ObserverIntervalSeconds
        WatchdogObservations = $observations.ToArray()
    }
}

function Invoke-PplidPostgresDump {
    <#
    .SYNOPSIS
    Runs pg_dump in custom format with compression level 1 into a .partial file.
    #>
    [CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
    param(
        [Parameter(Mandatory = $true)][string]$BackendEnvPath,
        [Parameter(Mandatory = $true)][string]$PgDumpPath,
        [Parameter(Mandatory = $true)][string]$DestinationPath,
        [scriptblock]$Observer,
        [ValidateRange(1, 86400)][int]$ObserverIntervalSeconds = 30,
        [switch]$Force
    )

    if (-not $DestinationPath.EndsWith('.partial', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "DestinationPath deve terminar em .partial."
    }
    if (-not $PSCmdlet.ShouldProcess($DestinationPath, 'Executar pg_dump custom com compressao 1')) {
        return [PSCustomObject]@{
            Succeeded       = $false
            Skipped         = $true
            ExitCode        = $null
            DestinationPath = $DestinationPath
            StandardErr     = ""
            StartedAtUtc    = $null
            FinishedAtUtc   = $null
            PriorityRequested = 'BelowNormal'
            PriorityApplied = $false
            PriorityError   = $null
            ObserverIntervalSeconds = $ObserverIntervalSeconds
            WatchdogObservations = @()
        }
    }

    if (-not (Test-Path -LiteralPath $PgDumpPath -PathType Leaf)) {
        throw "pg_dump nao encontrado: $PgDumpPath"
    }
    $parent = Split-Path -Path $DestinationPath -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Diretorio de destino nao existe: $parent"
    }
    if ((Test-Path -LiteralPath $DestinationPath) -and -not $Force) {
        throw "Destino .partial ja existe: $DestinationPath"
    }

    $settings = Get-PplidPostgresEnvSettings -BackendEnvPath $BackendEnvPath
    $arguments = @(
        '--format=custom',
        '--compress=1',
        '--no-password',
        "--file=$DestinationPath",
        "--host=$($settings.HostName)",
        "--port=$($settings.Port)",
        "--username=$($settings.Username)",
        $settings.Database
    )

    $password = $null
    try {
        $passwordMap = Read-PplidBackendEnv -Path $BackendEnvPath -RequiredKeys @('POSTGRES_PASSWORD')
        $password = [string]$passwordMap.POSTGRES_PASSWORD
        $passwordMap = $null

        $nativeResult = Invoke-PplidNativeProcess -FilePath $PgDumpPath -Arguments $arguments `
            -EnvironmentVariables @{ PGPASSWORD = $password } -SecretsToRedact @($password) `
            -Observer $Observer -ObserverIntervalSeconds $ObserverIntervalSeconds -PriorityClass 'BelowNormal'
    } finally {
        $password = $null
        $passwordMap = $null
    }

    return [PSCustomObject]@{
        Succeeded       = ($nativeResult.ExitCode -eq 0)
        Skipped         = $false
        ExitCode        = $nativeResult.ExitCode
        DestinationPath = $DestinationPath
        StandardErr     = $nativeResult.StandardErr
        StartedAtUtc    = $nativeResult.StartedAtUtc
        FinishedAtUtc   = $nativeResult.FinishedAtUtc
        PriorityRequested = $nativeResult.PriorityRequested
        PriorityApplied = $nativeResult.PriorityApplied
        PriorityError   = $nativeResult.PriorityError
        ObserverIntervalSeconds = $nativeResult.ObserverIntervalSeconds
        WatchdogObservations = @($nativeResult.WatchdogObservations)
    }
}

function Test-PplidPostgresDump {
    <#
    .SYNOPSIS
    Validates a custom-format dump by running pg_restore --list.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$PgRestorePath,
        [Parameter(Mandatory = $true)][string]$DumpPath
    )

    if (-not (Test-Path -LiteralPath $PgRestorePath -PathType Leaf)) {
        throw "pg_restore nao encontrado: $PgRestorePath"
    }
    if (-not (Test-Path -LiteralPath $DumpPath -PathType Leaf)) {
        throw "Dump nao encontrado: $DumpPath"
    }

    $nativeResult = Invoke-PplidNativeProcess -FilePath $PgRestorePath -Arguments @('--list', $DumpPath)
    $entryCount = @($nativeResult.StandardOut -split "`r?`n" | Where-Object { $_ -and -not $_.StartsWith(';') }).Count
    return [PSCustomObject]@{
        Valid       = ($nativeResult.ExitCode -eq 0 -and $entryCount -gt 0)
        ExitCode    = $nativeResult.ExitCode
        EntryCount  = $entryCount
        StandardErr = $nativeResult.StandardErr
        DumpPath    = $DumpPath
    }
}

function Get-PplidPostgresBackupMetadata {
    <#
    .SYNOPSIS
    Returns SHA-256 and basic, non-secret metadata for a completed dump file.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$DumpPath,
        [string]$DatabaseName = ""
    )

    if (-not (Test-Path -LiteralPath $DumpPath -PathType Leaf)) {
        throw "Dump nao encontrado: $DumpPath"
    }
    $file = Get-Item -LiteralPath $DumpPath
    $hash = Get-FileHash -LiteralPath $DumpPath -Algorithm SHA256

    return [PSCustomObject]@{
        FileName         = $file.Name
        FullPath         = $file.FullName
        SizeBytes        = [long]$file.Length
        Sha256           = $hash.Hash.ToLowerInvariant()
        Database         = $DatabaseName
        LastWriteTimeUtc = $file.LastWriteTimeUtc
        GeneratedAtUtc   = [DateTime]::UtcNow
    }
}
