$libraryPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\backup_postgres.ps1'
. $libraryPath

Describe 'Read-PplidBackendEnv' {
    It 'parses common dotenv forms without printing secrets' {
        $envPath = Join-Path $TestDrive 'backend.env'
        @"
# comment
POSTGRES_DB=pplid_test
POSTGRES_USER=postgres
POSTGRES_PASSWORD="secret#value"
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
export OPTIONAL=value # inline comment
"@ | Set-Content -LiteralPath $envPath -Encoding UTF8

        $map = Read-PplidBackendEnv -Path $envPath -RequiredKeys @(
            'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_HOST', 'POSTGRES_PORT'
        )

        $map.POSTGRES_PASSWORD | Should Be 'secret#value'
        $map.OPTIONAL | Should Be 'value'
    }

    It 'reports a missing key without exposing another value' {
        $envPath = Join-Path $TestDrive 'missing.env'
        'POSTGRES_PASSWORD=do-not-leak' | Set-Content -LiteralPath $envPath -Encoding UTF8

        $message = ''
        try {
            Read-PplidBackendEnv -Path $envPath -RequiredKeys @('POSTGRES_DB') | Out-Null
        } catch {
            $message = $_.Exception.Message
        }

        $message | Should Match 'POSTGRES_DB'
        $message | Should Not Match 'do-not-leak'
    }
}

Describe 'Get-PplidPostgresEnvSettings' {
    It 'never exposes the password in properties, enumeration, or serialization' {
        $envPath = Join-Path $TestDrive 'settings.env'
        @(
            'POSTGRES_DB=pplid_test'
            'POSTGRES_USER=postgres'
            'POSTGRES_PASSWORD=unique-secret-not-for-output'
            'POSTGRES_HOST=localhost'
            'POSTGRES_PORT=5432'
        ) | Set-Content -LiteralPath $envPath -Encoding UTF8

        $settings = Get-PplidPostgresEnvSettings -BackendEnvPath $envPath
        $propertyNames = @($settings.PSObject.Properties | ForEach-Object { $_.Name })
        $enumerated = $settings | Format-List * | Out-String
        $serialized = [System.Management.Automation.PSSerializer]::Serialize($settings)

        ($propertyNames -contains 'Password') | Should Be $false
        $settings.PSObject.Properties['Password'] | Should BeNullOrEmpty
        $enumerated | Should Not Match 'Password'
        $enumerated | Should Not Match 'unique-secret-not-for-output'
        $serialized | Should Not Match 'Password'
        $serialized | Should Not Match 'unique-secret-not-for-output'
        $settings.Database | Should Be 'pplid_test'
        $settings.Username | Should Be 'postgres'
        $settings.HostName | Should Be 'localhost'
        $settings.Port | Should Be 5432
    }
}

Describe 'Invoke-PplidPostgresDump' {
    It 'honors WhatIf before reading credentials or starting pg_dump' {
        $destination = Join-Path $TestDrive 'backup.dump.partial'
        $result = Invoke-PplidPostgresDump `
            -BackendEnvPath (Join-Path $TestDrive 'does-not-exist.env') `
            -PgDumpPath (Join-Path $TestDrive 'does-not-exist.exe') `
            -DestinationPath $destination `
            -WhatIf

        $result.Skipped | Should Be $true
        Test-Path -LiteralPath $destination | Should Be $false
    }

    It 'forwards observer and requests BelowNormal without exposing password' {
        $envPath = Join-Path $TestDrive 'dump.env'
        @(
            'POSTGRES_DB=pplid_test'
            'POSTGRES_USER=postgres'
            'POSTGRES_PASSWORD=unique-dump-secret'
            'POSTGRES_HOST=localhost'
            'POSTGRES_PORT=5432'
        ) | Set-Content -LiteralPath $envPath -Encoding UTF8
        $fakePgDump = Join-Path $TestDrive 'pg_dump.exe'
        New-Item -ItemType File -Path $fakePgDump | Out-Null
        $destination = Join-Path $TestDrive 'backup.dump.partial'
        $observer = { param($context) $null }

        Mock Invoke-PplidNativeProcess {
            [PSCustomObject]@{
                ExitCode = 0; StandardErr = ''
                StartedAtUtc = [DateTime]::UtcNow; FinishedAtUtc = [DateTime]::UtcNow
                PriorityRequested = 'BelowNormal'; PriorityApplied = $true; PriorityError = $null
                ObserverIntervalSeconds = 7
                WatchdogObservations = @([PSCustomObject]@{ Succeeded = $true })
            }
        }

        $result = Invoke-PplidPostgresDump -BackendEnvPath $envPath -PgDumpPath $fakePgDump `
            -DestinationPath $destination -Observer $observer -ObserverIntervalSeconds 7

        Assert-MockCalled Invoke-PplidNativeProcess -Times 1 -Exactly -ParameterFilter {
            $PriorityClass -eq 'BelowNormal' -and $ObserverIntervalSeconds -eq 7 -and
            $Observer -eq $observer -and $EnvironmentVariables.Count -eq 1 -and
            $EnvironmentVariables.PGPASSWORD -eq 'unique-dump-secret' -and
            ($Arguments -join ' ') -notmatch 'unique-dump-secret'
        }
        $result.PriorityRequested | Should Be 'BelowNormal'
        $result.WatchdogObservations.Count | Should BeGreaterThan 0
        ($result | ConvertTo-Json -Depth 6) | Should Not Match 'unique-dump-secret'
    }
}

Describe 'Protect-PplidSecretText' {
    It 'redacts every occurrence of a secret' {
        Protect-PplidSecretText -Text 'failure secret then secret' -Secret 'secret' |
            Should Be 'failure [REDACTED] then [REDACTED]'
    }
}

Describe 'Invoke-PplidNativeProcess watchdog' {
    It 'calls observer periodically for an inoffensive process' {
        $script:observerCalls = 0
        $observer = {
            param($context)
            $script:observerCalls++
            if ($context.ProcessId -le 0) { throw 'invalid process id' }
        }
        $result = Invoke-PplidNativeProcess -FilePath (Join-Path $PSHOME 'powershell.exe') `
            -Arguments @('-NoProfile', '-Command', 'Start-Sleep -Milliseconds 2200') `
            -Observer $observer -ObserverIntervalSeconds 1 -PriorityClass BelowNormal

        $result.ExitCode | Should Be 0
        $script:observerCalls | Should BeGreaterThan 1
        $result.WatchdogObservations.Count | Should Be $script:observerCalls
        @($result.WatchdogObservations | Where-Object { -not $_.Succeeded }).Count | Should Be 0
        $result.PriorityRequested | Should Be 'BelowNormal'
        $result.PriorityApplied.GetType().Name | Should Be 'Boolean'
    }

    It 'does not interrupt the process when observer fails' {
        $result = Invoke-PplidNativeProcess -FilePath (Join-Path $PSHOME 'powershell.exe') `
            -Arguments @('-NoProfile', '-Command', 'Start-Sleep -Milliseconds 1200') `
            -Observer { throw 'observer-only failure' } -ObserverIntervalSeconds 1

        $result.ExitCode | Should Be 0
        $result.WatchdogObservations.Count | Should BeGreaterThan 0
        $result.WatchdogObservations[0].Succeeded | Should Be $false
        $result.WatchdogObservations[0].Error | Should Match 'observer-only failure'
    }

    It 'contains no process termination primitive in production library' {
        $source = Get-Content -LiteralPath $libraryPath -Raw
        $source | Should Not Match '(?i)Stop-Process'
        $source | Should Not Match '(?i)\.Kill\s*\('
    }
}

Describe 'Get-PplidPostgresBackupMetadata' {
    It 'returns lowercase SHA256 and basic file metadata' {
        $dumpPath = Join-Path $TestDrive 'sample.dump'
        'fake custom dump' | Set-Content -LiteralPath $dumpPath -Encoding ASCII

        $metadata = Get-PplidPostgresBackupMetadata -DumpPath $dumpPath -DatabaseName 'pplid_test'

        $metadata.Database | Should Be 'pplid_test'
        $metadata.SizeBytes | Should BeGreaterThan 0
        $metadata.Sha256 | Should Match '^[0-9a-f]{64}$'
        $metadata.FullPath | Should Be (Get-Item -LiteralPath $dumpPath).FullName
    }
}
