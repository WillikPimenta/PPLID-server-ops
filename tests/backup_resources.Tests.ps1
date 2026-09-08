$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path (Split-Path -Parent $here) 'lib\backup_resources.ps1')

function New-TestSamples {
    param(
        [double[]]$Cpu = @(20, 20, 20),
        [double[]]$Memory = @(8, 8, 8),
        [double[]]$Disk = @(30, 30, 30)
    )
    $count = @($Cpu.Count, $Memory.Count, $Disk.Count | Measure-Object -Minimum).Minimum
    @(for ($i = 0; $i -lt $count; $i++) {
        [pscustomobject]@{
            TimestampUtc = [DateTime]::UtcNow.AddSeconds($i)
            CpuPercent = $Cpu[$i]
            AvailableMemoryGB = $Memory[$i]
            FreeDiskGB = $Disk[$i]
        }
    })
}

Describe 'backup_resources - estimativa' {
    It 'usa piso de 1 GB sem histórico' {
        $result = Get-PplidBackupDumpEstimate
        $result.EstimateGB | Should Be 1
        $result.HistoryCount | Should Be 0
    }

    It 'usa a maior proteção entre maior x1.5 e média x2' {
        $gib = [long]1073741824
        $result = Get-PplidBackupDumpEstimate -DumpSizesBytes @([long](0.2*$gib), [long](0.3*$gib), [long](0.4*$gib))
        $result.EstimateGB | Should Be 1

        $result = Get-PplidBackupDumpEstimate -DumpSizesBytes @($gib, (2*$gib), (3*$gib))
        $result.EstimateGB | Should Be 4.5
    }
}

Describe 'backup_resources - coleta injetável' {
    It 'retorna a amostra do provedor sem consultar CIM' {
        $expected = [pscustomobject]@{ CpuPercent = 12; AvailableMemoryGB = 9; FreeDiskGB = 40 }
        $actual = Get-PplidBackupResourceSample -SampleProvider { $expected }
        $actual | Should Be $expected
    }

    It 'coleta o número configurado sem espera quando o intervalo é zero' {
        $script:calls = 0
        $result = Test-PplidBackupCapacity -SampleProvider {
            $script:calls++
            [pscustomobject]@{ CpuPercent = 10; AvailableMemoryGB = 10; FreeDiskGB = 40 }
        } -SampleCount 4 -SampleIntervalSeconds 0
        $script:calls | Should Be 4
        $result.Passed | Should Be $true
    }
}

Describe 'backup_resources - preflight' {
    It 'aprova capacidade saudável e expõe métricas estruturadas' {
        $result = Test-PplidBackupCapacity -Samples (New-TestSamples) -PendingDumpCount 0
        $result.Passed | Should Be $true
        $result.ExitCode | Should Be 0
        $result.Metrics.RequiredFreeDiskGB | Should Be 15
        $result.Warnings.Count | Should Be 0
    }

    It 'não bloqueia por um pico isolado de CPU' {
        $samples = New-TestSamples -Cpu @(20, 95, 20, 20) -Memory @(8,8,8,8) -Disk @(30,30,30,30)
        $result = Test-PplidBackupCapacity -Samples $samples
        $result.Passed | Should Be $true
        $result.Metrics.CpuPersistentHigh | Should Be $false
    }

    It 'bloqueia CPU alta por três amostras consecutivas' {
        $samples = New-TestSamples -Cpu @(20, 80, 85, 90) -Memory @(8,8,8,8) -Disk @(30,30,30,30)
        $result = Test-PplidBackupCapacity -Samples $samples
        $result.Passed | Should Be $false
        $result.Reason | Should Be 'deferred_high_cpu'
        $result.ExitCode | Should Be 11
    }

    It 'não bloqueia por uma amostra isolada de RAM baixa' {
        $samples = New-TestSamples -Cpu @(20,20,20,20) -Memory @(8,2,8,8) -Disk @(30,30,30,30)
        (Test-PplidBackupCapacity -Samples $samples).Passed | Should Be $true
    }

    It 'bloqueia RAM baixa persistente' {
        $samples = New-TestSamples -Cpu @(20,20,20,20) -Memory @(8,3.1,3.0,2.9) -Disk @(30,30,30,30)
        $result = Test-PplidBackupCapacity -Samples $samples
        $result.Reason | Should Be 'deferred_low_memory'
        $result.ExitCode | Should Be 12
    }

    It 'bloqueia disco abaixo da reserva mais allowance' {
        $samples = New-TestSamples -Disk @(14.9,14.9,14.9)
        $result = Test-PplidBackupCapacity -Samples $samples
        $result.Reason | Should Be 'deferred_low_disk'
        $result.ExitCode | Should Be 13
        $result.Metrics.RequiredFreeDiskGB | Should Be 15
    }

    It 'aumenta a exigência de disco conforme estimativa do histórico' {
        $gib = [long]1073741824
        $result = Test-PplidBackupCapacity -Samples (New-TestSamples -Disk @(16,16,16)) -DumpHistoryBytes @((4*$gib),(4*$gib),(4*$gib))
        $result.Metrics.EstimatedDumpGB | Should Be 8
        $result.Metrics.RequiredFreeDiskGB | Should Be 18
        $result.Reason | Should Be 'deferred_low_disk'
    }

    It 'bloqueia novo dump quando já existe uma pendência' {
        $result = Test-PplidBackupCapacity -Samples (New-TestSamples) -PendingDumpCount 1
        $result.Reason | Should Be 'upload_pending'
        $result.ExitCode | Should Be 20
        ($result.Warnings -contains 'upload_pending') | Should Be $true
    }

    It 'produz avisos sem impedir quando ainda está acima dos bloqueios' {
        $samples = New-TestSamples -Cpu @(71,71,71) -Memory @(4.5,4.5,4.5) -Disk @(16,16,16)
        $result = Test-PplidBackupCapacity -Samples $samples
        $result.Passed | Should Be $true
        ($result.Warnings -contains 'high_cpu') | Should Be $true
        ($result.Warnings -contains 'low_memory') | Should Be $true
    }
}

Describe 'backup_resources - watchdog observacional' {
    It 'relata alertas persistentes sem ação de encerramento' {
        $samples = New-TestSamples -Cpu @(91,92,93) -Memory @(2.4,2.3,2.2) -Disk @(11,11,11)
        $result = New-PplidBackupWatchdogReport -Samples $samples
        $result.ObservationalOnly | Should Be $true
        ($result.Alerts -contains 'critical_cpu_observed') | Should Be $true
        ($result.Alerts -contains 'critical_memory_observed') | Should Be $true
        ($result.Alerts -contains 'critical_disk_observed') | Should Be $true
    }

    It 'invoca provedor injetado e retorna relatório' {
        $result = Invoke-PplidBackupResourceWatchdog -SampleProvider {
            [pscustomobject]@{ CpuPercent = 10; AvailableMemoryGB = 10; FreeDiskGB = 40 }
        } -SampleCount 2 -SampleIntervalSeconds 0
        $result.SampleCount | Should Be 2
        $result.Alerts.Count | Should Be 0
    }

    It 'não contém comandos destrutivos ou de término de processo' {
        $modulePath = Join-Path (Split-Path -Parent $here) 'lib\backup_resources.ps1'
        $source = Get-Content -LiteralPath $modulePath -Raw
        $source | Should Not Match '(?i)Stop-Process|Remove-Item|\.Kill\s*\('
    }
}
