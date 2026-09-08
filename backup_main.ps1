<#
.SYNOPSIS
    Backup leve do PostgreSQL PPLID com transporte controlado.

.DESCRIPTION
    Gera um pg_dump customizado em staging local, valida com pg_restore,
    publica por rclone (padrao) ou pelo OneDrive Desktop legado. No fluxo
    rclone, preserva o conjunto local em backups\confirmed e calcula apenas
    um plano dry-run de retencao remota.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [ValidateSet("MAIN", "DEV", "HOM")]
    [string]$Environment = "MAIN",
    [ValidateRange(1, 365)]
    [int]$RetentionCount = 7,
    [ValidateSet("Rclone", "OneDriveDesktopLegacy")]
    [string]$Transport = "Rclone",
    [string]$RcloneBinaryPath = "C:\pplid\tools\rclone\rclone.exe",
    [string]$RcloneConfigPath = "C:\pplid\config\rclone.conf",
    [string]$RcloneRemote = "pplid-onedrive:",
    [string]$OneDriveRoot = "",
    [switch]$AllowLegacyDestructive,
    [switch]$ValidateOnly,
    [ValidateRange(0, 86400)]
    [int]$OnlineOnlyTimeoutSeconds = 3600,
    [ValidateRange(1, 3600)]
    [int]$OnlineOnlyPollSeconds = 10,
    [long]$MinimumFreeBytes = 2GB,
    [ValidateRange(1, 120)][int]$CapacitySampleCount = 12,
    [ValidateRange(0, 60)][int]$CapacitySampleIntervalSeconds = 5,
    [ValidateRange(1, 100)][int]$CapacityPersistentSampleCount = 3,
    [double]$MaximumCpuPercent = 80,
    [double]$CpuWarningPercent = 70,
    [double]$MinimumAvailableMemoryGB = 3.2,
    [double]$MemoryWarningGB = 4.75,
    [double]$CriticalDiskReserveGB = 10,
    [double]$DiskWarningGB = 12,
    [double]$MinimumDumpAllowanceGB = 5,
    [ValidateRange(1, 1000)][int]$MaximumPendingBeforeBlock = 1,
    [ValidateRange(1, 3600)][int]$WatchdogIntervalSeconds = 30
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\backup_postgres.ps1")
. (Join-Path $PSScriptRoot "lib\backup_onedrive.ps1")
. (Join-Path $PSScriptRoot "lib\backup_resources.ps1")
. (Join-Path $PSScriptRoot "lib\backup_rclone.ps1")

$script:BackupMutexes = New-Object System.Collections.Generic.List[object]
$script:LogFile = $null
$script:StatusFile = $null
$script:RunStartedAt = [DateTime]::UtcNow

function Write-PplidBackupLog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR")][string]$Level = "INFO"
    )

    $line = "[{0}] [{1}] [{2}] {3}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Environment, $Level, $Message
    Write-Host $line
    if ($script:LogFile) {
        Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8
    }
}

function Write-PplidBackupStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Result,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][string]$Classification,
        [string]$Message = "",
        [object]$Backup = $null,
        [string[]]$Warnings = @()
    )

    if (-not $script:StatusFile) { return }
    $payload = [ordered]@{
        environment = $Environment
        result = $Result
        exitCode = $ExitCode
        classification = $Classification
        message = $Message
        startedAt = $script:RunStartedAt.ToString("o")
        finishedAt = ([DateTime]::UtcNow).ToString("o")
        durationSeconds = [Math]::Round(([DateTime]::UtcNow - $script:RunStartedAt).TotalSeconds, 3)
        backup = $Backup
        warnings = @($Warnings)
    }
    $temporaryStatus = $script:StatusFile + ".tmp"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($temporaryStatus, ($payload | ConvertTo-Json -Depth 8), $utf8NoBom)
    Move-Item -LiteralPath $temporaryStatus -Destination $script:StatusFile -Force
}

function Enter-PplidNamedMutex {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $mutex = New-Object System.Threading.Mutex($false, $Name)
    $owned = $false
    try {
        try {
            $owned = $mutex.WaitOne(0)
        } catch [System.Threading.AbandonedMutexException] {
            $owned = $true
        }
        if (-not $owned) {
            $mutex.Dispose()
            return $false
        }
        $script:BackupMutexes.Add([PSCustomObject]@{ Name = $Name; Label = $Label; Mutex = $mutex }) | Out-Null
        return $true
    } catch {
        if (-not $owned) { $mutex.Dispose() }
        throw
    }
}

function Exit-PplidBackupMutexes {
    for ($index = $script:BackupMutexes.Count - 1; $index -ge 0; $index--) {
        $item = $script:BackupMutexes[$index]
        try { $item.Mutex.ReleaseMutex() } catch { }
        try { $item.Mutex.Dispose() } catch { }
    }
    $script:BackupMutexes.Clear()
}

function Exit-PplidDeployMutex {
    for ($index = $script:BackupMutexes.Count - 1; $index -ge 0; $index--) {
        $item = $script:BackupMutexes[$index]
        if ($item.Label -ne "deploy") { continue }
        try { $item.Mutex.ReleaseMutex() } catch { }
        try { $item.Mutex.Dispose() } catch { }
        $script:BackupMutexes.RemoveAt($index)
    }
}

function Get-PplidBackupEnvironmentPaths {
    $baseDir = Get-PplidBaseDir
    $envUpper = $Environment.ToUpperInvariant()
    return [PSCustomObject]@{
        BaseDir = $baseDir
        BackendEnv = Join-Path $baseDir ("deploy\{0}\shared\backend.env" -f $envUpper)
        Staging = Join-Path $baseDir "backups\staging"
        Confirmed = Join-Path $baseDir "backups\confirmed"
        Confirming = Join-Path $baseDir "backups\.confirming"
        PartialQuarantine = Join-Path $baseDir "backups\quarantine\partials"
        LogDir = Join-Path $baseDir "logs"
        FilePrefix = "pplid_{0}_" -f $envUpper.ToLowerInvariant()
    }
}

function Test-PplidBackupFilesEquivalent {
    param(
        [Parameter(Mandatory = $true)][string]$LeftPath,
        [Parameter(Mandatory = $true)][string]$RightPath
    )
    if (-not (Test-Path -LiteralPath $LeftPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $RightPath -PathType Leaf)) { return $false }
    $left = Get-Item -LiteralPath $LeftPath
    $right = Get-Item -LiteralPath $RightPath
    if ([long]$left.Length -ne [long]$right.Length) { return $false }
    return (Get-FileHash -LiteralPath $LeftPath -Algorithm SHA256).Hash -ceq
        (Get-FileHash -LiteralPath $RightPath -Algorithm SHA256).Hash
}

function Move-PplidAbandonedPartialsToQuarantine {
    param(
        [Parameter(Mandatory = $true)][string]$StagingDirectory,
        [Parameter(Mandatory = $true)][string]$QuarantineDirectory,
        [Parameter(Mandatory = $true)][string]$FilePrefix
    )
    if (-not (Test-Path -LiteralPath $StagingDirectory -PathType Container)) { return @() }
    $partials = @(Get-ChildItem -LiteralPath $StagingDirectory -Filter ($FilePrefix + "*.dump.partial") -File)
    if ($partials.Count -eq 0) { return @() }
    if (-not (Test-Path -LiteralPath $QuarantineDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $QuarantineDirectory -Force | Out-Null
    }
    $moved = New-Object System.Collections.Generic.List[object]
    foreach ($partial in $partials) {
        $uniqueName = "{0}.quarantine.{1}.{2}" -f $partial.Name,
            ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")), ([Guid]::NewGuid().ToString("N"))
        $destination = Join-Path $QuarantineDirectory $uniqueName
        Move-Item -LiteralPath $partial.FullName -Destination $destination -ErrorAction Stop
        $moved.Add([PSCustomObject]@{ Source = $partial.FullName; QuarantinePath = $destination }) | Out-Null
    }
    return $moved.ToArray()
}

function Write-PplidBackupSha256File {
    param(
        [Parameter(Mandatory = $true)][string]$DumpPath,
        [Parameter(Mandatory = $true)][string]$Sha256Path
    )
    $hash = (Get-FileHash -LiteralPath $DumpPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $line = "{0} *{1}`r`n" -f $hash, [IO.Path]::GetFileName($DumpPath)
    [IO.File]::WriteAllText($Sha256Path, $line, (New-Object System.Text.UTF8Encoding($false)))
    return $hash
}

function Move-PplidBackupSetToConfirmed {
    param(
        [Parameter(Mandatory = $true)][string]$DumpPath,
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][string]$Sha256Path,
        [Parameter(Mandatory = $true)][string]$ConfirmedDirectory
    )
    $baseName = [IO.Path]::GetFileNameWithoutExtension($DumpPath)
    $backupRoot = Split-Path -Parent $ConfirmedDirectory
    $confirmingRoot = Join-Path $backupRoot ".confirming"
    $transactionDirectory = Join-Path $confirmingRoot $baseName
    $finalDirectory = Join-Path $ConfirmedDirectory $baseName
    foreach ($directory in @($ConfirmedDirectory, $confirmingRoot)) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
    }

    $components = @(
        [PSCustomObject]@{ Kind = "Manifest"; Source = $ManifestPath; Name = [IO.Path]::GetFileName($ManifestPath) },
        [PSCustomObject]@{ Kind = "Sha256"; Source = $Sha256Path; Name = [IO.Path]::GetFileName($Sha256Path) },
        [PSCustomObject]@{ Kind = "Dump"; Source = $DumpPath; Name = [IO.Path]::GetFileName($DumpPath) }
    )
    if (Test-Path -LiteralPath $finalDirectory -PathType Container) {
        foreach ($component in $components) {
            $finalPath = Join-Path $finalDirectory $component.Name
            if (-not (Test-Path -LiteralPath $finalPath -PathType Leaf)) {
                throw "Diretorio confirmado local incompleto; nenhuma sobrescrita foi feita."
            }
            if ((Test-Path -LiteralPath $component.Source -PathType Leaf) -and
                -not (Test-PplidBackupFilesEquivalent -LeftPath $component.Source -RightPath $finalPath)) {
                throw "Conjunto confirmado local divergente; nenhuma sobrescrita foi feita."
            }
        }
        return [PSCustomObject]@{
            Dump = Join-Path $finalDirectory $components[2].Name
            Manifest = Join-Path $finalDirectory $components[0].Name
            Sha256 = Join-Path $finalDirectory $components[1].Name
            Directory = $finalDirectory
            Resumed = $true
        }
    }

    if (-not (Test-Path -LiteralPath $transactionDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $transactionDirectory -Force | Out-Null
    }
    foreach ($component in $components) {
        $transactionPath = Join-Path $transactionDirectory $component.Name
        $legacySplitPath = Join-Path $ConfirmedDirectory $component.Name
        if (Test-Path -LiteralPath $transactionPath -PathType Leaf) {
            if ((Test-Path -LiteralPath $component.Source -PathType Leaf) -and
                -not (Test-PplidBackupFilesEquivalent -LeftPath $component.Source -RightPath $transactionPath)) {
                throw "Componente transacional divergente; nenhuma sobrescrita foi feita."
            }
            continue
        }
        $candidate = if (Test-Path -LiteralPath $component.Source -PathType Leaf) {
            $component.Source
        } elseif (Test-Path -LiteralPath $legacySplitPath -PathType Leaf) {
            $legacySplitPath
        } else {
            $null
        }
        if (-not $candidate) {
            throw "Conjunto local dividido ou incompleto; dados preservados para retomada."
        }
        Move-Item -LiteralPath $candidate -Destination $transactionPath -ErrorAction Stop
    }

    Move-Item -LiteralPath $transactionDirectory -Destination $finalDirectory -ErrorAction Stop
    return [PSCustomObject]@{
        Dump = Join-Path $finalDirectory $components[2].Name
        Manifest = Join-Path $finalDirectory $components[0].Name
        Sha256 = Join-Path $finalDirectory $components[1].Name
        Directory = $finalDirectory
        Resumed = $false
    }
}

function Resume-PplidLocalConfirmations {
    param(
        [Parameter(Mandatory = $true)][string]$ConfirmingDirectory,
        [Parameter(Mandatory = $true)][string]$ConfirmedDirectory
    )
    if (-not (Test-Path -LiteralPath $ConfirmingDirectory -PathType Container)) { return @() }
    if (-not (Test-Path -LiteralPath $ConfirmedDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $ConfirmedDirectory -Force | Out-Null
    }
    $resumed = New-Object System.Collections.Generic.List[object]
    foreach ($transaction in @(Get-ChildItem -LiteralPath $ConfirmingDirectory -Directory)) {
        $baseName = $transaction.Name
        # PowerShell 5.1 can bind comma-separated Join-Path expressions as one
        # invocation. Build each path independently before creating the array.
        $transactionManifest = Join-Path $transaction.FullName ($baseName + ".manifest.json")
        $transactionSha256 = Join-Path $transaction.FullName ($baseName + ".sha256")
        $transactionDump = Join-Path $transaction.FullName ($baseName + ".dump")
        $expected = @($transactionManifest, $transactionSha256, $transactionDump)
        if (@($expected | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }).Count -gt 0) {
            continue
        }
        $destination = Join-Path $ConfirmedDirectory $baseName
        if (Test-Path -LiteralPath $destination -PathType Container) {
            $destinationManifest = Join-Path $destination ($baseName + ".manifest.json")
            $destinationSha256 = Join-Path $destination ($baseName + ".sha256")
            $destinationDump = Join-Path $destination ($baseName + ".dump")
            $destinationExpected = @($destinationManifest, $destinationSha256, $destinationDump)
            if (@($destinationExpected | Where-Object {
                        -not (Test-Path -LiteralPath $_ -PathType Leaf)
                    }).Count -gt 0) {
                throw "Confirmacao local existente esta incompleta; dados preservados."
            }
            for ($index = 0; $index -lt $expected.Count; $index++) {
                if (-not (Test-PplidBackupFilesEquivalent -LeftPath $expected[$index] `
                        -RightPath $destinationExpected[$index])) {
                    throw "Confirmacao local transacional diverge do destino existente; dados preservados."
                }
            }
            $resumed.Add([PSCustomObject]@{
                BaseName = $baseName
                Directory = $destination
                AlreadyConfirmed = $true
                ConfirmingDirectoryPreserved = $true
            }) | Out-Null
            continue
        }
        Move-Item -LiteralPath $transaction.FullName -Destination $destination -ErrorAction Stop
        $resumed.Add([PSCustomObject]@{
            BaseName = $baseName
            Directory = $destination
            AlreadyConfirmed = $false
            ConfirmingDirectoryPreserved = $false
        }) | Out-Null
    }
    return $resumed.ToArray()
}

function Get-PplidTransportExitCode {
    param([string]$Classification)
    switch ($Classification) {
        "auth_required" { return 21 }
        "remote_quota_low" { return 22 }
        "configuration_error" { return 40 }
        default { return 20 }
    }
}

function Get-PplidBackupClassification {
    param([Parameter(Mandatory = $true)][int]$ExitCode)
    switch ($ExitCode) {
        0 { return "success" }
        10 { return "deferred_deploy" }
        11 { return "deferred_high_cpu" }
        12 { return "deferred_low_memory" }
        13 { return "deferred_low_disk" }
        20 { return "upload_pending" }
        21 { return "auth_required" }
        22 { return "remote_quota_low" }
        30 { return "dump_failed" }
        31 { return "validation_failed" }
        40 { return "configuration_error" }
        41 { return "partial_quarantine" }
        default { return "failed" }
    }
}

function Get-PplidBackupResultName {
    param([Parameter(Mandatory = $true)][string]$Classification)
    if ($Classification -eq "success") { return "success" }
    if ($Classification -like "deferred_*") { return "deferred" }
    if ($Classification -in @("upload_pending", "auth_required", "remote_quota_low")) {
        return "upload_pending"
    }
    if ($Classification -eq "partial_quarantine") { return "quarantined" }
    return "failed"
}

function Get-PplidMinimumRcloneFreeBytes {
    param([Parameter(Mandatory = $true)][long]$EstimatedDumpBytes)
    return [long][Math]::Max(5GB, ([double]$EstimatedDumpBytes * 2.0))
}

function Complete-PplidRcloneBackup {
    param(
        [Parameter(Mandatory = $true)][string]$LocalDumpPath,
        [Parameter(Mandatory = $true)][string]$LocalManifestPath,
        [Parameter(Mandatory = $true)][string]$LocalSha256Path,
        [Parameter(Mandatory = $true)]$Configuration,
        [Parameter(Mandatory = $true)][long]$EstimatedDumpBytes,
        [Parameter(Mandatory = $true)][string]$ConfirmedDirectory
    )
    $minimumRemoteFreeBytes = Get-PplidMinimumRcloneFreeBytes -EstimatedDumpBytes $EstimatedDumpBytes
    $quota = Get-PplidRcloneQuota -Configuration $Configuration
    if (-not $quota.Success) {
        return [PSCustomObject]@{
            Success = $false; Classification = $quota.Classification
            Message = "Quota remota indisponivel; staging preservado."
        }
    }
    if ([long]$quota.FreeBytes -lt $minimumRemoteFreeBytes) {
        return [PSCustomObject]@{
            Success = $false; Classification = "remote_quota_low"
            Message = "Quota remota abaixo do minimo seguro; staging preservado."
        }
    }

    $published = Publish-PplidBackupWithRclone -DumpPath $LocalDumpPath `
        -ManifestPath $LocalManifestPath -Sha256Path $LocalSha256Path `
        -Environment $Environment -Configuration $Configuration
    if (-not $published.Success) {
        return [PSCustomObject]@{
            Success = $false; Classification = $published.Classification
            Message = "Publicacao rclone pendente; staging preservado."
        }
    }

    $inventory = Get-PplidRcloneDirectoryInventory -Configuration $Configuration `
        -RemotePath $published.FinalRoot
    if (-not $inventory.Success) {
        return [PSCustomObject]@{
            Success = $false; Classification = "upload_pending"
            Message = "Publicacao concluida, mas inventario final nao foi confirmado; staging preservado."
        }
    }
    $retentionPlan = Get-PplidRcloneRetentionPlan -Environment $Environment `
        -Inventory $inventory.Items -RetentionCount $RetentionCount `
        -ProtectedDumpName ([IO.Path]::GetFileName($LocalDumpPath))
    if (-not $retentionPlan.DryRun -or @($retentionPlan.MutationCommands).Count -ne 0) {
        throw "Contrato de retencao rclone deixou de ser dry-run; copia local preservada."
    }
    $confirmed = Move-PplidBackupSetToConfirmed -DumpPath $LocalDumpPath `
        -ManifestPath $LocalManifestPath -Sha256Path $LocalSha256Path `
        -ConfirmedDirectory $ConfirmedDirectory
    return [PSCustomObject]@{
        Success = $true
        Classification = "success"
        Published = $published
        Confirmed = $confirmed
        Quota = $quota
        MinimumRemoteFreeBytes = $minimumRemoteFreeBytes
        RetentionPlan = $retentionPlan
        AlreadyPublished = [bool]$published.AlreadyPublished
        OrphanIncoming = @($published.OrphanIncoming)
    }
}

function Get-PplidCloudSetForLocalDump {
    param(
        [Parameter(Mandatory = $true)][string]$LocalDumpPath,
        [Parameter(Mandatory = $true)][string]$CloudDirectory
    )
    return Get-PplidBackupSetPaths -DumpPath (Join-Path $CloudDirectory ([IO.Path]::GetFileName($LocalDumpPath)))
}

function Write-PplidBackupManifest {
    param(
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][object]$Metadata,
        [Parameter(Mandatory = $true)][object]$Validation,
        [Parameter(Mandatory = $true)][object]$Tools,
        [object]$DumpResult = $null,
        [switch]$RecoveredFromPending
    )

    $generatedAt = [DateTime]$Metadata.GeneratedAtUtc
    $dumpStartedAt = $generatedAt
    $dumpFinishedAt = $generatedAt
    $dumpDurationSeconds = $null
    if ($DumpResult) {
        $dumpStartedAt = [DateTime]$DumpResult.StartedAtUtc
        $dumpFinishedAt = [DateTime]$DumpResult.FinishedAtUtc
        $dumpDurationSeconds = [Math]::Round(($dumpFinishedAt - $dumpStartedAt).TotalSeconds, 3)
    }

    $manifest = [ordered]@{
        schemaVersion = 1
        environment = $Environment
        database = $Metadata.Database
        fileName = $Metadata.FileName
        sizeBytes = $Metadata.SizeBytes
        sha256 = $Metadata.Sha256
        generatedAtUtc = $generatedAt.ToString("o")
        dumpStartedAtUtc = $dumpStartedAt.ToString("o")
        dumpFinishedAtUtc = $dumpFinishedAt.ToString("o")
        dumpDurationSeconds = $dumpDurationSeconds
        pgDumpVersion = $Tools.PgDumpVersion
        pgRestoreVersion = $Tools.PgRestoreVersion
        restoreListEntries = $Validation.EntryCount
        compressionLevel = 1
        format = "custom"
        recoveredFromPending = [bool]$RecoveredFromPending
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($ManifestPath, ($manifest | ConvertTo-Json -Depth 6), $utf8NoBom)
}

function Resolve-PplidPendingDumpManifest {
    param(
        [Parameter(Mandatory = $true)][string]$DumpPath,
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][string]$PgRestorePath,
        [Parameter(Mandatory = $true)][string]$DatabaseName,
        [Parameter(Mandatory = $true)][object]$Tools
    )

    $metadata = Get-PplidPostgresBackupMetadata -DumpPath $DumpPath -DatabaseName $DatabaseName
    $manifestCoherent = $false
    if (Test-Path -LiteralPath $ManifestPath -PathType Leaf) {
        try {
            $rawManifest = [IO.File]::ReadAllText($ManifestPath)
            if (-not [String]::IsNullOrWhiteSpace($rawManifest)) {
                $existingManifest = $rawManifest | ConvertFrom-Json -ErrorAction Stop
                $requiredProperties = @('fileName', 'environment', 'database', 'sizeBytes', 'sha256')
                $missingProperties = @($requiredProperties | Where-Object {
                    $null -eq $existingManifest.PSObject.Properties[$_]
                })
                $manifestSize = [long]0
                $sizeValid = [long]::TryParse([string]$existingManifest.sizeBytes, [ref]$manifestSize)
                $manifestCoherent = (
                    $missingProperties.Count -eq 0 -and
                    $sizeValid -and
                    [StringComparer]::Ordinal.Equals([string]$existingManifest.fileName, [string]$metadata.FileName) -and
                    [StringComparer]::OrdinalIgnoreCase.Equals([string]$existingManifest.environment, [string]$Environment) -and
                    [StringComparer]::Ordinal.Equals([string]$existingManifest.database, [string]$metadata.Database) -and
                    $manifestSize -eq [long]$metadata.SizeBytes -and
                    [StringComparer]::OrdinalIgnoreCase.Equals([string]$existingManifest.sha256, [string]$metadata.Sha256)
                )
            }
        } catch {
            $manifestCoherent = $false
        }
    }

    if ($manifestCoherent) {
        return [PSCustomObject]@{ Ready = $true; Rebuilt = $false; QuarantinePath = $null }
    }

    $validation = Test-PplidPostgresDump -PgRestorePath $PgRestorePath -DumpPath $DumpPath
    if ($validation.Valid) {
        Write-PplidBackupManifest -ManifestPath $ManifestPath -Metadata $metadata `
            -Validation $validation -Tools $Tools -RecoveredFromPending
        return [PSCustomObject]@{ Ready = $true; Rebuilt = $true; QuarantinePath = $null }
    }

    $quarantinePath = "{0}.quarantine.{1}.{2}" -f $DumpPath, `
        ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")), ([Guid]::NewGuid().ToString("N"))
    Move-Item -LiteralPath $DumpPath -Destination $quarantinePath -ErrorAction Stop
    return [PSCustomObject]@{ Ready = $false; Rebuilt = $false; QuarantinePath = $quarantinePath }
}

function Complete-PplidPendingCloudBackup {
    param(
        [Parameter(Mandatory = $true)][string]$LocalDumpPath,
        [Parameter(Mandatory = $true)][string]$LocalManifestPath,
        [Parameter(Mandatory = $true)][string]$CloudDirectory,
        [switch]$ReplaceCompleteSet
    )

    Repair-PplidInterruptedOneDrivePublish -LocalDumpPath $LocalDumpPath -CloudDirectory $CloudDirectory | Out-Null
    $cloudSet = Get-PplidCloudSetForLocalDump -LocalDumpPath $LocalDumpPath -CloudDirectory $CloudDirectory
    $allCloudPaths = @($cloudSet.Dump, $cloudSet.Manifest, $cloudSet.Sha256)
    $existingCount = @($allCloudPaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }).Count

    if ($ReplaceCompleteSet -and $existingCount -eq 3) {
        throw "Modo OneDrive Desktop legado recusou substituir conjunto completo; staging preservado para intervencao manual."
    }

    if ($existingCount -eq 0) {
        Write-PplidBackupLog "Publicando $([IO.Path]::GetFileName($LocalDumpPath)) no OneDrive."
        $published = Publish-PplidBackupToOneDrive -SourceDumpPath $LocalDumpPath `
            -SourceManifestPath $LocalManifestPath -Environment $Environment -OneDriveRoot $OneDriveRoot
        $cloudSet = [PSCustomObject]@{
            Dump = $published.DumpPath
            Manifest = $published.ManifestPath
            Sha256 = $published.Sha256Path
        }
        $allCloudPaths = @($cloudSet.Dump, $cloudSet.Manifest, $cloudSet.Sha256)
    } elseif ($existingCount -ne 3) {
        throw "Conjunto incompleto ja existe no OneDrive para $([IO.Path]::GetFileName($LocalDumpPath))."
    } else {
        $localHash = (Get-FileHash -LiteralPath $LocalDumpPath -Algorithm SHA256).Hash
        $cloudHash = (Get-FileHash -LiteralPath $cloudSet.Dump -Algorithm SHA256).Hash
        if ($localHash -cne $cloudHash) {
            throw "Dump existente no OneDrive possui checksum diferente do staging."
        }
        Write-PplidBackupLog "Retomando confirmacao online-only de backup pendente."
    }

    Request-PplidOneDriveOnlineOnly -Path $allCloudPaths
    $onlineOnly = Wait-PplidOneDriveOnlineOnly -Path $allCloudPaths `
        -TimeoutSeconds $OnlineOnlyTimeoutSeconds -PollIntervalSeconds $OnlineOnlyPollSeconds
    if (-not $onlineOnly) {
        throw "OneDrive nao confirmou online-only dentro do prazo; staging preservado."
    }

    $removedByRetention = @(Invoke-PplidOneDriveRetention -ConfirmedDumpPath $cloudSet.Dump `
        -OnlineOnlyConfirmed $true -KeepCount $RetentionCount)
    $missingConfirmedPaths = @($allCloudPaths | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
    if ($missingConfirmedPaths.Count -gt 0) {
        throw "Retencao removeu ou tornou indisponivel o conjunto recem-confirmado; staging preservado."
    }
    Remove-Item -LiteralPath $LocalDumpPath, $LocalManifestPath -Force -ErrorAction Stop

    return [PSCustomObject]@{
        DumpPath = $cloudSet.Dump
        ManifestPath = $cloudSet.Manifest
        Sha256Path = $cloudSet.Sha256
        OnlineOnly = $true
        RetentionFilesRemoved = $removedByRetention.Count
    }
}

$paths = Get-PplidBackupEnvironmentPaths

if ($ValidateOnly -or $WhatIfPreference) {
    $tools = Resolve-PplidPostgres14Tools
    $settings = Get-PplidPostgresEnvSettings -BackendEnvPath $paths.BackendEnv
    if ($Transport -eq "Rclone") {
        $rclonePreflight = Test-PplidRcloneStaticPreflight -BinaryPath $RcloneBinaryPath `
            -ConfigPath $RcloneConfigPath -Remote $RcloneRemote
        if (-not $rclonePreflight.Passed) {
            Write-Error $rclonePreflight.Message
            exit 40
        }
        $transportTarget = $rclonePreflight.Configuration.Remote
    } elseif ($Transport -eq "OneDriveDesktopLegacy") {
        if (-not $AllowLegacyDestructive) {
            Write-Error "OneDriveDesktopLegacy bloqueado: informe -AllowLegacyDestructive explicitamente."
            exit 40
        }
        $transportTarget = Resolve-PplidOneDriveRoot -OneDriveRoot $OneDriveRoot
    }
    Write-Host ("Validacao estatica OK: environment={0}; database={1}; pg_dump={2}; transport={3}; target={4}" -f `
        $Environment, $settings.Database, $tools.PgDumpPath, $Transport, $transportTarget)
    if ($WhatIfPreference) {
        Write-Host "WhatIf: nenhum diretorio, dump, upload, retencao ou tarefa foi alterado."
    }
    exit 0
}

$exitCode = 0
$classification = "success"
$finalStatus = $null
try {
    New-Item -ItemType Directory -Path $paths.Staging, $paths.LogDir, $paths.Confirmed -Force | Out-Null
    $script:LogFile = Join-Path $paths.LogDir ("backup-{0}.log" -f $Environment.ToLowerInvariant())
    $script:StatusFile = Join-Path $paths.LogDir ("backup-{0}-status.json" -f $Environment.ToLowerInvariant())

    if (-not (Enter-PplidNamedMutex -Name ("Global\PPLID-{0}-Backup" -f $Environment) -Label "backup")) {
        throw "Outra execucao de backup ja esta ativa."
    }

    $tools = Resolve-PplidPostgres14Tools
    $settings = Get-PplidPostgresEnvSettings -BackendEnvPath $paths.BackendEnv
    $rcloneConfiguration = $null
    $cloudDirectory = $null
    if ($Transport -eq "Rclone") {
        $rclonePreflight = Test-PplidRcloneStaticPreflight -BinaryPath $RcloneBinaryPath `
            -ConfigPath $RcloneConfigPath -Remote $RcloneRemote
        if (-not $rclonePreflight.Passed) {
            $exitCode = 40
            throw $rclonePreflight.Message
        }
        $rcloneConfiguration = $rclonePreflight.Configuration
    } elseif ($Transport -eq "OneDriveDesktopLegacy") {
        if (-not $AllowLegacyDestructive) {
            $exitCode = 40
            throw "OneDriveDesktopLegacy bloqueado: informe -AllowLegacyDestructive explicitamente."
        }
        $OneDriveRoot = Resolve-PplidOneDriveRoot -OneDriveRoot $OneDriveRoot
        $cloudDirectory = Get-PplidOneDriveBackupDirectory -Environment $Environment `
            -OneDriveRoot $OneDriveRoot -Create
    }

    Write-PplidBackupLog "Inicio do backup; database=$($settings.Database); transport=$Transport; retentionDryRun=$RetentionCount."
    $resumedConfirmations = @(Resume-PplidLocalConfirmations -ConfirmingDirectory $paths.Confirming -ConfirmedDirectory $paths.Confirmed)
    if ($resumedConfirmations.Count -gt 0) {
        Write-PplidBackupLog ("Confirmacoes locais retomadas: {0}." -f $resumedConfirmations.Count) "WARN"
    }
    $quarantinedPartials = @(Move-PplidAbandonedPartialsToQuarantine -StagingDirectory $paths.Staging -QuarantineDirectory $paths.PartialQuarantine -FilePrefix $paths.FilePrefix)
    if ($quarantinedPartials.Count -gt 0) {
        $exitCode = 41
        $classification = Get-PplidBackupClassification -ExitCode $exitCode
        $warning = "Partial abandonado preservado em quarentena; nenhum novo dump foi criado."
        Write-PplidBackupLog $warning "WARN"
        $finalStatus = [ordered]@{
            partialQuarantine = @($quarantinedPartials)
            resumedConfirmations = @($resumedConfirmations)
        }
        Write-PplidBackupStatus -Result (Get-PplidBackupResultName -Classification $classification) -ExitCode $exitCode -Classification $classification -Message $warning -Backup $finalStatus -Warnings @("partial_quarantine")
        exit $exitCode
    }
    $historyFiles = @()
    foreach ($historyPath in @($paths.Confirmed, $paths.Staging)) {
        if (Test-Path -LiteralPath $historyPath -PathType Container) {
            $historyFiles += @(Get-ChildItem -LiteralPath $historyPath -Filter ($paths.FilePrefix + "*.dump") -File -Recurse)
        }
    }
    $dumpEstimate = Get-PplidBackupDumpEstimate -DumpSizesBytes @(
        $historyFiles | Sort-Object LastWriteTimeUtc | ForEach-Object { [long]$_.Length }
    )
    $pendingDumps = @(Get-ChildItem -LiteralPath $paths.Staging -Filter ($paths.FilePrefix + "*.dump") -File |
        Sort-Object LastWriteTimeUtc)
    $invalidPendingQuarantines = New-Object System.Collections.Generic.List[string]
    foreach ($pendingDump in $pendingDumps) {
        $pendingManifest = Join-Path $paths.Staging ($pendingDump.BaseName + ".manifest.json")
        $pendingResolution = Resolve-PplidPendingDumpManifest -DumpPath $pendingDump.FullName `
            -ManifestPath $pendingManifest -PgRestorePath $tools.PgRestorePath `
            -DatabaseName $settings.Database -Tools $tools
        if (-not $pendingResolution.Ready) {
            Write-PplidBackupLog "Dump pendente invalido preservado em quarentena: $($pendingResolution.QuarantinePath)." "WARN"
            $invalidPendingQuarantines.Add($pendingResolution.QuarantinePath) | Out-Null
            continue
        }
        if ($pendingResolution.Rebuilt) {
            Write-PplidBackupLog "Manifesto reconstruido para dump pendente: $($pendingDump.FullName)." "WARN"
        }
        if ($Transport -eq "Rclone") {
            $pendingSha = Join-Path $paths.Staging ($pendingDump.BaseName + ".sha256")
            Write-PplidBackupSha256File -DumpPath $pendingDump.FullName -Sha256Path $pendingSha | Out-Null
            $transportResult = Complete-PplidRcloneBackup -LocalDumpPath $pendingDump.FullName `
                -LocalManifestPath $pendingManifest -LocalSha256Path $pendingSha `
                -Configuration $rcloneConfiguration -EstimatedDumpBytes $dumpEstimate.EstimateBytes `
                -ConfirmedDirectory $paths.Confirmed
            if (-not $transportResult.Success) {
                $exitCode = Get-PplidTransportExitCode -Classification $transportResult.Classification
                throw $transportResult.Message
            }
            $finalStatus = $transportResult
        } elseif ($Transport -eq "OneDriveDesktopLegacy") {
            $finalStatus = Complete-PplidPendingCloudBackup -LocalDumpPath $pendingDump.FullName `
                -LocalManifestPath $pendingManifest -CloudDirectory $cloudDirectory `
                -ReplaceCompleteSet:$pendingResolution.Rebuilt
        }
        Write-PplidBackupLog "Backup pendente transportado; nenhum novo dump sera criado nesta execucao."
    }
    if ($pendingDumps.Count -gt 0) {
        if ($invalidPendingQuarantines.Count -gt 0) {
            $exitCode = 31
            $classification = Get-PplidBackupClassification -ExitCode $exitCode
            $finalStatus = [ordered]@{
                invalidDumpQuarantine = @($invalidPendingQuarantines)
                transport = $finalStatus
            }
            Write-PplidBackupStatus -Result (Get-PplidBackupResultName -Classification $classification) -ExitCode $exitCode -Classification $classification -Message "Dump pendente invalido preservado; nenhum novo dump foi criado." -Backup $finalStatus -Warnings @("validation_quarantine")
            exit $exitCode
        }
        $classification = "success"
        Write-PplidBackupStatus -Result "success" -ExitCode 0 -Classification $classification -Message "Pendencias processadas; novo dump deliberadamente omitido." -Backup $finalStatus
        exit 0
    }

    $rcloneQuotaPreflight = $null
    if ($Transport -eq "Rclone") {
        $rcloneQuotaPreflight = Get-PplidRcloneQuota -Configuration $rcloneConfiguration
        if (-not $rcloneQuotaPreflight.Success) {
            $exitCode = Get-PplidTransportExitCode -Classification $rcloneQuotaPreflight.Classification
            throw "Preflight de quota rclone falhou antes do pg_dump."
        }
        $minimumRemoteFreeBytes = Get-PplidMinimumRcloneFreeBytes -EstimatedDumpBytes $dumpEstimate.EstimateBytes
        if ([long]$rcloneQuotaPreflight.FreeBytes -lt $minimumRemoteFreeBytes) {
            $exitCode = 22
            throw "Preflight de quota rclone abaixo do minimo seguro antes do pg_dump."
        }
    }

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $baseName = $paths.FilePrefix + $timestamp
    $partialPath = Join-Path $paths.Staging ($baseName + ".dump.partial")
    $localDumpPath = Join-Path $paths.Staging ($baseName + ".dump")
    $localManifestPath = Join-Path $paths.Staging ($baseName + ".manifest.json")
    $localSha256Path = Join-Path $paths.Staging ($baseName + ".sha256")

    $preflight = Test-PplidPostgresBackupPreflight -BackendEnvPath $paths.BackendEnv `
        -DestinationPath $partialPath -MinimumFreeBytes $MinimumFreeBytes `
        -PgDumpPath $tools.PgDumpPath -PgRestorePath $tools.PgRestorePath -ThrowOnFailure

    $capacity = Test-PplidBackupCapacity -SampleCount $CapacitySampleCount `
        -SampleIntervalSeconds $CapacitySampleIntervalSeconds `
        -PersistentSampleCount $CapacityPersistentSampleCount `
        -MaximumCpuPercent $MaximumCpuPercent -CpuWarningPercent $CpuWarningPercent `
        -MinimumAvailableMemoryGB $MinimumAvailableMemoryGB -MemoryWarningGB $MemoryWarningGB `
        -CriticalDiskReserveGB $CriticalDiskReserveGB -DiskWarningGB $DiskWarningGB `
        -MinimumDumpAllowanceGB $MinimumDumpAllowanceGB `
        -DumpHistoryBytes @($historyFiles | Sort-Object LastWriteTimeUtc | ForEach-Object { [long]$_.Length }) `
        -PendingDumpCount (Get-PplidBackupPendingDumpCount -StagingPath $paths.Staging) `
        -MaximumPendingBeforeBlock $MaximumPendingBeforeBlock -StagingPath $paths.Staging
    $finalStatus = [ordered]@{ capacity = $capacity.Metrics; capacityWarnings = @($capacity.Warnings) }
    if (-not $capacity.Passed) {
        $exitCode = [int]$capacity.ExitCode
        throw "Preflight de capacidade adiou o backup: $($capacity.Reason)."
    }

    $watchdogSamples = New-Object System.Collections.Generic.List[object]
    $resourceObserver = {
        param($ProcessObservation)
        $sample = Get-PplidBackupResourceSample -StagingPath $paths.Staging
        $watchdogSamples.Add($sample) | Out-Null
        Write-PplidBackupLog ("Watchdog observacional: cpu={0}%; ram={1}GB; disco={2}GB." -f `
            $sample.CpuPercent, $sample.AvailableMemoryGB, $sample.FreeDiskGB)
    }

    if (-not (Enter-PplidNamedMutex -Name ("Global\PPLID-Deploy-{0}" -f $Environment) -Label "deploy")) {
        $exitCode = 10
        throw "Deploy de $Environment em andamento; backup adiado."
    }
    try {
        $dumpResult = Invoke-PplidPostgresDump -BackendEnvPath $paths.BackendEnv `
            -PgDumpPath $tools.PgDumpPath -DestinationPath $partialPath `
            -Observer $resourceObserver -ObserverIntervalSeconds $WatchdogIntervalSeconds
    } finally {
        # O guard de deploy cobre somente o snapshot do pg_dump.
        Exit-PplidDeployMutex
    }
    if (-not $dumpResult.Succeeded) {
        $exitCode = 30
        throw "pg_dump falhou (exit=$($dumpResult.ExitCode)): $($dumpResult.StandardErr)"
    }
    $watchdogReport = if ($watchdogSamples.Count -gt 0) {
        New-PplidBackupWatchdogReport -Samples $watchdogSamples.ToArray()
    } else {
        [PSCustomObject]@{ ObservationalOnly = $true; SampleCount = 0; Alerts = @() }
    }
    Write-PplidBackupLog ("Watchdog concluido: amostras={0}; alertas={1}." -f `
        $watchdogReport.SampleCount, (@($watchdogReport.Alerts) -join ","))

    $postDumpCapacity = Get-PplidBackupResourceSample -StagingPath $paths.Staging
    if ([double]$postDumpCapacity.FreeDiskGB -lt [double]$CriticalDiskReserveGB) {
        $preservedPartials = @(Move-PplidAbandonedPartialsToQuarantine -StagingDirectory $paths.Staging -QuarantineDirectory $paths.PartialQuarantine -FilePrefix $paths.FilePrefix)
        $exitCode = 13
        $finalStatus = [ordered]@{
            capacity = $capacity.Metrics
            postDumpCapacity = $postDumpCapacity
            watchdog = $watchdogReport
            partialQuarantine = @($preservedPartials)
        }
        throw "Disco abaixo da reserva critica apos pg_dump; arquivo preservado e upload bloqueado."
    }

    $validation = Test-PplidPostgresDump -PgRestorePath $tools.PgRestorePath -DumpPath $partialPath
    if (-not $validation.Valid) {
        $exitCode = 31
        throw "pg_restore --list rejeitou o dump (exit=$($validation.ExitCode)): $($validation.StandardErr)"
    }

    Move-Item -LiteralPath $partialPath -Destination $localDumpPath -ErrorAction Stop
    $metadata = Get-PplidPostgresBackupMetadata -DumpPath $localDumpPath -DatabaseName $settings.Database
    if ($metadata.SizeBytes -lt 1MB) {
        throw "Dump concluido ficou abaixo do minimo de seguranca de 1 MiB."
    }

    Write-PplidBackupManifest -ManifestPath $localManifestPath -Metadata $metadata `
        -Validation $validation -Tools $tools -DumpResult $dumpResult
    if ($Transport -eq "Rclone") {
        $writtenHash = Write-PplidBackupSha256File -DumpPath $localDumpPath -Sha256Path $localSha256Path
        if ($writtenHash -ine $metadata.Sha256) {
            $exitCode = 31
            throw "Checksum local divergiu do manifesto; conjunto preservado no staging."
        }
        $transportResult = Complete-PplidRcloneBackup -LocalDumpPath $localDumpPath `
            -LocalManifestPath $localManifestPath -LocalSha256Path $localSha256Path `
            -Configuration $rcloneConfiguration -EstimatedDumpBytes $dumpEstimate.EstimateBytes `
            -ConfirmedDirectory $paths.Confirmed
        if (-not $transportResult.Success) {
            $exitCode = Get-PplidTransportExitCode -Classification $transportResult.Classification
            throw $transportResult.Message
        }
        $finalStatus = [ordered]@{
            fileName = [IO.Path]::GetFileName($transportResult.Confirmed.Dump)
            remotePath = $transportResult.Published.DumpRemotePath
            confirmedLocalPath = $transportResult.Confirmed.Dump
            sizeBytes = $metadata.SizeBytes
            sha256 = $metadata.Sha256
            restoreListEntries = $validation.EntryCount
            capacity = $capacity.Metrics
            watchdog = $watchdogReport
            retentionPlan = $transportResult.RetentionPlan
            retentionDryRun = [bool]$transportResult.RetentionPlan.DryRun
            wouldRetire = @($transportResult.RetentionPlan.WouldRetire)
            alreadyPublished = [bool]$transportResult.AlreadyPublished
            orphanIncoming = @($transportResult.OrphanIncoming)
        }
    } elseif ($Transport -eq "OneDriveDesktopLegacy") {
        $cloudResult = Complete-PplidPendingCloudBackup -LocalDumpPath $localDumpPath `
            -LocalManifestPath $localManifestPath -CloudDirectory $cloudDirectory
        $finalStatus = [ordered]@{
            fileName = [IO.Path]::GetFileName($cloudResult.DumpPath)
            cloudPath = $cloudResult.DumpPath
            sizeBytes = $metadata.SizeBytes
            sha256 = $metadata.Sha256
            onlineOnly = $cloudResult.OnlineOnly
            retentionFilesRemoved = $cloudResult.RetentionFilesRemoved
            restoreListEntries = $validation.EntryCount
            capacity = $capacity.Metrics
            watchdog = $watchdogReport
        }
    }
    Write-PplidBackupLog "Backup concluido com transporte $Transport."
    $classification = "success"
    Write-PplidBackupStatus -Result "success" -ExitCode 0 -Classification $classification -Message "Backup concluido." -Backup $finalStatus
} catch {
    if ($exitCode -eq 0) { $exitCode = 1 }
    $classification = Get-PplidBackupClassification -ExitCode $exitCode
    $message = $_.Exception.Message
    Write-PplidBackupLog $message "ERROR"
    $resultName = Get-PplidBackupResultName -Classification $classification
    Write-PplidBackupStatus -Result $resultName -ExitCode $exitCode -Classification $classification -Message $message -Backup $finalStatus
} finally {
    Exit-PplidBackupMutexes
}

exit $exitCode
