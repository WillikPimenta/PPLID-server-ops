$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path (Split-Path $here -Parent) "lib\backup_onedrive.ps1")

Describe "backup_onedrive" {
    BeforeEach {
        $script:testRoot = Join-Path ([IO.Path]::GetTempPath()) ("pplid-backup-onedrive-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $script:testRoot -Force | Out-Null
        $env:OneDriveCommercial = $null
        $env:OneDrive = $null
    }

    AfterEach {
        if (Test-Path -LiteralPath $script:testRoot) {
            Remove-Item -LiteralPath $script:testRoot -Recurse -Force
        }
        $env:OneDriveCommercial = $null
        $env:OneDrive = $null
    }

    Context "Resolve-PplidOneDriveRoot" {
        It "prefers OneDriveCommercial and falls back to OneDrive" {
            $commercial = Join-Path $script:testRoot "commercial"
            $personal = Join-Path $script:testRoot "personal"
            New-Item -ItemType Directory -Path $commercial, $personal | Out-Null
            $env:OneDriveCommercial = $commercial
            $env:OneDrive = $personal

            (Resolve-PplidOneDriveRoot) | Should Be ([IO.Path]::GetFullPath($commercial))
            Remove-Item -LiteralPath $commercial -Force
            (Resolve-PplidOneDriveRoot) | Should Be ([IO.Path]::GetFullPath($personal))
        }

        It 'discovers OneDrive directories in the user profile and prefers EXPERIAN or Commercial' {
            $personal = Join-Path $script:testRoot 'OneDrive - Personal'
            $experian = Join-Path $script:testRoot 'OneDrive - EXPERIAN'
            New-Item -ItemType Directory -Path $personal, $experian | Out-Null
            $script:profileOneDriveDirectories = @(
                (Get-Item -LiteralPath $personal),
                (Get-Item -LiteralPath $experian)
            )
            Mock Get-ChildItem { return $script:profileOneDriveDirectories }

            (Resolve-PplidOneDriveRoot) | Should Be ([IO.Path]::GetFullPath($experian))
            Assert-MockCalled Get-ChildItem -Times 1 -Exactly -ParameterFilter { $Filter -eq 'OneDrive*' -and $Directory }
        }

        It "throws when no usable root exists" {
            Mock Get-ChildItem { return @() }
            { Resolve-PplidOneDriveRoot } | Should Throw
        }
    }

    Context "Publish-PplidBackupToOneDrive" {
        It "publishes a complete set and leaves the source untouched" {
            $oneDrive = Join-Path $script:testRoot "OneDrive"
            $source = Join-Path $script:testRoot "pplid_main_20260828_020000.dump"
            $manifest = Join-Path $script:testRoot "manifest.json"
            New-Item -ItemType Directory -Path $oneDrive | Out-Null
            [IO.File]::WriteAllText($source, "database payload")
            [IO.File]::WriteAllText($manifest, '{"database":"pplid_main"}')

            $result = Publish-PplidBackupToOneDrive -SourceDumpPath $source -SourceManifestPath $manifest -Environment MAIN -OneDriveRoot $oneDrive

            (Test-Path -LiteralPath $source -PathType Leaf) | Should Be $true
            (Test-Path -LiteralPath $manifest -PathType Leaf) | Should Be $true
            (Test-Path -LiteralPath $result.DumpPath -PathType Leaf) | Should Be $true
            (Test-Path -LiteralPath $result.ManifestPath -PathType Leaf) | Should Be $true
            (Test-Path -LiteralPath $result.Sha256Path -PathType Leaf) | Should Be $true
            (Get-Content -LiteralPath $result.Sha256Path -Raw) | Should Match $result.Sha256
            @(Get-ChildItem -LiteralPath $result.Directory -Filter "*.uploading" -File).Count | Should Be 0
            (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash | Should Be $result.Sha256
        }

        It "does not overwrite an existing destination set" {
            $oneDrive = Join-Path $script:testRoot "OneDrive"
            $source = Join-Path $script:testRoot "same.dump"
            $manifest = Join-Path $script:testRoot "manifest.json"
            New-Item -ItemType Directory -Path $oneDrive | Out-Null
            [IO.File]::WriteAllText($source, "payload")
            [IO.File]::WriteAllText($manifest, "{}")
            Publish-PplidBackupToOneDrive -SourceDumpPath $source -SourceManifestPath $manifest -Environment MAIN -OneDriveRoot $oneDrive | Out-Null

            { Publish-PplidBackupToOneDrive -SourceDumpPath $source -SourceManifestPath $manifest -Environment MAIN -OneDriveRoot $oneDrive } | Should Throw
            (Test-Path -LiteralPath $source -PathType Leaf) | Should Be $true
        }
    }

    Context "online-only handling" {
        It "requests unpinned online-only attributes for every set file" {
            $first = Join-Path $script:testRoot "first.dump"
            $second = Join-Path $script:testRoot "first.sha256"
            [IO.File]::WriteAllText($first, "a")
            [IO.File]::WriteAllText($second, "b")
            Mock Invoke-PplidAttribOnlineOnly { }

            Request-PplidOneDriveOnlineOnly -Path @($first, $second)

            Assert-MockCalled Invoke-PplidAttribOnlineOnly -Times 2 -Exactly
        }

        It "returns true only when all files are placeholders" {
            $first = Join-Path $script:testRoot "first.dump"
            $second = Join-Path $script:testRoot "first.sha256"
            [IO.File]::WriteAllText($first, "a")
            [IO.File]::WriteAllText($second, "b")
            Mock Get-PplidFileAttributes { return [IO.FileAttributes]::Offline }

            (Test-PplidOneDriveOnlineOnly -Path @($first, $second)) | Should Be $true
            Assert-MockCalled Get-PplidFileAttributes -Times 2 -Exactly
        }

        It "times out without deleting any file" {
            $file = Join-Path $script:testRoot "backup.dump"
            [IO.File]::WriteAllText($file, "payload")
            Mock Get-PplidFileAttributes { return [IO.FileAttributes]::Archive }

            (Wait-PplidOneDriveOnlineOnly -Path $file -TimeoutSeconds 0) | Should Be $false
            (Test-Path -LiteralPath $file -PathType Leaf) | Should Be $true
        }
    }

    Context "Invoke-PplidOneDriveRetention" {
        It "keeps the newest complete sets after online-only confirmation" {
            $backupDirectory = Join-Path $script:testRoot "backups"
            New-Item -ItemType Directory -Path $backupDirectory | Out-Null
            $baseTime = [DateTime]::UtcNow.AddDays(-10)
            $dumpPaths = @()
            foreach ($index in 1..4) {
                $dump = Join-Path $backupDirectory ('pplid_main_2026080{0}_020000.dump' -f $index)
                $set = Get-PplidBackupSetPaths -DumpPath $dump
                $hash = ('a' * 64)
                [IO.File]::WriteAllText($set.Dump, "set $index")
                [IO.File]::WriteAllText($set.Manifest, (@{ fileName = [IO.Path]::GetFileName($dump); environment = 'main'; sha256 = $hash } | ConvertTo-Json -Compress))
                [IO.File]::WriteAllText($set.Sha256, ($hash + '  ' + [IO.Path]::GetFileName($dump)))
                foreach ($path in @($set.Dump, $set.Manifest, $set.Sha256)) {
                    (Get-Item -LiteralPath $path).LastWriteTimeUtc = $baseTime.AddDays($index)
                }
                $dumpPaths += $dump
            }

            $removed = @(Invoke-PplidOneDriveRetention -ConfirmedDumpPath $dumpPaths[3] -OnlineOnlyConfirmed $true -KeepCount 2)

            $removed.Count | Should Be 6
            (Test-Path -LiteralPath $dumpPaths[0]) | Should Be $false
            (Test-Path -LiteralPath $dumpPaths[1]) | Should Be $false
            (Test-Path -LiteralPath $dumpPaths[2]) | Should Be $true
            (Test-Path -LiteralPath $dumpPaths[3]) | Should Be $true
        }

        It "refuses retention without online-only confirmation" {
            $dump = Join-Path $script:testRoot 'pplid_main_20260828_020000.dump'
            $set = Get-PplidBackupSetPaths -DumpPath $dump
            $hash = ('a' * 64)
            [IO.File]::WriteAllText($set.Dump, 'content')
            [IO.File]::WriteAllText($set.Manifest, (@{ fileName = [IO.Path]::GetFileName($dump); environment = 'main'; sha256 = $hash } | ConvertTo-Json -Compress))
            [IO.File]::WriteAllText($set.Sha256, ($hash + '  ' + [IO.Path]::GetFileName($dump)))

            { Invoke-PplidOneDriveRetention -ConfirmedDumpPath $dump -OnlineOnlyConfirmed $false -KeepCount 1 } | Should Throw
            (Test-Path -LiteralPath $dump) | Should Be $true
        }

        It "ignores incomplete sets" {
            $confirmed = Join-Path $script:testRoot 'pplid_main_20260828_020000.dump'
            $confirmedSet = Get-PplidBackupSetPaths -DumpPath $confirmed
            $hash = ('a' * 64)
            [IO.File]::WriteAllText($confirmedSet.Dump, 'content')
            [IO.File]::WriteAllText($confirmedSet.Manifest, (@{ fileName = [IO.Path]::GetFileName($confirmed); environment = 'main'; sha256 = $hash } | ConvertTo-Json -Compress))
            [IO.File]::WriteAllText($confirmedSet.Sha256, ($hash + '  ' + [IO.Path]::GetFileName($confirmed)))
            $orphan = Join-Path $script:testRoot "orphan.dump"
            [IO.File]::WriteAllText($orphan, "orphan")

            Invoke-PplidOneDriveRetention -ConfirmedDumpPath $confirmed -OnlineOnlyConfirmed $true -KeepCount 1 | Out-Null

            (Test-Path -LiteralPath $orphan) | Should Be $true
        }
    }

    Context "Remove-PplidStaleBackupPartials" {
        It "removes only partial files older than the cutoff" {
            $now = [DateTime]::UtcNow
            $oldPartial = Join-Path $script:testRoot "old.dump.partial"
            $oldUploading = Join-Path $script:testRoot "old.dump.uploading"
            $recentPartial = Join-Path $script:testRoot "recent.dump.partial"
            $sourceDump = Join-Path $script:testRoot "source.dump"
            foreach ($path in @($oldPartial, $oldUploading, $recentPartial, $sourceDump)) {
                [IO.File]::WriteAllText($path, "content")
            }
            (Get-Item -LiteralPath $oldPartial).LastWriteTimeUtc = $now.AddHours(-25)
            (Get-Item -LiteralPath $oldUploading).LastWriteTimeUtc = $now.AddHours(-25)
            (Get-Item -LiteralPath $recentPartial).LastWriteTimeUtc = $now.AddHours(-23)
            (Get-Item -LiteralPath $sourceDump).LastWriteTimeUtc = $now.AddDays(-30)

            $removed = @(Remove-PplidStaleBackupPartials -StagingPath $script:testRoot -OlderThanHours 24 -NowUtc $now)

            $removed.Count | Should Be 2
            (Test-Path -LiteralPath $oldPartial) | Should Be $false
            (Test-Path -LiteralPath $oldUploading) | Should Be $false
            (Test-Path -LiteralPath $recentPartial) | Should Be $true
            (Test-Path -LiteralPath $sourceDump) | Should Be $true
        }
    }

    Context 'interrupted publish repair and strict retention' {
        function New-StrictBackupSet {
            param(
                [string]$Directory,
                [string]$Name,
                [string]$Environment = 'main',
                [string]$ManifestHash,
                [string]$LineHash
            )
            if (-not $ManifestHash) { $ManifestHash = ('a' * 64) }
            if (-not $LineHash) { $LineHash = $ManifestHash }
            $set = Get-PplidBackupSetPaths -DumpPath (Join-Path $Directory $Name)
            [IO.File]::WriteAllText($set.Dump, 'payload')
            [IO.File]::WriteAllText($set.Manifest, (@{ fileName = $Name; environment = $Environment; sha256 = $ManifestHash } | ConvertTo-Json -Compress))
            [IO.File]::WriteAllText($set.Sha256, ($LineHash + '  ' + $Name))
            return $set
        }

        It 'repair preserves a complete trio and removes exact uploading files' {
            $cloud = Join-Path $script:testRoot 'cloud'
            New-Item -ItemType Directory -Path $cloud | Out-Null
            $local = Join-Path $script:testRoot 'pplid_main_20260828_020000.dump'
            [IO.File]::WriteAllText($local, 'local')
            $set = New-StrictBackupSet -Directory $cloud -Name ([IO.Path]::GetFileName($local))
            $uploading = @($set.Dump, $set.Manifest, $set.Sha256) | ForEach-Object { $_ + '.uploading' }
            foreach ($path in $uploading) { [IO.File]::WriteAllText($path, 'partial') }

            $repair = Repair-PplidInterruptedOneDrivePublish -LocalDumpPath $local -CloudDirectory $cloud

            $repair.CompleteSetPreserved | Should Be $true
            $repair.ReplacedCompleteSet | Should Be $false
            foreach ($path in @($set.Dump, $set.Manifest, $set.Sha256, $local)) {
                (Test-Path -LiteralPath $path -PathType Leaf) | Should Be $true
            }
            foreach ($path in $uploading) {
                (Test-Path -LiteralPath $path) | Should Be $false
            }
        }

        It 'repair replaces an exact complete trio only with ReplaceCompleteSet' {
            $cloud = Join-Path $script:testRoot 'cloud'
            New-Item -ItemType Directory -Path $cloud | Out-Null
            $local = Join-Path $script:testRoot 'pplid_main_20260828_020000.dump'
            [IO.File]::WriteAllText($local, 'new local payload')
            $set = New-StrictBackupSet -Directory $cloud -Name ([IO.Path]::GetFileName($local))
            $uploading = @($set.Dump, $set.Manifest, $set.Sha256) | ForEach-Object { $_ + '.uploading' }
            foreach ($path in $uploading) { [IO.File]::WriteAllText($path, 'partial') }

            $repair = Repair-PplidInterruptedOneDrivePublish -LocalDumpPath $local `
                -CloudDirectory $cloud -ReplaceCompleteSet

            $repair.CompleteSetPreserved | Should Be $false
            $repair.ReplacedCompleteSet | Should Be $true
            foreach ($path in @($set.Dump, $set.Manifest, $set.Sha256) + $uploading) {
                (Test-Path -LiteralPath $path) | Should Be $false
            }
            (Test-Path -LiteralPath $local -PathType Leaf) | Should Be $true
        }

        It 'repair removes matching partial finals and uploading without touching local' {
            $cloud = Join-Path $script:testRoot 'cloud'
            New-Item -ItemType Directory -Path $cloud | Out-Null
            $local = Join-Path $script:testRoot 'pplid_main_20260828_020000.dump'
            [IO.File]::WriteAllText($local, 'local')
            $set = Get-PplidBackupSetPaths -DumpPath (Join-Path $cloud ([IO.Path]::GetFileName($local)))
            [IO.File]::WriteAllText($set.Manifest, 'partial final')
            [IO.File]::WriteAllText(($set.Dump + '.uploading'), 'partial upload')

            Repair-PplidInterruptedOneDrivePublish -LocalDumpPath $local -CloudDirectory $cloud | Out-Null

            foreach ($path in @($set.Dump, $set.Manifest, $set.Sha256, ($set.Dump + '.uploading'))) {
                (Test-Path -LiteralPath $path) | Should Be $false
            }
            (Test-Path -LiteralPath $local -PathType Leaf) | Should Be $true
        }

        It 'retention protects confirmed even when a future backup exists' {
            $confirmed = New-StrictBackupSet -Directory $script:testRoot -Name 'pplid_main_20260828_020000.dump'
            $future = New-StrictBackupSet -Directory $script:testRoot -Name 'pplid_main_20991231_235959.dump'

            Invoke-PplidOneDriveRetention -ConfirmedDumpPath $confirmed.Dump -OnlineOnlyConfirmed $true -KeepCount 1 | Out-Null

            (Test-Path -LiteralPath $confirmed.Dump -PathType Leaf) | Should Be $true
            (Test-Path -LiteralPath $future.Dump) | Should Be $false
        }

        It 'retention ignores manual and incoherent sets' {
            $confirmed = New-StrictBackupSet -Directory $script:testRoot -Name 'pplid_main_20260828_020000.dump'
            $oldValid = New-StrictBackupSet -Directory $script:testRoot -Name 'pplid_main_20260827_020000.dump'
            $manual = New-StrictBackupSet -Directory $script:testRoot -Name 'manual_20260826_020000.dump'
            $incoherent = New-StrictBackupSet -Directory $script:testRoot -Name 'pplid_main_20260826_020000.dump' -ManifestHash ('b' * 64) -LineHash ('c' * 64)

            Invoke-PplidOneDriveRetention -ConfirmedDumpPath $confirmed.Dump -OnlineOnlyConfirmed $true -KeepCount 1 | Out-Null

            (Test-Path -LiteralPath $oldValid.Dump) | Should Be $false
            (Test-Path -LiteralPath $manual.Dump -PathType Leaf) | Should Be $true
            (Test-Path -LiteralPath $incoherent.Dump -PathType Leaf) | Should Be $true
        }

        It 'retention rejects an incoherent confirmed set' {
            $confirmed = New-StrictBackupSet -Directory $script:testRoot -Name 'pplid_main_20260828_020000.dump' -ManifestHash ('b' * 64) -LineHash ('c' * 64)

            { Invoke-PplidOneDriveRetention -ConfirmedDumpPath $confirmed.Dump -OnlineOnlyConfirmed $true -KeepCount 1 } | Should Throw
            (Test-Path -LiteralPath $confirmed.Dump -PathType Leaf) | Should Be $true
        }
    }
}
