$opsRoot = Split-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) -Parent
$scriptPath = Join-Path $opsRoot "backup_main.ps1"

. (Join-Path $opsRoot "lib\\backup_postgres.ps1")
. (Join-Path $opsRoot "lib\\backup_onedrive.ps1")
. (Join-Path $opsRoot "lib\\backup_resources.ps1")
. (Join-Path $opsRoot "lib\\backup_rclone.ps1")

if (-not (Get-Command Repair-PplidInterruptedOneDrivePublish -ErrorAction SilentlyContinue)) {
    function Repair-PplidInterruptedOneDrivePublish {
        param([string]$LocalDumpPath, [string]$CloudDirectory)
    }
}

$tokens = $null
$parseErrors = $null
$scriptAst = [Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref]$tokens, [ref]$parseErrors
)
foreach ($functionName in @(
    "Write-PplidBackupLog",
    "Write-PplidBackupSha256File",
    "Test-PplidBackupFilesEquivalent",
    "Move-PplidAbandonedPartialsToQuarantine",
    "Move-PplidBackupSetToConfirmed",
    "Resume-PplidLocalConfirmations",
    "Get-PplidTransportExitCode",
    "Get-PplidMinimumRcloneFreeBytes",
    "Complete-PplidRcloneBackup",
    "Write-PplidBackupManifest",
    "Resolve-PplidPendingDumpManifest",
    "Get-PplidCloudSetForLocalDump",
    "Complete-PplidPendingCloudBackup"
)) {
    $definition = $scriptAst.Find({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $functionName
    }, $true)
    Invoke-Expression $definition.Extent.Text
}

$Environment = "MAIN"
$RetentionCount = 7
$OneDriveRoot = ""
$OnlineOnlyTimeoutSeconds = 1
$OnlineOnlyPollSeconds = 1

Describe "backup_main resilience" {
    BeforeEach {
        $script:testRoot = Join-Path ([IO.Path]::GetTempPath()) (
            "pplid-backup-main-" + [Guid]::NewGuid().ToString("N")
        )
        New-Item -ItemType Directory -Path $script:testRoot -Force | Out-Null
    }

    AfterEach {
        if (Test-Path -LiteralPath $script:testRoot) {
            Remove-Item -LiteralPath $script:testRoot -Recurse -Force
        }
    }

    It "revalidates a pending dump and rebuilds its missing manifest" {
        $dump = Join-Path $script:testRoot "pplid_main_20260828_020000.dump"
        $manifest = Join-Path $script:testRoot "pplid_main_20260828_020000.manifest.json"
        [IO.File]::WriteAllText($dump, "valid dump")
        Mock Test-PplidPostgresDump {
            [PSCustomObject]@{ Valid = $true; EntryCount = 42; ExitCode = 0; StandardErr = "" }
        }
        Mock Get-PplidPostgresBackupMetadata {
            [PSCustomObject]@{
                FileName = [IO.Path]::GetFileName($dump)
                SizeBytes = 10
                Sha256 = "abc123"
                Database = "pplid_main"
                GeneratedAtUtc = [DateTime]::UtcNow
            }
        }
        $tools = [PSCustomObject]@{ PgDumpVersion = "14"; PgRestoreVersion = "14" }

        $result = Resolve-PplidPendingDumpManifest -DumpPath $dump -ManifestPath $manifest `
            -PgRestorePath "pg_restore.exe" -DatabaseName "pplid_main" -Tools $tools

        $result.Ready | Should Be $true
        $result.Rebuilt | Should Be $true
        (Test-Path -LiteralPath $manifest -PathType Leaf) | Should Be $true
        $json = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
        $json.recoveredFromPending | Should Be $true
        $json.restoreListEntries | Should Be 42
        Assert-MockCalled Test-PplidPostgresDump -Times 1 -Exactly -Scope It
        Assert-MockCalled Get-PplidPostgresBackupMetadata -Times 1 -Exactly
    }

    It "rebuilds a truncated existing manifest after dump revalidation" {
        $dump = Join-Path $script:testRoot "pplid_main_20260828_020010.dump"
        $manifest = Join-Path $script:testRoot "pplid_main_20260828_020010.manifest.json"
        [IO.File]::WriteAllText($dump, "valid dump")
        [IO.File]::WriteAllText($manifest, '{"fileName":')
        Mock Get-PplidPostgresBackupMetadata {
            [PSCustomObject]@{
                FileName = [IO.Path]::GetFileName($dump)
                SizeBytes = 10
                Sha256 = "abc123"
                Database = "pplid_main"
                GeneratedAtUtc = [DateTime]::UtcNow
            }
        }
        Mock Test-PplidPostgresDump {
            [PSCustomObject]@{ Valid = $true; EntryCount = 43; ExitCode = 0; StandardErr = "" }
        }

        $result = Resolve-PplidPendingDumpManifest -DumpPath $dump -ManifestPath $manifest `
            -PgRestorePath "pg_restore.exe" -DatabaseName "pplid_main" `
            -Tools ([PSCustomObject]@{ PgDumpVersion = "14"; PgRestoreVersion = "14" })

        $result.Ready | Should Be $true
        $result.Rebuilt | Should Be $true
        $rebuilt = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
        $rebuilt.fileName | Should Be ([IO.Path]::GetFileName($dump))
        $rebuilt.recoveredFromPending | Should Be $true
        Assert-MockCalled Test-PplidPostgresDump -Times 1 -Exactly -Scope It
    }

    It "rebuilds an existing manifest whose identity and checksum are incoherent" {
        $dump = Join-Path $script:testRoot "pplid_main_20260828_020011.dump"
        $manifest = Join-Path $script:testRoot "pplid_main_20260828_020011.manifest.json"
        [IO.File]::WriteAllText($dump, "valid dump")
        @{
            fileName = "other.dump"
            environment = "DEV"
            database = "other"
            sizeBytes = 999
            sha256 = "wrong"
        } | ConvertTo-Json | Set-Content -LiteralPath $manifest
        Mock Get-PplidPostgresBackupMetadata {
            [PSCustomObject]@{
                FileName = [IO.Path]::GetFileName($dump)
                SizeBytes = 10
                Sha256 = "abc123"
                Database = "pplid_main"
                GeneratedAtUtc = [DateTime]::UtcNow
            }
        }
        Mock Test-PplidPostgresDump {
            [PSCustomObject]@{ Valid = $true; EntryCount = 44; ExitCode = 0; StandardErr = "" }
        }

        $result = Resolve-PplidPendingDumpManifest -DumpPath $dump -ManifestPath $manifest `
            -PgRestorePath "pg_restore.exe" -DatabaseName "pplid_main" `
            -Tools ([PSCustomObject]@{ PgDumpVersion = "14"; PgRestoreVersion = "14" })

        $result.Rebuilt | Should Be $true
        $rebuilt = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
        $rebuilt.environment | Should Be "MAIN"
        $rebuilt.database | Should Be "pplid_main"
        $rebuilt.sizeBytes | Should Be 10
        $rebuilt.sha256 | Should Be "abc123"
    }

    It "quarantines an invalid pending dump with a unique extension" {
        $dump = Join-Path $script:testRoot "pplid_main_20260828_020001.dump"
        $manifest = Join-Path $script:testRoot "pplid_main_20260828_020001.manifest.json"
        [IO.File]::WriteAllText($dump, "invalid dump")
        Mock Test-PplidPostgresDump {
            [PSCustomObject]@{ Valid = $false; EntryCount = 0; ExitCode = 1; StandardErr = "invalid" }
        }

        $result = Resolve-PplidPendingDumpManifest -DumpPath $dump -ManifestPath $manifest `
            -PgRestorePath "pg_restore.exe" -DatabaseName "pplid_main" `
            -Tools ([PSCustomObject]@{ PgDumpVersion = "14"; PgRestoreVersion = "14" })

        $result.Ready | Should Be $false
        $result.QuarantinePath.Contains(".dump.quarantine.") | Should Be $true
        (Test-Path -LiteralPath $dump) | Should Be $false
        (Test-Path -LiteralPath $result.QuarantinePath -PathType Leaf) | Should Be $true
        (Test-Path -LiteralPath $manifest) | Should Be $false
    }

    It "resumes a complete confirming transaction on PowerShell 5.1" {
        $confirming = Join-Path $script:testRoot '.confirming'
        $confirmed = Join-Path $script:testRoot 'confirmed'
        $base = 'pplid_main_20260828_040000'
        $transaction = Join-Path $confirming $base
        New-Item -ItemType Directory -Path $transaction -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $transaction ($base + '.manifest.json')), '{}')
        [IO.File]::WriteAllText((Join-Path $transaction ($base + '.sha256')), ('a' * 64))
        [IO.File]::WriteAllText((Join-Path $transaction ($base + '.dump')), 'dump')

        $result = @(Resume-PplidLocalConfirmations -ConfirmingDirectory $confirming `
            -ConfirmedDirectory $confirmed)

        $result.Count | Should Be 1
        $result[0].AlreadyConfirmed | Should Be $false
        (Test-Path -LiteralPath $transaction) | Should Be $false
        $destination = Join-Path $confirmed $base
        (Test-Path -LiteralPath (Join-Path $destination ($base + '.manifest.json'))) | Should Be $true
        (Test-Path -LiteralPath (Join-Path $destination ($base + '.sha256'))) | Should Be $true
        (Test-Path -LiteralPath (Join-Path $destination ($base + '.dump'))) | Should Be $true
    }

    It "preserves a partial confirming transaction without promoting it" {
        $confirming = Join-Path $script:testRoot '.confirming'
        $confirmed = Join-Path $script:testRoot 'confirmed'
        $base = 'pplid_main_20260828_040001'
        $transaction = Join-Path $confirming $base
        New-Item -ItemType Directory -Path $transaction -Force | Out-Null
        $manifest = Join-Path $transaction ($base + '.manifest.json')
        [IO.File]::WriteAllText($manifest, '{}')

        $result = @(Resume-PplidLocalConfirmations -ConfirmingDirectory $confirming `
            -ConfirmedDirectory $confirmed)

        $result.Count | Should Be 0
        (Test-Path -LiteralPath $manifest -PathType Leaf) | Should Be $true
        (Test-Path -LiteralPath (Join-Path $confirmed $base)) | Should Be $false
    }

    It "treats a completed rename as already recovered on the next run" {
        $confirming = Join-Path $script:testRoot '.confirming'
        $confirmed = Join-Path $script:testRoot 'confirmed'
        $base = 'pplid_main_20260828_040002'
        $destination = Join-Path $confirmed $base
        New-Item -ItemType Directory -Path $confirming, $destination -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $destination ($base + '.manifest.json')), '{}')
        [IO.File]::WriteAllText((Join-Path $destination ($base + '.sha256')), ('b' * 64))
        [IO.File]::WriteAllText((Join-Path $destination ($base + '.dump')), 'dump')

        $result = @(Resume-PplidLocalConfirmations -ConfirmingDirectory $confirming `
            -ConfirmedDirectory $confirmed)

        $result.Count | Should Be 0
        (Test-Path -LiteralPath $destination -PathType Container) | Should Be $true
    }

    It "accepts equivalent confirmed and confirming sets without overwriting or deleting" {
        $confirming = Join-Path $script:testRoot '.confirming'
        $confirmed = Join-Path $script:testRoot 'confirmed'
        $base = 'pplid_main_20260828_040003'
        $transaction = Join-Path $confirming $base
        $destination = Join-Path $confirmed $base
        New-Item -ItemType Directory -Path $transaction, $destination -Force | Out-Null
        foreach ($suffix in @('.manifest.json', '.sha256', '.dump')) {
            [IO.File]::WriteAllText((Join-Path $transaction ($base + $suffix)), ('same-' + $suffix))
            [IO.File]::WriteAllText((Join-Path $destination ($base + $suffix)), ('same-' + $suffix))
        }

        $result = @(Resume-PplidLocalConfirmations -ConfirmingDirectory $confirming `
            -ConfirmedDirectory $confirmed)

        $result.Count | Should Be 1
        $result[0].AlreadyConfirmed | Should Be $true
        $result[0].ConfirmingDirectoryPreserved | Should Be $true
        (Get-ChildItem -LiteralPath $transaction -File).Count | Should Be 3
        (Get-ChildItem -LiteralPath $destination -File).Count | Should Be 3
    }

    It "fails on divergent confirmed and confirming sets while preserving both" {
        $confirming = Join-Path $script:testRoot '.confirming'
        $confirmed = Join-Path $script:testRoot 'confirmed'
        $base = 'pplid_main_20260828_040004'
        $transaction = Join-Path $confirming $base
        $destination = Join-Path $confirmed $base
        New-Item -ItemType Directory -Path $transaction, $destination -Force | Out-Null
        foreach ($suffix in @('.manifest.json', '.sha256', '.dump')) {
            [IO.File]::WriteAllText((Join-Path $transaction ($base + $suffix)), ('local-' + $suffix))
            [IO.File]::WriteAllText((Join-Path $destination ($base + $suffix)), ('confirmed-' + $suffix))
        }

        { Resume-PplidLocalConfirmations -ConfirmingDirectory $confirming `
            -ConfirmedDirectory $confirmed } | Should Throw

        (Get-ChildItem -LiteralPath $transaction -File).Count | Should Be 3
        (Get-ChildItem -LiteralPath $destination -File).Count | Should Be 3
    }

    It "moves only matching abandoned partials to quarantine and preserves their bytes" {
        $staging = Join-Path $script:testRoot 'staging'
        $quarantine = Join-Path $script:testRoot 'quarantine'
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        $partial = Join-Path $staging 'pplid_main_20260828_040005.dump.partial'
        $unrelated = Join-Path $staging 'other_main_20260828_040005.dump.partial'
        [IO.File]::WriteAllText($partial, 'partial payload')
        [IO.File]::WriteAllText($unrelated, 'unrelated payload')

        $result = @(Move-PplidAbandonedPartialsToQuarantine -StagingDirectory $staging `
            -QuarantineDirectory $quarantine -FilePrefix 'pplid_main_')

        $result.Count | Should Be 1
        (Test-Path -LiteralPath $partial) | Should Be $false
        (Test-Path -LiteralPath $result[0].QuarantinePath -PathType Leaf) | Should Be $true
        [IO.File]::ReadAllText($result[0].QuarantinePath) | Should Be 'partial payload'
        [IO.File]::ReadAllText($unrelated) | Should Be 'unrelated payload'
    }

    It "runs retention before deleting staging and keeps the confirmed cloud trio" {
        $cloud = Join-Path $script:testRoot "cloud"
        New-Item -ItemType Directory -Path $cloud | Out-Null
        $localDump = Join-Path $script:testRoot "pplid_main_20260828_020002.dump"
        $localManifest = Join-Path $script:testRoot "pplid_main_20260828_020002.manifest.json"
        [IO.File]::WriteAllText($localDump, "payload")
        [IO.File]::WriteAllText($localManifest, "{}")
        $cloudSet = Get-PplidBackupSetPaths -DumpPath (
            Join-Path $cloud ([IO.Path]::GetFileName($localDump))
        )
        [IO.File]::WriteAllText($cloudSet.Dump, "payload")
        [IO.File]::WriteAllText($cloudSet.Manifest, "{}")
        [IO.File]::WriteAllText($cloudSet.Sha256, "hash")
        $script:stagingExistedDuringRetention = $false
        Mock Repair-PplidInterruptedOneDrivePublish { }
        Mock Write-PplidBackupLog { }
        Mock Request-PplidOneDriveOnlineOnly { }
        Mock Wait-PplidOneDriveOnlineOnly { $true }
        Mock Invoke-PplidOneDriveRetention {
            $script:stagingExistedDuringRetention =
                (Test-Path -LiteralPath $localDump -PathType Leaf) -and
                (Test-Path -LiteralPath $localManifest -PathType Leaf)
            @()
        }

        Complete-PplidPendingCloudBackup -LocalDumpPath $localDump `
            -LocalManifestPath $localManifest -CloudDirectory $cloud | Out-Null

        $script:stagingExistedDuringRetention | Should Be $true
        (Test-Path -LiteralPath $localDump) | Should Be $false
        (Test-Path -LiteralPath $localManifest) | Should Be $false
        (Test-Path -LiteralPath $cloudSet.Dump -PathType Leaf) | Should Be $true
        (Test-Path -LiteralPath $cloudSet.Manifest -PathType Leaf) | Should Be $true
        (Test-Path -LiteralPath $cloudSet.Sha256 -PathType Leaf) | Should Be $true
        Assert-MockCalled Repair-PplidInterruptedOneDrivePublish -Times 1 -Exactly -Scope It
        Assert-MockCalled Invoke-PplidOneDriveRetention -Times 1 -Exactly
    }

    It "keeps rebuilt legacy pending staging when no complete cloud set exists" {
        $cloud = Join-Path $script:testRoot "cloud"
        New-Item -ItemType Directory -Path $cloud | Out-Null
        $localDump = Join-Path $script:testRoot "pplid_main_20260828_020012.dump"
        $localManifest = Join-Path $script:testRoot "pplid_main_20260828_020012.manifest.json"
        [IO.File]::WriteAllText($localDump, "payload")
        [IO.File]::WriteAllText($localManifest, "{}")
        Mock Repair-PplidInterruptedOneDrivePublish { }
        Mock Write-PplidBackupLog { }
        Mock Publish-PplidBackupToOneDrive {
            $set = Get-PplidBackupSetPaths -DumpPath (
                Join-Path $cloud ([IO.Path]::GetFileName($localDump))
            )
            [IO.File]::WriteAllText($set.Dump, "payload")
            [IO.File]::WriteAllText($set.Manifest, "{}")
            [IO.File]::WriteAllText($set.Sha256, "hash")
            [PSCustomObject]@{
                DumpPath = $set.Dump
                ManifestPath = $set.Manifest
                Sha256Path = $set.Sha256
            }
        }
        Mock Request-PplidOneDriveOnlineOnly { }
        Mock Wait-PplidOneDriveOnlineOnly { $true }
        Mock Invoke-PplidOneDriveRetention { @() }

        Complete-PplidPendingCloudBackup -LocalDumpPath $localDump `
            -LocalManifestPath $localManifest -CloudDirectory $cloud -ReplaceCompleteSet | Out-Null

        Assert-MockCalled Repair-PplidInterruptedOneDrivePublish -Times 1 -Exactly -Scope It
    }

    It "uses only backup and deploy mutexes and releases deploy before validation" {
        $source = Get-Content -LiteralPath $scriptPath -Raw
        $pending = $source.IndexOf('$pendingDumps =')
        $backupLock = $source.IndexOf("Global\PPLID-{0}-Backup")
        $deployLock = $source.IndexOf("PPLID-Deploy-{0}")
        $dump = $source.IndexOf('$dumpResult = Invoke-PplidPostgresDump', $deployLock)
        $unlock = $source.IndexOf("Exit-PplidDeployMutex", $dump)
        $validation = $source.IndexOf('$validation = Test-PplidPostgresDump', $unlock)
        $newUpload = $source.IndexOf('$cloudResult = Complete-PplidPendingCloudBackup')
        $rebuildForward = $source.IndexOf('-ReplaceCompleteSet:$pendingResolution.Rebuilt')

        $pending | Should BeGreaterThan -1
        $backupLock | Should BeGreaterThan -1
        $backupLock | Should BeLessThan $pending
        $deployLock | Should BeGreaterThan $pending
        $dump | Should BeGreaterThan $deployLock
        $unlock | Should BeGreaterThan $deployLock
        $validation | Should BeGreaterThan $unlock
        $newUpload | Should BeGreaterThan $unlock
        $rebuildForward | Should BeGreaterThan $pending
        $source | Should Not Match 'PPLID-GitHub-Sync'
        $source | Should Not Match 'Exit-PplidOperationalMutexes'
    }

    It "defaults to rclone and exposes Desktop only as an explicit legacy transport" {
        $source = Get-Content -LiteralPath $scriptPath -Raw

        $source | Should Match '\[ValidateSet\("Rclone", "OneDriveDesktopLegacy"\)\]'
        $source | Should Match '\[string\]\$Transport = "Rclone"'
        $source | Should Match 'elseif \(\$Transport -eq "OneDriveDesktopLegacy"\)'
        $source | Should Not Match 'OneDriveDesktop"'
        $source | Should Match 'lib\\backup_resources\.ps1'
        $source | Should Match 'lib\\backup_rclone\.ps1'
    }

    It "orders configuration resource state and quota preflights before pg_dump" {
        $source = Get-Content -LiteralPath $scriptPath -Raw
        $operational = $source.IndexOf('$exitCode = 0')
        $backupLock = $source.IndexOf('Global\PPLID-{0}-Backup', $operational)
        $rcloneStatic = $source.IndexOf('$rclonePreflight = Test-PplidRcloneStaticPreflight', $operational)
        $pendingGuard = $source.IndexOf('if ($pendingDumps.Count -gt 0)', $operational)
        $quota = $source.IndexOf('$rcloneQuotaPreflight = Get-PplidRcloneQuota', $pendingGuard)
        $postgresPreflight = $source.IndexOf('$preflight = Test-PplidPostgresBackupPreflight', $quota)
        $capacity = $source.IndexOf('$capacity = Test-PplidBackupCapacity', $postgresPreflight)
        $dump = $source.IndexOf('$dumpResult = Invoke-PplidPostgresDump', $capacity)

        $backupLock | Should BeGreaterThan $operational
        $rcloneStatic | Should BeGreaterThan $backupLock
        $pendingGuard | Should BeGreaterThan $rcloneStatic
        $quota | Should BeGreaterThan $pendingGuard
        $postgresPreflight | Should BeGreaterThan $quota
        $capacity | Should BeGreaterThan $postgresPreflight
        $dump | Should BeGreaterThan $capacity
    }

    It "blocks a new dump whenever staging had a pending dump" {
        $source = Get-Content -LiteralPath $scriptPath -Raw
        $pendingList = $source.IndexOf('$pendingDumps =')
        $pendingGuard = $source.IndexOf('if ($pendingDumps.Count -gt 0)', $pendingList)
        $pendingExit = $source.IndexOf('exit 0', $pendingGuard)
        $newTimestamp = $source.IndexOf('$timestamp = Get-Date', $pendingGuard)
        $dump = $source.IndexOf('$dumpResult = Invoke-PplidPostgresDump', $pendingGuard)

        $pendingGuard | Should BeGreaterThan $pendingList
        $pendingExit | Should BeGreaterThan $pendingGuard
        $newTimestamp | Should BeGreaterThan $pendingExit
        $dump | Should BeGreaterThan $newTimestamp
    }

    It "creates local SHA uses a pg_dump observer and commits local files with Move-Item" {
        $source = Get-Content -LiteralPath $scriptPath -Raw
        $sha = $source.IndexOf('$writtenHash = Write-PplidBackupSha256File')
        $complete = $source.IndexOf('$transportResult = Complete-PplidRcloneBackup', $sha)
        $observer = $source.IndexOf('-Observer $resourceObserver -ObserverIntervalSeconds $WatchdogIntervalSeconds')
        $moveDefinition = $scriptAst.Find({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq 'Move-PplidBackupSetToConfirmed'
        }, $true).Extent.Text
        $rcloneDefinition = $scriptAst.Find({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq 'Complete-PplidRcloneBackup'
        }, $true).Extent.Text

        $sha | Should BeGreaterThan -1
        $complete | Should BeGreaterThan $sha
        $observer | Should BeGreaterThan -1
        $moveDefinition | Should Match 'Move-Item'
        $moveDefinition | Should Not Match 'Remove-Item'
        $rcloneDefinition | Should Match 'Move-PplidBackupSetToConfirmed'
        $rcloneDefinition | Should Not Match 'Remove-Item'
        $rcloneDefinition | Should Match 'Get-PplidRcloneQuota'
        $rcloneDefinition | Should Match 'Publish-PplidBackupWithRclone'
        $rcloneDefinition | Should Match 'Get-PplidRcloneRetentionPlan'
    }

    It "publishes with mocked rclone keeps retention dry-run and moves the local trio to confirmed" {
        $base = 'pplid_main_20260828_030000'
        $dump = Join-Path $script:testRoot ($base + '.dump')
        $manifest = Join-Path $script:testRoot ($base + '.manifest.json')
        $sha = Join-Path $script:testRoot ($base + '.sha256')
        $confirmedDirectory = Join-Path $script:testRoot 'confirmed'
        [IO.File]::WriteAllText($dump, 'dump')
        [IO.File]::WriteAllText($manifest, '{}')
        [IO.File]::WriteAllText($sha, ('a' * 64))
        $configuration = [PSCustomObject]@{ Remote = 'corp:'; ConfigPath = 'mock'; BinaryPath = 'mock.exe' }
        Mock Get-PplidRcloneQuota {
            [PSCustomObject]@{ Success = $true; Classification = 'success'; FreeBytes = 20GB }
        }
        Mock Publish-PplidBackupWithRclone {
            [PSCustomObject]@{
                Success = $true
                Classification = 'success'
                AlreadyPublished = $false
                OrphanIncoming = @()
                FinalRoot = 'corp:PPLID/Backups/MAIN'
                DumpRemotePath = ('corp:PPLID/Backups/MAIN/' + [IO.Path]::GetFileName($dump))
            }
        }
        Mock Get-PplidRcloneDirectoryInventory {
            [PSCustomObject]@{
                Success = $true
                Items = @(
                    [PSCustomObject]@{ Name = [IO.Path]::GetFileName($dump) },
                    [PSCustomObject]@{ Name = [IO.Path]::GetFileName($manifest) },
                    [PSCustomObject]@{ Name = [IO.Path]::GetFileName($sha) }
                )
            }
        }
        Mock Get-PplidRcloneRetentionPlan {
            [PSCustomObject]@{ DryRun = $true; MutationCommands = @(); Keep = @(); WouldRetire = @() }
        }

        $result = Complete-PplidRcloneBackup -LocalDumpPath $dump -LocalManifestPath $manifest `
            -LocalSha256Path $sha -Configuration $configuration -EstimatedDumpBytes 1GB `
            -ConfirmedDirectory $confirmedDirectory

        $result.Success | Should Be $true
        $result.AlreadyPublished | Should Be $false
        @($result.OrphanIncoming).Count | Should Be 0
        $result.RetentionPlan.DryRun | Should Be $true
        @($result.RetentionPlan.MutationCommands).Count | Should Be 0
        (Test-Path -LiteralPath $dump) | Should Be $false
        (Test-Path -LiteralPath $manifest) | Should Be $false
        (Test-Path -LiteralPath $sha) | Should Be $false
        (Test-Path -LiteralPath $result.Confirmed.Dump -PathType Leaf) | Should Be $true
        (Test-Path -LiteralPath $result.Confirmed.Manifest -PathType Leaf) | Should Be $true
        (Test-Path -LiteralPath $result.Confirmed.Sha256 -PathType Leaf) | Should Be $true
        Assert-MockCalled Get-PplidRcloneQuota -Times 1 -Exactly
        Assert-MockCalled Publish-PplidBackupWithRclone -Times 1 -Exactly
        Assert-MockCalled Get-PplidRcloneRetentionPlan -Times 1 -Exactly
    }
}
