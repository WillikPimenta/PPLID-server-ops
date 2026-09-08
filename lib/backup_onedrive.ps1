Set-StrictMode -Version 2.0

function Resolve-PplidOneDriveRoot {
    [CmdletBinding()]
    param(
        [string]$OneDriveRoot
    )

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($OneDriveRoot)) {
        $candidates.Add($OneDriveRoot)
    } else {
        foreach ($variableName in @("OneDriveCommercial", "OneDrive")) {
            $value = [Environment]::GetEnvironmentVariable($variableName, "Process")
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                $candidates.Add($value)
            }
        }
        if ($candidates.Count -eq 0) {
            $userProfile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
            if (-not [string]::IsNullOrWhiteSpace($userProfile)) {
                $profileDirectories = @(
                    Get-ChildItem -LiteralPath $userProfile -Directory -Filter 'OneDrive*' -ErrorAction SilentlyContinue |
                        Sort-Object -Property @{ Expression = { if ($_.Name -match '(?i)(EXPERIAN|Commercial)') { 0 } else { 1 } } },
                            @{ Expression = { $_.Name } }
                )
                foreach ($directory in $profileDirectories) {
                    $candidates.Add($directory.FullName)
                }
            }
        }
    }

    foreach ($candidate in $candidates) {
        try {
            $resolved = [IO.Path]::GetFullPath($candidate)
        } catch {
            continue
        }

        if (Test-Path -LiteralPath $resolved -PathType Container) {
            return $resolved.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($OneDriveRoot)) {
        throw "OneDrive root does not exist or is not a directory: $OneDriveRoot"
    }
    throw "OneDrive root not found. Set OneDriveCommercial or OneDrive, or pass -OneDriveRoot."
}

function Get-PplidOneDriveBackupDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[A-Za-z0-9_-]+$')]
        [string]$Environment,
        [string]$OneDriveRoot,
        [switch]$Create
    )

    $root = Resolve-PplidOneDriveRoot -OneDriveRoot $OneDriveRoot
    $directory = Join-Path (Join-Path (Join-Path $root "PPLID") "Backups") $Environment.ToUpperInvariant()
    if ($Create -and -not (Test-Path -LiteralPath $directory -PathType Container)) {
        New-Item -ItemType Directory -Path $directory -Force -ErrorAction Stop | Out-Null
    }
    return $directory
}

function Get-PplidBackupSetPaths {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$DumpPath
    )

    if ([IO.Path]::GetExtension($DumpPath) -ine ".dump") {
        throw "Backup dump must use the .dump extension: $DumpPath"
    }

    $directory = Split-Path -Parent $DumpPath
    $baseName = [IO.Path]::GetFileNameWithoutExtension($DumpPath)
    return [PSCustomObject]@{
        BaseName = $baseName
        Dump = $DumpPath
        Manifest = Join-Path $directory ($baseName + ".manifest.json")
        Sha256 = Join-Path $directory ($baseName + ".sha256")
    }
}

function Repair-PplidInterruptedOneDrivePublish {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
        [string]$LocalDumpPath,
        [Parameter(Mandatory = $true)]
        [ValidateScript({ Test-Path -LiteralPath $_ -PathType Container })]
        [string]$CloudDirectory,
        [switch]$ReplaceCompleteSet
    )

    if ([IO.Path]::GetExtension($LocalDumpPath) -ine '.dump') {
        throw 'LocalDumpPath must use the .dump extension.'
    }

    $localFullPath = [IO.Path]::GetFullPath($LocalDumpPath)
    $cloudFullPath = [IO.Path]::GetFullPath($CloudDirectory)
    $cloudDumpPath = Join-Path $cloudFullPath ([IO.Path]::GetFileName($localFullPath))
    $set = Get-PplidBackupSetPaths -DumpPath $cloudDumpPath
    $finalPaths = @($set.Dump, $set.Manifest, $set.Sha256)
    $uploadingPaths = @($finalPaths | ForEach-Object { $_ + '.uploading' })
    $complete = (@($finalPaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }).Count -eq $finalPaths.Count)
    $removed = New-Object System.Collections.Generic.List[string]

    if ($ReplaceCompleteSet -and -not (Test-Path -LiteralPath $localFullPath -PathType Leaf)) {
        throw 'ReplaceCompleteSet requires an existing local dump.'
    }

    $preserveCompleteSet = $complete -and -not $ReplaceCompleteSet
    $pathsToRemove = if ($preserveCompleteSet) { $uploadingPaths } else { @($finalPaths + $uploadingPaths) }
    foreach ($path in $pathsToRemove) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force -ErrorAction Stop
            $removed.Add($path)
        }
    }

    return [PSCustomObject]@{
        BaseName = $set.BaseName
        CompleteSetPreserved = $preserveCompleteSet
        ReplacedCompleteSet = ($complete -and $ReplaceCompleteSet)
        RemovedPaths = $removed.ToArray()
        LocalDumpPath = $localFullPath
    }
}

function Publish-PplidBackupToOneDrive {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
        [string]$SourceDumpPath,
        [Parameter(Mandatory = $true)]
        [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
        [string]$SourceManifestPath,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[A-Za-z0-9_-]+$')]
        [string]$Environment,
        [string]$OneDriveRoot
    )

    if ([IO.Path]::GetExtension($SourceDumpPath) -ine ".dump") {
        throw "Source dump must use the .dump extension: $SourceDumpPath"
    }

    $backupDirectory = Get-PplidOneDriveBackupDirectory -Environment $Environment -OneDriveRoot $OneDriveRoot -Create
    $destinationDump = Join-Path $backupDirectory ([IO.Path]::GetFileName($SourceDumpPath))
    $set = Get-PplidBackupSetPaths -DumpPath $destinationDump
    $finalPaths = @($set.Dump, $set.Manifest, $set.Sha256)
    $uploadingPaths = @($finalPaths | ForEach-Object { $_ + ".uploading" })

    foreach ($path in @($finalPaths + $uploadingPaths)) {
        if (Test-Path -LiteralPath $path) {
            throw "Destination backup path already exists: $path"
        }
    }

    $sourceHash = (Get-FileHash -LiteralPath $SourceDumpPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToUpperInvariant()
    $renamedFinals = New-Object System.Collections.Generic.List[string]
    try {
        Copy-Item -LiteralPath $SourceDumpPath -Destination $uploadingPaths[0] -ErrorAction Stop
        Copy-Item -LiteralPath $SourceManifestPath -Destination $uploadingPaths[1] -ErrorAction Stop

        $shaLine = $sourceHash + "  " + [IO.Path]::GetFileName($set.Dump) + [Environment]::NewLine
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($uploadingPaths[2], $shaLine, $utf8NoBom)

        $uploadedHash = (Get-FileHash -LiteralPath $uploadingPaths[0] -Algorithm SHA256 -ErrorAction Stop).Hash.ToUpperInvariant()
        if ($uploadedHash -cne $sourceHash) {
            throw "Uploaded dump checksum does not match the source dump."
        }

        foreach ($index in @(1, 2, 0)) {
            Move-Item -LiteralPath $uploadingPaths[$index] -Destination $finalPaths[$index] -ErrorAction Stop
            $renamedFinals.Add($finalPaths[$index])
        }

        $publishedAtUtc = [DateTime]::UtcNow
        foreach ($path in $finalPaths) {
            (Get-Item -LiteralPath $path -ErrorAction Stop).LastWriteTimeUtc = $publishedAtUtc
        }

        return [PSCustomObject]@{
            BaseName = $set.BaseName
            Directory = $backupDirectory
            DumpPath = $set.Dump
            ManifestPath = $set.Manifest
            Sha256Path = $set.Sha256
            Sha256 = $sourceHash
            PublishedAtUtc = $publishedAtUtc
        }
    } catch {
        foreach ($path in $uploadingPaths) {
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            }
        }
        foreach ($path in $renamedFinals) {
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            }
        }
        throw
    }
}

function Invoke-PplidAttribOnlineOnly {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    & attrib.exe +U -P $Path
    if ($LASTEXITCODE -ne 0) {
        throw "attrib.exe failed with exit code $LASTEXITCODE for: $Path"
    }
}

function Request-PplidOneDriveOnlineOnly {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string[]]$Path
    )

    foreach ($itemPath in $Path) {
        if (-not (Test-Path -LiteralPath $itemPath -PathType Leaf)) {
            throw "Cannot request online-only for a missing file: $itemPath"
        }
        Invoke-PplidAttribOnlineOnly -Path ([IO.Path]::GetFullPath($itemPath))
    }
}

function Get-PplidFileAttributes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )
    return [IO.File]::GetAttributes($Path)
}

function Test-PplidOneDriveOnlineOnly {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string[]]$Path
    )

    foreach ($itemPath in $Path) {
        if (-not (Test-Path -LiteralPath $itemPath -PathType Leaf)) {
            return $false
        }
        $attributes = Get-PplidFileAttributes -Path $itemPath
        $attributeBits = [int64]$attributes
        $isOffline = (($attributeBits -band [int64][IO.FileAttributes]::Offline) -ne 0)
        # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS is absent from older .NET enums.
        $isRecallOnDataAccess = (($attributeBits -band 0x00400000) -ne 0)
        if (-not ($isOffline -or $isRecallOnDataAccess)) {
            return $false
        }
    }
    return $true
}

function Wait-PplidOneDriveOnlineOnly {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string[]]$Path,
        [ValidateRange(0, 86400)]
        [int]$TimeoutSeconds = 3600,
        [ValidateRange(1, 3600)]
        [int]$PollIntervalSeconds = 10
    )

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    do {
        if (Test-PplidOneDriveOnlineOnly -Path $Path) {
            return $true
        }
        if ($stopwatch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
            return $false
        }
        Start-Sleep -Seconds $PollIntervalSeconds
    } while ($true)
}

function Get-PplidOneDriveRetentionCandidate {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [IO.FileInfo]$Dump,
        [Parameter(Mandatory = $true)]
        [ValidateSet('main', 'dev', 'hom')]
        [string]$Environment
    )

    $namePattern = '^pplid_(main|dev|hom)_([0-9]{8}_[0-9]{6})\.dump$'
    if ($Dump.Name -cnotmatch $namePattern) {
        return $null
    }
    $candidateEnvironment = $Matches[1]
    $timestampText = $Matches[2]
    if ($candidateEnvironment -cne $Environment) {
        return $null
    }

    $timestamp = [DateTime]::MinValue
    $dateStyles = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
    if (-not [DateTime]::TryParseExact(
        $timestampText,
        'yyyyMMdd_HHmmss',
        [Globalization.CultureInfo]::InvariantCulture,
        $dateStyles,
        [ref]$timestamp
    )) {
        return $null
    }

    $set = Get-PplidBackupSetPaths -DumpPath $Dump.FullName
    if (-not (Test-Path -LiteralPath $set.Manifest -PathType Leaf) -or
        -not (Test-Path -LiteralPath $set.Sha256 -PathType Leaf)) {
        return $null
    }

    try {
        $manifest = Get-Content -LiteralPath $set.Manifest -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $shaLine = (Get-Content -LiteralPath $set.Sha256 -Raw -Encoding UTF8 -ErrorAction Stop).Trim()
    } catch {
        return $null
    }

    if ($shaLine -cnotmatch '^([0-9A-Fa-f]{64})  ([^\r\n]+)$') {
        return $null
    }
    $lineHash = $Matches[1]
    $lineFileName = $Matches[2]

    $fileNameProperty = $manifest.PSObject.Properties['fileName']
    $environmentProperty = $manifest.PSObject.Properties['environment']
    $shaProperty = $manifest.PSObject.Properties['sha256']
    if ($null -eq $fileNameProperty -or $null -eq $environmentProperty -or $null -eq $shaProperty) {
        return $null
    }

    if ([string]$fileNameProperty.Value -cne $Dump.Name -or
        $lineFileName -cne $Dump.Name -or
        ([string]$environmentProperty.Value).ToLowerInvariant() -cne $Environment -or
        [string]$shaProperty.Value -cnotmatch '^[0-9A-Fa-f]{64}$' -or
        -not [string]::Equals([string]$shaProperty.Value, $lineHash, [StringComparison]::OrdinalIgnoreCase)) {
        return $null
    }

    return [PSCustomObject]@{
        Dump = $set.Dump
        Manifest = $set.Manifest
        Sha256 = $set.Sha256
        TimestampUtc = $timestamp
    }
}

function Invoke-PplidOneDriveRetention {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
        [string]$ConfirmedDumpPath,
        [Parameter(Mandatory = $true)]
        [bool]$OnlineOnlyConfirmed,
        [ValidateRange(1, 10000)]
        [int]$KeepCount = 7
    )

    if (-not $OnlineOnlyConfirmed) {
        throw "Retention requires a confirmed online-only backup set."
    }

    $confirmedFullPath = [IO.Path]::GetFullPath($ConfirmedDumpPath)
    $confirmedName = [IO.Path]::GetFileName($confirmedFullPath)
    if ($confirmedName -cnotmatch '^pplid_(main|dev|hom)_([0-9]{8}_[0-9]{6})\.dump$') {
        throw 'Confirmed dump name does not match the strict PPLID backup pattern.'
    }
    $confirmedEnvironment = $Matches[1]
    $confirmedSet = Get-PplidBackupSetPaths -DumpPath $confirmedFullPath
    foreach ($path in @($confirmedSet.Dump, $confirmedSet.Manifest, $confirmedSet.Sha256)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Confirmed backup set is incomplete: $path"
        }
    }
    $confirmedCandidate = Get-PplidOneDriveRetentionCandidate -Dump (Get-Item -LiteralPath $confirmedFullPath -ErrorAction Stop) -Environment $confirmedEnvironment
    if ($null -eq $confirmedCandidate) {
        throw 'Confirmed backup set metadata is incoherent.'
    }

    $backupDirectory = Split-Path -Parent $confirmedFullPath
    $eligibleSets = @(
        foreach ($dump in @(Get-ChildItem -LiteralPath $backupDirectory -Filter '*.dump' -File -ErrorAction Stop)) {
            $candidate = Get-PplidOneDriveRetentionCandidate -Dump $dump -Environment $confirmedEnvironment
            if ($null -ne $candidate) {
                $candidate
            }
        }
    )

    $keptDumpPaths = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    [void]$keptDumpPaths.Add($confirmedFullPath)
    $remainingSlots = $KeepCount - 1
    if ($remainingSlots -gt 0) {
        $newestOtherSets = @(
            $eligibleSets |
                Where-Object { -not [string]::Equals($_.Dump, $confirmedFullPath, [StringComparison]::OrdinalIgnoreCase) } |
                Sort-Object -Property TimestampUtc -Descending |
                Select-Object -First $remainingSlots
        )
        foreach ($setToKeep in $newestOtherSets) {
            [void]$keptDumpPaths.Add($setToKeep.Dump)
        }
    }

    $removed = New-Object System.Collections.Generic.List[string]
    foreach ($oldSet in @($eligibleSets | Where-Object { -not $keptDumpPaths.Contains($_.Dump) })) {
        # Remove the dump marker first so an interrupted deletion cannot look complete.
        foreach ($path in @($oldSet.Dump, $oldSet.Manifest, $oldSet.Sha256)) {
            Remove-Item -LiteralPath $path -Force -ErrorAction Stop
            $removed.Add($path)
        }
    }
    return $removed.ToArray()
}

function Remove-PplidStaleBackupPartials {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateScript({ Test-Path -LiteralPath $_ -PathType Container })]
        [string]$StagingPath,
        [ValidateRange(1, 8760)]
        [int]$OlderThanHours = 24,
        [DateTime]$NowUtc = ([DateTime]::UtcNow)
    )

    $cutoffUtc = $NowUtc.ToUniversalTime().AddHours(-$OlderThanHours)
    $removed = New-Object System.Collections.Generic.List[string]
    $partials = Get-ChildItem -LiteralPath $StagingPath -File -Recurse -ErrorAction Stop |
        Where-Object {
            ($_.Name.EndsWith(".partial", [StringComparison]::OrdinalIgnoreCase) -or
             $_.Name.EndsWith(".uploading", [StringComparison]::OrdinalIgnoreCase)) -and
            $_.LastWriteTimeUtc -lt $cutoffUtc
        }

    foreach ($partial in @($partials)) {
        Remove-Item -LiteralPath $partial.FullName -Force -ErrorAction Stop
        $removed.Add($partial.FullName)
    }
    return $removed.ToArray()
}
