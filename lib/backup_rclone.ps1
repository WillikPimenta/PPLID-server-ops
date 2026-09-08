Set-StrictMode -Version 2.0

function ConvertTo-PplidRcloneArgumentString {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string[]]$ArgumentList)

    $quoted = foreach ($argument in $ArgumentList) {
        if ($argument -notmatch '[\s"]') { $argument; continue }
        '"' + ($argument -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
    }
    return ($quoted -join ' ')
}

function Invoke-PplidRcloneProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = ConvertTo-PplidRcloneArgumentString -ArgumentList $ArgumentList
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw 'Failed to start rclone.' }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        return [PSCustomObject]@{ ExitCode = $process.ExitCode; StdOut = $stdout; StdErr = $stderr }
    } finally {
        $process.Dispose()
    }
}

function Resolve-PplidRcloneConfiguration {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$BinaryPath,
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$Remote
    )

    if ($Remote -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}:$') {
        throw 'Remote must be an explicit rclone remote name ending in a colon.'
    }
    $binary = [IO.Path]::GetFullPath($BinaryPath)
    $config = [IO.Path]::GetFullPath($ConfigPath)
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw "rclone binary not found: $binary" }
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { throw "rclone config not found: $config" }
    if ([IO.Path]::GetExtension($binary) -ine '.exe') { throw 'rclone binary must be an .exe file.' }

    return [PSCustomObject]@{ BinaryPath = $binary; ConfigPath = $config; Remote = $Remote }
}

function Test-PplidRcloneStaticPreflight {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$BinaryPath,
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$Remote
    )
    try {
        $resolved = Resolve-PplidRcloneConfiguration @PSBoundParameters
        return [PSCustomObject]@{ Passed = $true; Classification = 'ready'; Configuration = $resolved; Message = 'Static preflight passed; no network call was made.' }
    } catch {
        return [PSCustomObject]@{ Passed = $false; Classification = 'configuration_error'; Configuration = $null; Message = $_.Exception.Message }
    }
}

function Assert-PplidRcloneSafeSegment {
    param([Parameter(Mandatory = $true)][string]$Value, [string]$Name = 'path segment')
    $isReservedIncoming = ($Value -ceq '_incoming')
    $isOrdinarySegment = ($Value -match '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
    if ((-not $isReservedIncoming -and -not $isOrdinarySegment) -or $Value -eq '.' -or $Value -eq '..') {
        throw "Unsafe rclone $Name."
    }
}

function Get-PplidRcloneRemotePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}:$')][string]$Remote,
        [Parameter(Mandatory = $true)][ValidateSet('MAIN','DEV','HOM')][string]$Environment,
        [string[]]$ChildSegment
    )
    $segments = @('PPLID', 'Backups', $Environment.ToUpperInvariant())
    foreach ($segment in @($ChildSegment)) {
        Assert-PplidRcloneSafeSegment -Value $segment
        $segments += $segment
    }
    return $Remote + ($segments -join '/')
}

function Get-PplidRcloneCommonArguments {
    param([Parameter(Mandatory = $true)][string]$ConfigPath)
    return @('--config', $ConfigPath, '--transfers', '1', '--checkers', '2', '--onedrive-chunk-size', '10Mi', '--retries', '3', '--low-level-retries', '10', '--no-update-modtime')
}

function Protect-PplidRcloneText {
    param([AllowNull()][string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return '' }
    $redacted = $Text -replace '(?i)("(?:access_token|refresh_token|client_secret)"\s*:\s*")[^"]*(")', '$1<redacted>$2'
    $redacted = $redacted -replace '(?i)(access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password)\s*[:=]\s*\S+', '$1=<redacted>'
    $redacted = $redacted -replace '(?i)\bBearer\s+[A-Za-z0-9._~+/\-=]+', 'Bearer <redacted>'
    return $redacted
}

function Get-PplidRcloneFailureClassification {
    param([AllowNull()][string]$Text)
    if ($Text -match '(?i)(invalid_grant|unauthori[sz]ed|authentication|token.{0,20}expired|access denied|\b401\b|\b403\b)') { return 'auth_required' }
    if ($Text -match '(?i)(quota|insufficient storage|storageLimitExceeded|\b507\b)') { return 'remote_quota_low' }
    return 'upload_pending'
}

function Invoke-PplidRcloneCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Configuration,
        [Parameter(Mandatory = $true)][string[]]$CommandArguments,
        [scriptblock]$Executor
    )
    $forbidden = ($CommandArguments -join ' ')
    if ($forbidden -match '(?i)(config[_-]?pass|password|access[_-]?token|refresh[_-]?token|client[_-]?secret)') {
        throw 'Secret-bearing rclone arguments are prohibited.'
    }
    $arguments = @($CommandArguments + (Get-PplidRcloneCommonArguments -ConfigPath $Configuration.ConfigPath))
    if ($null -eq $Executor) {
        $raw = Invoke-PplidRcloneProcess -FilePath $Configuration.BinaryPath -ArgumentList $arguments
    } else {
        $raw = & $Executor $Configuration.BinaryPath $arguments
    }
    if ($null -eq $raw -or $null -eq $raw.ExitCode) { throw 'The rclone executor returned an invalid result.' }
    return [PSCustomObject]@{
        ExitCode = [int]$raw.ExitCode
        StdOut = Protect-PplidRcloneText -Text ([string]$raw.StdOut)
        StdErr = Protect-PplidRcloneText -Text ([string]$raw.StdErr)
        Arguments = $arguments
    }
}

function Get-PplidRcloneDirectoryInventory {
    param($Configuration, [string]$RemotePath, [scriptblock]$Executor)
    $result = Invoke-PplidRcloneCommand -Configuration $Configuration -CommandArguments @('lsjson', $RemotePath, '--files-only', '--max-depth', '1', '--hash', '--hash-type', 'QuickXorHash') -Executor $Executor
    if ($result.ExitCode -ne 0) { return [PSCustomObject]@{ Success = $false; Command = $result; Items = @() } }
    try {
        $parsed = $result.StdOut | ConvertFrom-Json
        if ($null -eq $parsed) {
            $items = @()
        } elseif ($parsed -is [Array]) {
            $items = @($parsed)
        } else {
            $items = @($parsed)
        }
    } catch {
        return [PSCustomObject]@{ Success = $false; Command = $result; Items = @() }
    }
    return [PSCustomObject]@{ Success = $true; Command = $result; Items = $items }
}

function Get-PplidRcloneItemQuickXorHash {
    param([Parameter(Mandatory = $true)]$Item)
    $hashesProperty = $Item.PSObject.Properties['Hashes']
    if ($null -eq $hashesProperty -or $null -eq $hashesProperty.Value) { return $null }
    $hashes = $hashesProperty.Value
    if ($hashes -is [Collections.IDictionary]) {
        foreach ($key in $hashes.Keys) {
            if ([string]$key -ieq 'QuickXorHash') { return [string]$hashes[$key] }
        }
        return $null
    }
    $quickProperty = @($hashes.PSObject.Properties | Where-Object { $_.Name -ieq 'QuickXorHash' } | Select-Object -First 1)
    if ($quickProperty.Count -eq 0) { return $null }
    return [string]$quickProperty[0].Value
}

function Get-PplidRcloneInventoryFileState {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Items,
        [Parameter(Mandatory = $true)]$ExpectedFile
    )
    $matches = @($Items | Where-Object { [string]$_.Name -ceq [string]$ExpectedFile.Name })
    if ($matches.Count -eq 0) { return [PSCustomObject]@{ State = 'missing'; Item = $null } }
    if ($matches.Count -ne 1) { return [PSCustomObject]@{ State = 'conflict'; Item = $null } }
    $item = $matches[0]
    $sizeProperty = $item.PSObject.Properties['Size']
    if ($null -eq $sizeProperty) { return [PSCustomObject]@{ State = 'conflict'; Item = $item } }
    try { $actualSize = [int64]$sizeProperty.Value } catch { return [PSCustomObject]@{ State = 'conflict'; Item = $item } }
    $actualHash = Get-PplidRcloneItemQuickXorHash -Item $item
    $equivalent = (
        $actualSize -eq [int64]$ExpectedFile.Size -and
        -not [string]::IsNullOrWhiteSpace($actualHash) -and
        [string]::Equals($actualHash, [string]$ExpectedFile.QuickXorHash, [StringComparison]::OrdinalIgnoreCase)
    )
    return [PSCustomObject]@{ State = $(if ($equivalent) { 'equivalent' } else { 'conflict' }); Item = $item }
}

function Test-PplidRcloneInventoryFiles {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Items,
        [Parameter(Mandatory = $true)][object[]]$ExpectedFiles
    )
    foreach ($expected in $ExpectedFiles) {
        if ((Get-PplidRcloneInventoryFileState -Items $Items -ExpectedFile $expected).State -ne 'equivalent') {
            return $false
        }
    }
    return $true
}

function Get-PplidRcloneLocalQuickXorHash {
    param(
        [Parameter(Mandatory = $true)]$Configuration,
        [Parameter(Mandatory = $true)][string]$LocalPath,
        [scriptblock]$Executor
    )
    $command = Invoke-PplidRcloneCommand -Configuration $Configuration -CommandArguments @('hashsum', 'QuickXorHash', [IO.Path]::GetFullPath($LocalPath)) -Executor $Executor
    if ($command.ExitCode -ne 0) {
        return [PSCustomObject]@{ Success = $false; Hash = $null; Command = $command }
    }
    $line = @($command.StdOut -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
    if ($line.Count -ne 1 -or $line[0] -notmatch '^([^\s]+)\s+') {
        return [PSCustomObject]@{ Success = $false; Hash = $null; Command = $command }
    }
    return [PSCustomObject]@{ Success = $true; Hash = [string]$Matches[1]; Command = $command }
}

function Test-PplidRcloneMissingDirectoryError {
    param([AllowNull()][string]$Text)
    return ($Text -match '(?i)(directory|path|object).{0,40}(not found|does not exist|missing)|not found.{0,40}(directory|path|object)')
}

function Publish-PplidBackupWithRclone {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })][string]$DumpPath,
        [Parameter(Mandatory = $true)][ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })][string]$ManifestPath,
        [Parameter(Mandatory = $true)][ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })][string]$Sha256Path,
        [Parameter(Mandatory = $true)][ValidateSet('MAIN','DEV','HOM')][string]$Environment,
        [Parameter(Mandatory = $true)]$Configuration,
        [AllowEmptyString()][string]$RunId = '',
        [scriptblock]$Executor
    )

    $dumpName = [IO.Path]::GetFileName($DumpPath)
    $baseName = [IO.Path]::GetFileNameWithoutExtension($dumpName)
    if ($dumpName -notmatch ('^pplid_' + $Environment.ToLowerInvariant() + '_\d{8}_\d{6}\.dump$')) { throw 'Dump name does not match the controlled PPLID backup pattern.' }
    $manifestName = $baseName + '.manifest.json'
    $shaName = $baseName + '.sha256'
    if ([IO.Path]::GetFileName($ManifestPath) -ne $manifestName -or [IO.Path]::GetFileName($Sha256Path) -ne $shaName) { throw 'Backup sidecar names do not match the dump basename.' }

    $incomingSegment = if ([string]::IsNullOrWhiteSpace($RunId)) { $baseName } else { $RunId }
    Assert-PplidRcloneSafeSegment -Value $incomingSegment -Name 'run id'
    $finalRoot = Get-PplidRcloneRemotePath -Remote $Configuration.Remote -Environment $Environment
    $incomingRoot = Get-PplidRcloneRemotePath -Remote $Configuration.Remote -Environment $Environment -ChildSegment @('_incoming', $incomingSegment)
    $commands = New-Object System.Collections.Generic.List[object]
    $expectedFiles = @(
        [PSCustomObject]@{ Name = $dumpName; LocalPath = [IO.Path]::GetFullPath($DumpPath); Size = [int64](Get-Item -LiteralPath $DumpPath).Length; QuickXorHash = $null },
        [PSCustomObject]@{ Name = $manifestName; LocalPath = [IO.Path]::GetFullPath($ManifestPath); Size = [int64](Get-Item -LiteralPath $ManifestPath).Length; QuickXorHash = $null },
        [PSCustomObject]@{ Name = $shaName; LocalPath = [IO.Path]::GetFullPath($Sha256Path); Size = [int64](Get-Item -LiteralPath $Sha256Path).Length; QuickXorHash = $null }
    )
    foreach ($expected in $expectedFiles) {
        $localHash = Get-PplidRcloneLocalQuickXorHash -Configuration $Configuration -LocalPath $expected.LocalPath -Executor $Executor
        $commands.Add($localHash.Command)
        if (-not $localHash.Success) {
            return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $localHash.Command.StdErr); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
        }
        $expected.QuickXorHash = $localHash.Hash
    }

    $finalInventory = Get-PplidRcloneDirectoryInventory -Configuration $Configuration -RemotePath $finalRoot -Executor $Executor
    $commands.Add($finalInventory.Command)
    if (-not $finalInventory.Success -and (Test-PplidRcloneMissingDirectoryError -Text $finalInventory.Command.StdErr)) {
        $mkdir = Invoke-PplidRcloneCommand -Configuration $Configuration -CommandArguments @('mkdir', $finalRoot) -Executor $Executor
        $commands.Add($mkdir)
        if ($mkdir.ExitCode -ne 0) {
            return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $mkdir.StdErr); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
        }
        $finalInventory = Get-PplidRcloneDirectoryInventory -Configuration $Configuration -RemotePath $finalRoot -Executor $Executor
        $commands.Add($finalInventory.Command)
    }
    if (-not $finalInventory.Success) {
        return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $finalInventory.Command.StdErr); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
    }

    $initialStates = @{}
    foreach ($expected in $expectedFiles) {
        $state = Get-PplidRcloneInventoryFileState -Items $finalInventory.Items -ExpectedFile $expected
        $initialStates[$expected.Name] = $state.State
        if ($state.State -eq 'conflict') {
            return [PSCustomObject]@{ Success = $false; Classification = 'conflict'; Published = $false; AlreadyPublished = $false; ConflictName = $expected.Name; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
        }
    }

    $incomingInventory = Get-PplidRcloneDirectoryInventory -Configuration $Configuration -RemotePath $incomingRoot -Executor $Executor
    $commands.Add($incomingInventory.Command)
    if (-not $incomingInventory.Success) {
        if (Test-PplidRcloneMissingDirectoryError -Text $incomingInventory.Command.StdErr) {
            $incomingInventory = [PSCustomObject]@{ Success = $true; Items = @(); Command = $incomingInventory.Command }
        } else {
            return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $incomingInventory.Command.StdErr); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
        }
    }

    $allFinalEquivalent = (@($initialStates.Values | Where-Object { $_ -eq 'equivalent' }).Count -eq $expectedFiles.Count)
    if ($allFinalEquivalent) {
        return [PSCustomObject]@{ Success = $true; Classification = 'success'; Published = $true; AlreadyPublished = $true; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; DumpRemotePath = "$finalRoot/$dumpName"; ManifestRemotePath = "$finalRoot/$manifestName"; Sha256RemotePath = "$finalRoot/$shaName"; OrphanIncoming = @($incomingInventory.Items); Commands = $commands.ToArray() }
    }

    foreach ($expected in $expectedFiles) {
        if ($initialStates[$expected.Name] -eq 'equivalent') { continue }
        $incomingState = Get-PplidRcloneInventoryFileState -Items $incomingInventory.Items -ExpectedFile $expected
        if ($incomingState.State -eq 'conflict') {
            return [PSCustomObject]@{ Success = $false; Classification = 'conflict'; Published = $false; AlreadyPublished = $false; ConflictName = $expected.Name; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @($incomingInventory.Items); Commands = $commands.ToArray() }
        }
        if ($incomingState.State -eq 'missing') {
            $copy = Invoke-PplidRcloneCommand -Configuration $Configuration -CommandArguments @('copyto', $expected.LocalPath, "$incomingRoot/$($expected.Name)", '--immutable') -Executor $Executor
            $commands.Add($copy)
            if ($copy.ExitCode -ne 0) {
                return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $copy.StdErr); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @($incomingInventory.Items); Commands = $commands.ToArray() }
            }
            $incomingInventory = Get-PplidRcloneDirectoryInventory -Configuration $Configuration -RemotePath $incomingRoot -Executor $Executor
            $commands.Add($incomingInventory.Command)
            if (-not $incomingInventory.Success) {
                return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $incomingInventory.Command.StdErr); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
            }
            $copiedState = Get-PplidRcloneInventoryFileState -Items $incomingInventory.Items -ExpectedFile $expected
            if ($copiedState.State -ne 'equivalent') {
                $classification = if ($copiedState.State -eq 'conflict') { 'conflict' } else { 'upload_pending' }
                return [PSCustomObject]@{ Success = $false; Classification = $classification; Published = $false; AlreadyPublished = $false; ConflictName = $(if ($classification -eq 'conflict') { $expected.Name } else { $null }); FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @($incomingInventory.Items); Commands = $commands.ToArray() }
            }
        }
    }

    $commitOrder = @(
        @($expectedFiles | Where-Object { $_.Name -ceq $manifestName })[0],
        @($expectedFiles | Where-Object { $_.Name -ceq $shaName })[0],
        @($expectedFiles | Where-Object { $_.Name -ceq $dumpName })[0]
    )
    foreach ($expected in $commitOrder) {
        $destinationInventory = Get-PplidRcloneDirectoryInventory -Configuration $Configuration -RemotePath $finalRoot -Executor $Executor
        $commands.Add($destinationInventory.Command)
        if (-not $destinationInventory.Success) {
            return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $destinationInventory.Command.StdErr); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
        }
        $destinationState = Get-PplidRcloneInventoryFileState -Items $destinationInventory.Items -ExpectedFile $expected
        if ($destinationState.State -eq 'conflict') {
            return [PSCustomObject]@{ Success = $false; Classification = 'conflict'; Published = $false; AlreadyPublished = $false; ConflictName = $expected.Name; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
        }
        if ($destinationState.State -eq 'equivalent') { continue }

        $sourceInventory = Get-PplidRcloneDirectoryInventory -Configuration $Configuration -RemotePath $incomingRoot -Executor $Executor
        $commands.Add($sourceInventory.Command)
        if (-not $sourceInventory.Success) {
            return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $sourceInventory.Command.StdErr); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
        }
        $sourceState = Get-PplidRcloneInventoryFileState -Items $sourceInventory.Items -ExpectedFile $expected
        if ($sourceState.State -ne 'equivalent') {
            $classification = if ($sourceState.State -eq 'conflict') { 'conflict' } else { 'upload_pending' }
            return [PSCustomObject]@{ Success = $false; Classification = $classification; Published = $false; AlreadyPublished = $false; ConflictName = $(if ($classification -eq 'conflict') { $expected.Name } else { $null }); FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @($sourceInventory.Items); Commands = $commands.ToArray() }
        }

        $move = Invoke-PplidRcloneCommand -Configuration $Configuration -CommandArguments @('moveto', "$incomingRoot/$($expected.Name)", "$finalRoot/$($expected.Name)", '--immutable') -Executor $Executor
        $commands.Add($move)
        if ($move.ExitCode -ne 0) {
            return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $move.StdErr); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @($sourceInventory.Items); Commands = $commands.ToArray() }
        }
    }

    $confirmation = Get-PplidRcloneDirectoryInventory -Configuration $Configuration -RemotePath $finalRoot -Executor $Executor
    $commands.Add($confirmation.Command)
    if (-not $confirmation.Success -or -not (Test-PplidRcloneInventoryFiles -Items $confirmation.Items -ExpectedFiles $expectedFiles)) {
        $detail = if ($confirmation.Success) { 'final set name, size, or QuickXorHash mismatch' } else { $confirmation.Command.StdErr }
        return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $detail); Published = $false; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; OrphanIncoming = @(); Commands = $commands.ToArray() }
    }
    $remainingIncoming = Get-PplidRcloneDirectoryInventory -Configuration $Configuration -RemotePath $incomingRoot -Executor $Executor
    $commands.Add($remainingIncoming.Command)
    $orphans = if ($remainingIncoming.Success) { @($remainingIncoming.Items) } else { @() }
    return [PSCustomObject]@{ Success = $true; Classification = 'success'; Published = $true; AlreadyPublished = $false; FinalRoot = $finalRoot; IncomingRoot = $incomingRoot; DumpRemotePath = "$finalRoot/$dumpName"; ManifestRemotePath = "$finalRoot/$manifestName"; Sha256RemotePath = "$finalRoot/$shaName"; OrphanIncoming = $orphans; Commands = $commands.ToArray() }
}

function Get-PplidRcloneQuota {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Configuration, [scriptblock]$Executor)
    $result = Invoke-PplidRcloneCommand -Configuration $Configuration -CommandArguments @('about', $Configuration.Remote, '--json') -Executor $Executor
    if ($result.ExitCode -ne 0) {
        return [PSCustomObject]@{ Success = $false; Classification = (Get-PplidRcloneFailureClassification -Text $result.StdErr); TotalBytes = $null; UsedBytes = $null; FreeBytes = $null }
    }
    try {
        $quota = $result.StdOut | ConvertFrom-Json
        return [PSCustomObject]@{ Success = $true; Classification = 'success'; TotalBytes = [int64]$quota.total; UsedBytes = [int64]$quota.used; FreeBytes = [int64]$quota.free }
    } catch {
        return [PSCustomObject]@{ Success = $false; Classification = 'upload_pending'; TotalBytes = $null; UsedBytes = $null; FreeBytes = $null }
    }
}

function Get-PplidRcloneRetentionPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidateSet('MAIN','DEV','HOM')][string]$Environment,
        [Parameter(Mandatory = $true)][object[]]$Inventory,
        [ValidateRange(1,365)][int]$RetentionCount = 7,
        [string]$ProtectedDumpName
    )
    $pattern = '^pplid_' + $Environment.ToLowerInvariant() + '_(\d{8}_\d{6})\.dump$'
    $names = @($Inventory | ForEach-Object { [string]$_.Name })
    $sets = New-Object System.Collections.Generic.List[object]
    foreach ($dumpName in @($names | Where-Object { $_ -match $pattern })) {
        $base = $dumpName.Substring(0, $dumpName.Length - 5)
        if (($names -notcontains ($base + '.manifest.json')) -or ($names -notcontains ($base + '.sha256'))) { continue }
        $timestampText = [regex]::Match($dumpName, $pattern).Groups[1].Value
        $timestamp = [datetime]::ParseExact($timestampText, 'yyyyMMdd_HHmmss', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal)
        $sets.Add([PSCustomObject]@{ Dump = $dumpName; Manifest = $base + '.manifest.json'; Sha256 = $base + '.sha256'; TimestampUtc = $timestamp.ToUniversalTime() })
    }
    $ordered = @($sets | Sort-Object TimestampUtc -Descending)
    $keep = @($ordered | Select-Object -First $RetentionCount)
    $retire = @($ordered | Select-Object -Skip $RetentionCount | Where-Object { $_.Dump -ne $ProtectedDumpName })
    if (-not [string]::IsNullOrWhiteSpace($ProtectedDumpName)) {
        $protected = @($ordered | Where-Object { $_.Dump -eq $ProtectedDumpName })
        foreach ($item in $protected) { if (@($keep | Where-Object { $_.Dump -eq $item.Dump }).Count -eq 0) { $keep += $item } }
    }
    return [PSCustomObject]@{ DryRun = $true; RetentionCount = $RetentionCount; Keep = $keep; WouldRetire = $retire; MutationCommands = @() }
}
