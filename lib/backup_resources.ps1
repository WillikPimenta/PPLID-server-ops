Set-StrictMode -Version 2.0

$script:PplidGiB = [double]1073741824

function Get-PplidBackupResourceSample {
    [CmdletBinding()]
    param(
        [Parameter()]
        [scriptblock]$SampleProvider,

        [Parameter()]
        [string]$StagingPath = 'C:\pplid\backups\staging'
    )

    if ($SampleProvider) {
        $sample = & $SampleProvider
        if ($null -eq $sample) {
            throw 'O provedor de amostra não retornou dados.'
        }
        return $sample
    }

    $processors = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop)
    $operatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $fullStagingPath = [System.IO.Path]::GetFullPath($StagingPath)
    $driveRoot = [System.IO.Path]::GetPathRoot($fullStagingPath).TrimEnd('\')
    $logicalDisk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter ("DeviceID='{0}'" -f $driveRoot) -ErrorAction Stop

    if ($processors.Count -eq 0 -or $null -eq $logicalDisk) {
        throw 'Não foi possível coletar CPU ou disco via CIM.'
    }

    $cpuValues = @($processors | ForEach-Object { [double]$_.LoadPercentage })
    [pscustomobject]@{
        TimestampUtc      = [DateTime]::UtcNow
        CpuPercent       = [Math]::Round((($cpuValues | Measure-Object -Average).Average), 2)
        AvailableMemoryGB = [Math]::Round(([double]$operatingSystem.FreePhysicalMemory * 1KB / $script:PplidGiB), 3)
        FreeDiskGB        = [Math]::Round(([double]$logicalDisk.FreeSpace / $script:PplidGiB), 3)
    }
}

function Get-PplidBackupDumpEstimate {
    [CmdletBinding()]
    param(
        [Parameter()]
        [long[]]$DumpSizesBytes = @(),

        [Parameter()]
        [ValidateRange(1, 100)]
        [int]$HistoryCount = 3
    )

    $validSizes = @($DumpSizesBytes | Where-Object { $_ -gt 0 } | Select-Object -Last $HistoryCount)
    if ($validSizes.Count -eq 0) {
        return [pscustomobject]@{
            EstimateBytes = [long]$script:PplidGiB
            EstimateGB    = 1.0
            HistoryCount  = 0
            LargestGB     = 0.0
            AverageGB     = 0.0
        }
    }

    $measure = $validSizes | Measure-Object -Maximum -Average
    $estimateBytes = [Math]::Max(
        $script:PplidGiB,
        [Math]::Max(([double]$measure.Maximum * 1.5), ([double]$measure.Average * 2.0))
    )

    [pscustomobject]@{
        EstimateBytes = [long][Math]::Ceiling($estimateBytes)
        EstimateGB    = [Math]::Round($estimateBytes / $script:PplidGiB, 3)
        HistoryCount  = $validSizes.Count
        LargestGB     = [Math]::Round([double]$measure.Maximum / $script:PplidGiB, 3)
        AverageGB     = [Math]::Round([double]$measure.Average / $script:PplidGiB, 3)
    }
}

function Get-PplidBackupPendingDumpCount {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$StagingPath
    )

    if (-not (Test-Path -LiteralPath $StagingPath -PathType Container)) {
        return 0
    }

    # Todo dump finalizado no staging representa trabalho pendente, inclusive
    # quando seus sidecars ainda precisam ser reconstru�dos ap�s interrup��o.
    return @(Get-ChildItem -LiteralPath $StagingPath -File -Filter 'pplid_*.dump' -ErrorAction Stop).Count
}

function Test-PplidPersistentCondition {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Values,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Predicate,

        [Parameter()]
        [ValidateRange(1, 100)]
        [int]$ConsecutiveSamples = 3
    )

    $run = 0
    foreach ($value in $Values) {
        if (& $Predicate $value) {
            $run++
            if ($run -ge $ConsecutiveSamples) {
                return $true
            }
        }
        else {
            $run = 0
        }
    }
    return $false
}

function Test-PplidBackupCapacity {
    [CmdletBinding()]
    param(
        [Parameter()]
        [object[]]$Samples,

        [Parameter()]
        [scriptblock]$SampleProvider,

        [Parameter()]
        [ValidateRange(1, 120)]
        [int]$SampleCount = 12,

        [Parameter()]
        [ValidateRange(0, 60)]
        [int]$SampleIntervalSeconds = 5,

        [Parameter()]
        [ValidateRange(1, 100)]
        [int]$PersistentSampleCount = 3,

        [Parameter()]
        [double]$MaximumCpuPercent = 80,

        [Parameter()]
        [double]$CpuWarningPercent = 70,

        [Parameter()]
        [double]$MinimumAvailableMemoryGB = 3.2,

        [Parameter()]
        [double]$MemoryWarningGB = 4.75,

        [Parameter()]
        [double]$CriticalDiskReserveGB = 10,

        [Parameter()]
        [double]$DiskWarningGB = 12,

        [Parameter()]
        [double]$MinimumDumpAllowanceGB = 5,

        [Parameter()]
        [long[]]$DumpHistoryBytes = @(),

        [Parameter()]
        [ValidateRange(0, 1000000)]
        [int]$PendingDumpCount = 0,

        [Parameter()]
        [ValidateRange(1, 1000)]
        [int]$MaximumPendingBeforeBlock = 1,

        [Parameter()]
        [string]$StagingPath = 'C:\pplid\backups\staging'
    )

    $collected = @()
    if ($Samples) {
        $collected = @($Samples)
    }
    else {
        for ($index = 0; $index -lt $SampleCount; $index++) {
            $collected += Get-PplidBackupResourceSample -SampleProvider $SampleProvider -StagingPath $StagingPath
            if ($index -lt ($SampleCount - 1) -and $SampleIntervalSeconds -gt 0) {
                Start-Sleep -Seconds $SampleIntervalSeconds
            }
        }
    }

    if ($collected.Count -eq 0) {
        throw 'Nenhuma amostra foi fornecida para o preflight.'
    }

    foreach ($sample in $collected) {
        foreach ($property in @('CpuPercent', 'AvailableMemoryGB', 'FreeDiskGB')) {
            if ($null -eq $sample.PSObject.Properties[$property]) {
                throw "A amostra não contém a propriedade obrigatória '$property'."
            }
        }
    }

    $cpuValues = @($collected | ForEach-Object { [double]$_.CpuPercent })
    $memoryValues = @($collected | ForEach-Object { [double]$_.AvailableMemoryGB })
    $diskValues = @($collected | ForEach-Object { [double]$_.FreeDiskGB })
    $estimate = Get-PplidBackupDumpEstimate -DumpSizesBytes $DumpHistoryBytes
    $dumpAllowanceGB = [Math]::Max($MinimumDumpAllowanceGB, $estimate.EstimateGB)
    $requiredFreeDiskGB = $CriticalDiskReserveGB + $dumpAllowanceGB
    $cpuPersistent = Test-PplidPersistentCondition -Values $cpuValues -ConsecutiveSamples $PersistentSampleCount -Predicate { param($value) $value -ge $MaximumCpuPercent }
    $memoryPersistent = Test-PplidPersistentCondition -Values $memoryValues -ConsecutiveSamples $PersistentSampleCount -Predicate { param($value) $value -lt $MinimumAvailableMemoryGB }
    $cpuMeasure = $cpuValues | Measure-Object -Average -Maximum
    $memoryMeasure = $memoryValues | Measure-Object -Average -Minimum
    $diskMeasure = $diskValues | Measure-Object -Minimum
    $warnings = New-Object System.Collections.Generic.List[string]

    if ([double]$cpuMeasure.Average -ge $CpuWarningPercent) { $warnings.Add('high_cpu') }
    if ([double]$memoryMeasure.Average -lt $MemoryWarningGB) { $warnings.Add('low_memory') }
    if ([double]$diskMeasure.Minimum -lt $DiskWarningGB) { $warnings.Add('low_disk') }
    if ($PendingDumpCount -gt 0) { $warnings.Add('upload_pending') }

    $passed = $true
    $reason = $null
    $exitCode = 0

    if ([double]$diskMeasure.Minimum -lt $requiredFreeDiskGB) {
        $passed = $false; $reason = 'deferred_low_disk'; $exitCode = 13
    }
    elseif ($PendingDumpCount -ge $MaximumPendingBeforeBlock) {
        $passed = $false; $reason = 'upload_pending'; $exitCode = 20
    }
    elseif ($memoryPersistent) {
        $passed = $false; $reason = 'deferred_low_memory'; $exitCode = 12
    }
    elseif ($cpuPersistent) {
        $passed = $false; $reason = 'deferred_high_cpu'; $exitCode = 11
    }

    [pscustomobject]@{
        Passed       = $passed
        Reason       = $reason
        ExitCode     = $exitCode
        Warnings     = @($warnings)
        Metrics      = [pscustomobject]@{
            SampleCount             = $collected.Count
            CpuAveragePercent       = [Math]::Round([double]$cpuMeasure.Average, 2)
            CpuMaximumPercent       = [Math]::Round([double]$cpuMeasure.Maximum, 2)
            CpuPersistentHigh       = $cpuPersistent
            MemoryAverageGB         = [Math]::Round([double]$memoryMeasure.Average, 3)
            MemoryMinimumGB         = [Math]::Round([double]$memoryMeasure.Minimum, 3)
            MemoryPersistentLow     = $memoryPersistent
            FreeDiskMinimumGB       = [Math]::Round([double]$diskMeasure.Minimum, 3)
            CriticalDiskReserveGB   = $CriticalDiskReserveGB
            DumpAllowanceGB         = [Math]::Round($dumpAllowanceGB, 3)
            RequiredFreeDiskGB      = [Math]::Round($requiredFreeDiskGB, 3)
            EstimatedDumpGB         = $estimate.EstimateGB
            DumpHistoryCount        = $estimate.HistoryCount
            PendingDumpCount        = $PendingDumpCount
            MaximumPendingBeforeBlock = $MaximumPendingBeforeBlock
        }
    }
}

function New-PplidBackupWatchdogReport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Samples,

        [Parameter()]
        [double]$CpuAlertPercent = 90,

        [Parameter()]
        [double]$MemoryAlertGB = 2.5,

        [Parameter()]
        [double]$DiskAlertGB = 12,

        [Parameter()]
        [ValidateRange(1, 100)]
        [int]$PersistentSampleCount = 3
    )

    if ($Samples.Count -eq 0) { throw 'O watchdog requer ao menos uma amostra.' }
    $cpu = @($Samples | ForEach-Object { [double]$_.CpuPercent })
    $memory = @($Samples | ForEach-Object { [double]$_.AvailableMemoryGB })
    $disk = @($Samples | ForEach-Object { [double]$_.FreeDiskGB })
    $alerts = New-Object System.Collections.Generic.List[string]

    if (Test-PplidPersistentCondition -Values $cpu -ConsecutiveSamples $PersistentSampleCount -Predicate { param($value) $value -ge $CpuAlertPercent }) { $alerts.Add('critical_cpu_observed') }
    if (Test-PplidPersistentCondition -Values $memory -ConsecutiveSamples $PersistentSampleCount -Predicate { param($value) $value -lt $MemoryAlertGB }) { $alerts.Add('critical_memory_observed') }
    if (($disk | Measure-Object -Minimum).Minimum -lt $DiskAlertGB) { $alerts.Add('critical_disk_observed') }

    [pscustomobject]@{
        ObservationalOnly = $true
        SampleCount       = $Samples.Count
        Alerts            = @($alerts)
        LatestSample      = $Samples[-1]
        CpuMaximumPercent = [Math]::Round([double](($cpu | Measure-Object -Maximum).Maximum), 2)
        MemoryMinimumGB   = [Math]::Round([double](($memory | Measure-Object -Minimum).Minimum), 3)
        FreeDiskMinimumGB = [Math]::Round([double](($disk | Measure-Object -Minimum).Minimum), 3)
    }
}

function Invoke-PplidBackupResourceWatchdog {
    [CmdletBinding()]
    param(
        [Parameter()]
        [scriptblock]$SampleProvider,

        [Parameter()]
        [ValidateRange(1, 100000)]
        [int]$SampleCount = 1,

        [Parameter()]
        [ValidateRange(0, 3600)]
        [int]$SampleIntervalSeconds = 30,

        [Parameter()]
        [string]$StagingPath = 'C:\pplid\backups\staging'
    )

    $samples = @()
    for ($index = 0; $index -lt $SampleCount; $index++) {
        $samples += Get-PplidBackupResourceSample -SampleProvider $SampleProvider -StagingPath $StagingPath
        if ($index -lt ($SampleCount - 1) -and $SampleIntervalSeconds -gt 0) {
            Start-Sleep -Seconds $SampleIntervalSeconds
        }
    }
    return New-PplidBackupWatchdogReport -Samples $samples
}
