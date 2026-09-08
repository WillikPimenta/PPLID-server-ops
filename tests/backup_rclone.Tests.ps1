$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path (Split-Path $here -Parent) 'lib\backup_rclone.ps1')

function Copy-PplidSimItem {
    param([Parameter(Mandatory = $true)]$Item, [string]$Name)
    if (-not $Name) { $Name = [string]$Item.Name }
    return [PSCustomObject]@{
        Name = $Name
        Size = [int64]$Item.Size
        Hashes = [PSCustomObject]@{ QuickXorHash = [string]$Item.Hashes.QuickXorHash }
    }
}

function New-PplidRcloneTestSet {
    param([Parameter(Mandatory = $true)][string]$Root)
    $base = 'pplid_main_20260828_020000'
    $files = @(
        [PSCustomObject]@{ Name = $base + '.dump'; Content = 'dump-payload'; Hash = 'quick-dump' },
        [PSCustomObject]@{ Name = $base + '.manifest.json'; Content = '{"ok":true}'; Hash = 'quick-manifest' },
        [PSCustomObject]@{ Name = $base + '.sha256'; Content = 'sha-line'; Hash = 'quick-sha' }
    )
    $items = @()
    foreach ($file in $files) {
        $path = Join-Path $Root $file.Name
        [IO.File]::WriteAllText($path, $file.Content)
        $items += [PSCustomObject]@{
            Name = $file.Name
            LocalPath = [IO.Path]::GetFullPath($path)
            Size = [int64](Get-Item -LiteralPath $path).Length
            QuickXorHash = $file.Hash
            Hashes = [PSCustomObject]@{ QuickXorHash = $file.Hash }
        }
    }
    return [PSCustomObject]@{
        BaseName = $base
        Dump = $items[0].LocalPath
        Manifest = $items[1].LocalPath
        Sha256 = $items[2].LocalPath
        Items = $items
    }
}

function New-PplidRcloneSimulator {
    param([Parameter(Mandatory = $true)]$TestSet)
    $localItems = @{}
    foreach ($item in $TestSet.Items) { $localItems[$item.LocalPath] = $item }
    return [PSCustomObject]@{
        Directories = @{}
        LocalItems = $localItems
        Calls = New-Object System.Collections.Generic.List[object]
        CrashAfterMutation = 0
        MutationCount = 0
    }
}

function Set-PplidSimDirectory {
    param([Parameter(Mandatory = $true)]$Simulator, [Parameter(Mandatory = $true)][string]$Path, [object[]]$Items = @())
    $Simulator.Directories[$Path] = @($Items | ForEach-Object { Copy-PplidSimItem -Item $_ })
}

function Get-PplidSimDirectoryItems {
    param([Parameter(Mandatory = $true)]$Simulator, [Parameter(Mandatory = $true)][string]$Path)
    if (-not $Simulator.Directories.ContainsKey($Path)) { return @() }
    return @($Simulator.Directories[$Path])
}

function Invoke-PplidRcloneSimulator {
    param([Parameter(Mandatory = $true)]$Simulator, [Parameter(Mandatory = $true)][string[]]$Arguments)
    $Simulator.Calls.Add([PSCustomObject]@{ Args = @($Arguments) }) | Out-Null
    $command = $Arguments[0]
    if ($command -eq 'hashsum') {
        $localPath = [IO.Path]::GetFullPath($Arguments[2])
        if (-not $Simulator.LocalItems.ContainsKey($localPath)) {
            return [PSCustomObject]@{ ExitCode = 1; StdOut = ''; StdErr = 'local file not found' }
        }
        $item = $Simulator.LocalItems[$localPath]
        return [PSCustomObject]@{ ExitCode = 0; StdOut = ($item.QuickXorHash + '  ' + $item.Name); StdErr = '' }
    }
    if ($command -eq 'lsjson') {
        $path = $Arguments[1]
        if (-not $Simulator.Directories.ContainsKey($path)) {
            return [PSCustomObject]@{ ExitCode = 3; StdOut = ''; StdErr = 'directory not found' }
        }
        $json = ConvertTo-Json -InputObject @(Get-PplidSimDirectoryItems -Simulator $Simulator -Path $path) -Compress
        return [PSCustomObject]@{ ExitCode = 0; StdOut = $json; StdErr = '' }
    }
    if ($command -eq 'mkdir') {
        if (-not $Simulator.Directories.ContainsKey($Arguments[1])) { $Simulator.Directories[$Arguments[1]] = @() }
        return [PSCustomObject]@{ ExitCode = 0; StdOut = ''; StdErr = '' }
    }
    if ($command -eq 'copyto') {
        $localPath = [IO.Path]::GetFullPath($Arguments[1])
        $destination = $Arguments[2]
        $separator = $destination.LastIndexOf('/')
        $directory = $destination.Substring(0, $separator)
        $name = $destination.Substring($separator + 1)
        if (-not $Simulator.Directories.ContainsKey($directory)) { $Simulator.Directories[$directory] = @() }
        if (@($Simulator.Directories[$directory] | Where-Object { $_.Name -ceq $name }).Count -gt 0) {
            return [PSCustomObject]@{ ExitCode = 1; StdOut = ''; StdErr = 'immutable conflict' }
        }
        $Simulator.Directories[$directory] += Copy-PplidSimItem -Item $Simulator.LocalItems[$localPath] -Name $name
        $Simulator.MutationCount++
        if ($Simulator.CrashAfterMutation -eq $Simulator.MutationCount) {
            return [PSCustomObject]@{ ExitCode = 99; StdOut = ''; StdErr = 'simulated crash after copy' }
        }
        return [PSCustomObject]@{ ExitCode = 0; StdOut = ''; StdErr = '' }
    }
    if ($command -eq 'moveto') {
        $source = $Arguments[1]
        $destination = $Arguments[2]
        $sourceSeparator = $source.LastIndexOf('/')
        $sourceDirectory = $source.Substring(0, $sourceSeparator)
        $sourceName = $source.Substring($sourceSeparator + 1)
        $destinationSeparator = $destination.LastIndexOf('/')
        $destinationDirectory = $destination.Substring(0, $destinationSeparator)
        $destinationName = $destination.Substring($destinationSeparator + 1)
        if (-not $Simulator.Directories.ContainsKey($destinationDirectory)) { $Simulator.Directories[$destinationDirectory] = @() }
        if (@($Simulator.Directories[$destinationDirectory] | Where-Object { $_.Name -ceq $destinationName }).Count -gt 0) {
            return [PSCustomObject]@{ ExitCode = 1; StdOut = ''; StdErr = 'immutable conflict' }
        }
        $sourceItem = @($Simulator.Directories[$sourceDirectory] | Where-Object { $_.Name -ceq $sourceName })
        if ($sourceItem.Count -ne 1) { return [PSCustomObject]@{ ExitCode = 1; StdOut = ''; StdErr = 'source missing' } }
        $Simulator.Directories[$sourceDirectory] = @($Simulator.Directories[$sourceDirectory] | Where-Object { $_.Name -cne $sourceName })
        $Simulator.Directories[$destinationDirectory] += Copy-PplidSimItem -Item $sourceItem[0] -Name $destinationName
        $Simulator.MutationCount++
        if ($Simulator.CrashAfterMutation -eq $Simulator.MutationCount) {
            return [PSCustomObject]@{ ExitCode = 99; StdOut = ''; StdErr = 'simulated crash after move' }
        }
        return [PSCustomObject]@{ ExitCode = 0; StdOut = ''; StdErr = '' }
    }
    return [PSCustomObject]@{ ExitCode = 1; StdOut = ''; StdErr = ('unsupported simulated command: ' + $command) }
}

function Invoke-PplidTestPublish {
    param([Parameter(Mandatory = $true)]$Set, [Parameter(Mandatory = $true)]$Configuration, [Parameter(Mandatory = $true)]$Executor, [string]$RunId = '')
    return Publish-PplidBackupWithRclone -DumpPath $Set.Dump -ManifestPath $Set.Manifest -Sha256Path $Set.Sha256 -Environment MAIN -Configuration $Configuration -RunId $RunId -Executor $Executor
}

Describe 'backup_rclone idempotent transport' {
    BeforeEach {
        $script:testRoot = Join-Path ([IO.Path]::GetTempPath()) ('pplid-rclone-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $script:testRoot | Out-Null
        $binary = Join-Path $script:testRoot 'rclone.exe'
        $config = Join-Path $script:testRoot 'rclone.conf'
        [IO.File]::WriteAllText($binary, 'mock')
        [IO.File]::WriteAllText($config, '[corp]')
        $script:configuration = Resolve-PplidRcloneConfiguration -BinaryPath $binary -ConfigPath $config -Remote 'corp:'
        $script:set = New-PplidRcloneTestSet -Root $script:testRoot
        $script:sim = New-PplidRcloneSimulator -TestSet $script:set
        $script:executor = { param($file,$arguments) Invoke-PplidRcloneSimulator -Simulator $script:sim -Arguments $arguments }
        $script:finalRoot = 'corp:PPLID/Backups/MAIN'
        $script:incomingRoot = $script:finalRoot + '/_incoming/' + $script:set.BaseName
    }
    AfterEach {
        if (Test-Path -LiteralPath $script:testRoot) { Remove-Item -LiteralPath $script:testRoot -Recurse -Force }
    }

    It 'allows deterministic incoming without traversal' {
        (Get-PplidRcloneRemotePath -Remote 'corp:' -Environment MAIN -ChildSegment @('_incoming', $script:set.BaseName)) | Should Be $script:incomingRoot
        { Get-PplidRcloneRemotePath -Remote 'corp:' -Environment MAIN -ChildSegment @('_incoming', '..') } | Should Throw
    }

    It 'creates a missing final directory relists and publishes with hashes and immutable' {
        $result = Invoke-PplidTestPublish -Set $script:set -Configuration $script:configuration -Executor $script:executor

        $result.Success | Should Be $true
        $result.IncomingRoot | Should Be $script:incomingRoot
        @($script:sim.Calls | Where-Object { $_.Args[0] -eq 'mkdir' }).Count | Should Be 1
        @($script:sim.Calls | Where-Object { $_.Args[0] -eq 'hashsum' -and $_.Args[1] -eq 'QuickXorHash' }).Count | Should Be 3
        foreach ($call in @($script:sim.Calls | Where-Object { $_.Args[0] -eq 'lsjson' })) {
            ($call.Args -contains '--hash') | Should Be $true
            ($call.Args -contains 'QuickXorHash') | Should Be $true
        }
        foreach ($call in @($script:sim.Calls | Where-Object { $_.Args[0] -in @('copyto','moveto') })) {
            ($call.Args -contains '--immutable') | Should Be $true
        }
        @($script:sim.Calls | Where-Object { $_.Args[0] -eq 'moveto' })[-1].Args[1] | Should Match '\.dump$'
    }

    It 'returns AlreadyPublished for an equivalent complete final trio without upload' {
        Set-PplidSimDirectory -Simulator $script:sim -Path $script:finalRoot -Items $script:set.Items
        Set-PplidSimDirectory -Simulator $script:sim -Path $script:incomingRoot -Items @(
            [PSCustomObject]@{ Name = 'orphan.tmp'; Size = 7; Hashes = [PSCustomObject]@{ QuickXorHash = 'orphan-hash' } }
        )

        $result = Invoke-PplidTestPublish -Set $script:set -Configuration $script:configuration -Executor $script:executor

        $result.Success | Should Be $true
        $result.AlreadyPublished | Should Be $true
        @($script:sim.Calls | Where-Object { $_.Args[0] -in @('copyto','moveto') }).Count | Should Be 0
        @($result.OrphanIncoming).Count | Should Be 1
        (Get-PplidSimDirectoryItems -Simulator $script:sim -Path $script:incomingRoot)[0].Name | Should Be 'orphan.tmp'
    }

    It 'accepts equivalent partial commits and moves only missing objects with dump last' {
        Set-PplidSimDirectory -Simulator $script:sim -Path $script:finalRoot -Items @($script:set.Items[1])
        Set-PplidSimDirectory -Simulator $script:sim -Path $script:incomingRoot -Items @($script:set.Items[0], $script:set.Items[2])

        $result = Invoke-PplidTestPublish -Set $script:set -Configuration $script:configuration -Executor $script:executor

        $result.Success | Should Be $true
        @($script:sim.Calls | Where-Object { $_.Args[0] -eq 'copyto' }).Count | Should Be 0
        $moves = @($script:sim.Calls | Where-Object { $_.Args[0] -eq 'moveto' })
        $moves.Count | Should Be 2
        $moves[0].Args[1] | Should Match '\.sha256$'
        $moves[1].Args[1] | Should Match '\.dump$'
    }

    It 'returns conflict for equal-size different-hash and different-size objects without overwrite' {
        foreach ($variant in @('hash', 'size')) {
            $script:sim = New-PplidRcloneSimulator -TestSet $script:set
            $conflict = Copy-PplidSimItem -Item $script:set.Items[0]
            if ($variant -eq 'hash') { $conflict.Hashes.QuickXorHash = 'different-hash' } else { $conflict.Size++ }
            Set-PplidSimDirectory -Simulator $script:sim -Path $script:finalRoot -Items @($conflict)

            $result = Invoke-PplidTestPublish -Set $script:set -Configuration $script:configuration -Executor $script:executor

            $result.Success | Should Be $false
            $result.Classification | Should Be 'conflict'
            @($script:sim.Calls | Where-Object { $_.Args[0] -in @('copyto','moveto') }).Count | Should Be 0
        }
    }

    It 'resumes successfully after a simulated crash following every copy or move' {
        foreach ($crashPoint in 1..6) {
            $script:sim = New-PplidRcloneSimulator -TestSet $script:set
            $script:sim.CrashAfterMutation = $crashPoint
            $first = Invoke-PplidTestPublish -Set $script:set -Configuration $script:configuration -Executor $script:executor
            $first.Success | Should Be $false

            $script:sim.CrashAfterMutation = 0
            $second = Invoke-PplidTestPublish -Set $script:set -Configuration $script:configuration -Executor $script:executor
            $second.Success | Should Be $true
            $finalItems = Get-PplidSimDirectoryItems -Simulator $script:sim -Path $script:finalRoot
            (Test-PplidRcloneInventoryFiles -Items $finalItems -ExpectedFiles $script:set.Items) | Should Be $true
        }
    }

    It 'reports orphan incoming objects and never removes them' {
        $orphan = [PSCustomObject]@{ Name = 'manual-orphan.bin'; Size = 9; Hashes = [PSCustomObject]@{ QuickXorHash = 'orphan' } }
        Set-PplidSimDirectory -Simulator $script:sim -Path $script:incomingRoot -Items @($orphan)

        $result = Invoke-PplidTestPublish -Set $script:set -Configuration $script:configuration -Executor $script:executor

        $result.Success | Should Be $true
        @($result.OrphanIncoming | Where-Object { $_.Name -eq 'manual-orphan.bin' }).Count | Should Be 1
        @((Get-PplidSimDirectoryItems -Simulator $script:sim -Path $script:incomingRoot) | Where-Object { $_.Name -eq 'manual-orphan.bin' }).Count | Should Be 1
    }

    It 'redacts secrets and handles high executor stdout and stderr without a real process' {
        $large = ('x' * 1048576)
        $executor = {
            param($file,$arguments)
            [PSCustomObject]@{
                ExitCode = 0
                StdOut = ($large + '{"access_token":"secret-access"}')
                StdErr = ($large + ' Authorization: Bearer secret-bearer.value')
            }
        }
        $result = Invoke-PplidRcloneCommand -Configuration $script:configuration -CommandArguments @('version') -Executor $executor

        $result.StdOut.Length | Should BeGreaterThan 1000000
        $result.StdErr.Length | Should BeGreaterThan 1000000
        $result.StdOut | Should Not Match 'secret-access'
        $result.StdErr | Should Not Match 'secret-bearer'
    }

    It 'keeps retention dry-run and contains no destructive rclone command' {
        $items = @()
        foreach ($day in 1..4) {
            $base = 'pplid_main_2026080' + $day + '_020000'
            $items += [PSCustomObject]@{ Name = $base + '.dump' }, [PSCustomObject]@{ Name = $base + '.manifest.json' }, [PSCustomObject]@{ Name = $base + '.sha256' }
        }
        $plan = Get-PplidRcloneRetentionPlan -Environment MAIN -Inventory $items -RetentionCount 2 -ProtectedDumpName 'pplid_main_20260801_020000.dump'
        $source = Get-Content -LiteralPath (Join-Path (Split-Path $here -Parent) 'lib\backup_rclone.ps1') -Raw

        $plan.DryRun | Should Be $true
        @($plan.MutationCommands).Count | Should Be 0
        $source | Should Not Match "@\('(?:delete|purge|sync)'"
        $source | Should Not Match 'Remove-Item'
        $source | Should Match 'ReadToEndAsync'
        $source | Should Not Match '\.ReadToEnd\(\)'
    }
}
