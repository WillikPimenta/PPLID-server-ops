$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$opsRoot = Split-Path -Parent $here
. (Join-Path $opsRoot 'lib\backup_security.ps1')

function New-TestAcl {
    param([string]$Identity = 'SYSTEM', [string]$Rights = 'ReadAndExecute')
    [PSCustomObject]@{ Access = @([PSCustomObject]@{
        IdentityReference = $Identity
        AccessControlType = 'Allow'
        FileSystemRights = [Security.AccessControl.FileSystemRights]::$Rights
    }) }
}

function New-ActivationFixture {
    param([switch]$PlainToken)
    $root = Join-Path $TestDrive ([Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $binary = Join-Path $root 'rclone.exe'
    $config = Join-Path $root 'rclone.conf'
    $marker = Join-Path $root 'backup-approval.json'
    [IO.File]::WriteAllText($binary, 'approved rclone fixture')
    if ($PlainToken) { [IO.File]::WriteAllText($config, "[remote]`ntoken = {secret}") }
    else { [IO.File]::WriteAllText($config, "RCLONE_ENCRYPT_V0:`nfixture") }
    $hash = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash
    [PSCustomObject]@{
        Root = $root; Binary = $binary; Config = $config; Marker = $marker; Hash = $hash
    }
}

function Write-TestMarker {
    param($Fixture, [hashtable]$Override = @{})
    $value = [ordered]@{
        schemaVersion = 1; approved = $true; tenant = 'EXPERIAN SERVICES CORP'
        remote = 'pplid-onedrive:'; rcloneSha256 = $Fixture.Hash
        approvedBy = 'security-team'; approvalReference = 'CHG-1234'
        configProtection = [ordered]@{
            approved = $true; mechanism = 'rclone-config-encrypted-dpapi-password-command'
        }
    }
    foreach ($key in $Override.Keys) { $value[$key] = $Override[$key] }
    $value | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Fixture.Marker -Encoding UTF8
}

Describe 'backup activation security gate' {
    It 'allows broad ReadAndExecute and Synchronize on a normal path' {
        $normal = Join-Path $TestDrive 'normal'
        $acl = Test-PplidBackupAclReadOnly -Path $normal -AclProvider {
            New-TestAcl -Identity 'BUILTIN\Users' -Rights 'ReadAndExecute'
        }
        $acl.Passed | Should Be $true
    }

    It 'rejects broad Modify on a normal path' {
        $normal = Join-Path $TestDrive 'normal-modify'
        $acl = Test-PplidBackupAclReadOnly -Path $normal -AclProvider {
            New-TestAcl -Identity 'Authenticated Users' -Rights 'Modify'
        }
        $acl.Passed | Should Be $false
        $acl.Findings[0].Reason | Should Be 'broad_identity_has_write_access'
    }

    It 'rejects broad read on a sensitive path' {
        $sensitive = Join-Path $TestDrive 'backend.env'
        $acl = Test-PplidBackupAclReadOnly -Path $sensitive -SensitivePath $sensitive -AclProvider {
            New-TestAcl -Identity 'BUILTIN\Users' -Rights 'ReadAndExecute'
        }
        $acl.Passed | Should Be $false
        $acl.Findings[0].Reason | Should Be 'broad_identity_has_read_access_to_sensitive_path'
    }

    It 'allows SYSTEM Administrators and the task user on sensitive paths' {
        $sensitive = Join-Path $TestDrive 'rclone.conf'
        foreach ($identity in @('SYSTEM', 'BUILTIN\Administrators', 'c92928a')) {
            $acl = Test-PplidBackupAclReadOnly -Path $sensitive -SensitivePath $sensitive -AclProvider {
                param($path)
                New-TestAcl -Identity $identity -Rights 'FullControl'
            }
            $acl.Passed | Should Be $true
        }
    }

    It 'automatically treats config marker and backend.env as sensitive in the gate' {
        $f = New-ActivationFixture
        Write-TestMarker $f
        $backendEnv = Join-Path $f.Root 'backend.env'
        [IO.File]::WriteAllText($backendEnv, 'DB_PASSWORD=fixture')
        $result = Test-PplidBackupActivationGate -RcloneBinaryPath $f.Binary -RcloneConfigPath $f.Config `
            -ApprovalMarkerPath $f.Marker -ExpectedTenant 'EXPERIAN SERVICES CORP' `
            -ExpectedRemote 'pplid-onedrive:' -AclPath @($f.Root, $backendEnv) `
            -AclProvider { param($path) New-TestAcl -Identity 'BUILTIN\Users' -Rights 'ReadAndExecute' } `
            -ValidateOnlyInvoker { 0 }
        $result.Passed | Should Be $false
        @($result.AclAudit.Findings | Where-Object {
            $_.Reason -eq 'broad_identity_has_read_access_to_sensitive_path'
        }).Count | Should Be 3
    }

    It 'passes only with marker, matching hash, protected config, ACL and ValidateOnly zero' {
        $f = New-ActivationFixture
        Write-TestMarker $f
        $result = Test-PplidBackupActivationGate -RcloneBinaryPath $f.Binary -RcloneConfigPath $f.Config `
            -ApprovalMarkerPath $f.Marker -ExpectedTenant 'EXPERIAN SERVICES CORP' `
            -ExpectedRemote 'pplid-onedrive:' -AclPath @($f.Root) `
            -AclProvider { param($path) New-TestAcl } -ValidateOnlyInvoker { 0 }
        $result.Passed | Should Be $true
    }

    It 'rejects missing approval artifacts' {
        $f = New-ActivationFixture
        $result = Test-PplidBackupActivationGate -RcloneBinaryPath $f.Binary -RcloneConfigPath $f.Config `
            -ApprovalMarkerPath $f.Marker -ExpectedTenant 'EXPERIAN SERVICES CORP' `
            -ExpectedRemote 'pplid-onedrive:' -AclPath @($f.Root) -AclProvider { New-TestAcl } -ValidateOnlyInvoker { 0 }
        $result.Passed | Should Be $false
        ($result.Failures -join ',') | Should Match 'required_file_missing'
    }

    It 'rejects a binary hash different from the approved marker' {
        $f = New-ActivationFixture
        Write-TestMarker $f
        [IO.File]::AppendAllText($f.Binary, 'tampered')
        $result = Test-PplidBackupActivationGate -RcloneBinaryPath $f.Binary -RcloneConfigPath $f.Config `
            -ApprovalMarkerPath $f.Marker -ExpectedTenant 'EXPERIAN SERVICES CORP' `
            -ExpectedRemote 'pplid-onedrive:' -AclPath @($f.Root) -AclProvider { New-TestAcl } -ValidateOnlyInvoker { 0 }
        ($result.Failures -contains 'rclone_hash_mismatch') | Should Be $true
    }

    It 'rejects unexpected tenant or remote' {
        $f = New-ActivationFixture
        Write-TestMarker $f @{ tenant = 'other'; remote = 'other:' }
        $result = Test-PplidBackupActivationGate -RcloneBinaryPath $f.Binary -RcloneConfigPath $f.Config `
            -ApprovalMarkerPath $f.Marker -ExpectedTenant 'EXPERIAN SERVICES CORP' `
            -ExpectedRemote 'pplid-onedrive:' -AclPath @($f.Root) -AclProvider { New-TestAcl } -ValidateOnlyInvoker { 0 }
        ($result.Failures -contains 'unexpected_tenant') | Should Be $true
        ($result.Failures -contains 'unexpected_remote') | Should Be $true
    }

    It 'rejects plaintext token in rclone config' {
        $f = New-ActivationFixture -PlainToken
        Write-TestMarker $f
        $result = Test-PplidBackupActivationGate -RcloneBinaryPath $f.Binary -RcloneConfigPath $f.Config `
            -ApprovalMarkerPath $f.Marker -ExpectedTenant 'EXPERIAN SERVICES CORP' `
            -ExpectedRemote 'pplid-onedrive:' -AclPath @($f.Root) -AclProvider { New-TestAcl } -ValidateOnlyInvoker { 0 }
        ($result.Failures -contains 'plaintext_secret_in_rclone_config') | Should Be $true
    }

    It 'rejects broad write ACLs and does not mutate them' {
        $f = New-ActivationFixture
        Write-TestMarker $f
        $before = Get-Content -LiteralPath $f.Config -Raw
        $result = Test-PplidBackupActivationGate -RcloneBinaryPath $f.Binary -RcloneConfigPath $f.Config `
            -ApprovalMarkerPath $f.Marker -ExpectedTenant 'EXPERIAN SERVICES CORP' `
            -ExpectedRemote 'pplid-onedrive:' -AclPath @($f.Root) `
            -AclProvider { New-TestAcl -Identity 'BUILTIN\Users' -Rights 'Modify' } -ValidateOnlyInvoker { 0 }
        ($result.Failures -contains 'acl_audit_failed') | Should Be $true
        (Get-Content -LiteralPath $f.Config -Raw) | Should Be $before
    }

    It 'rejects a nonzero ValidateOnly result' {
        $f = New-ActivationFixture
        Write-TestMarker $f
        $result = Test-PplidBackupActivationGate -RcloneBinaryPath $f.Binary -RcloneConfigPath $f.Config `
            -ApprovalMarkerPath $f.Marker -ExpectedTenant 'EXPERIAN SERVICES CORP' `
            -ExpectedRemote 'pplid-onedrive:' -AclPath @($f.Root) -AclProvider { New-TestAcl } -ValidateOnlyInvoker { 40 }
        ($result.Failures -contains 'validate_only_failed:40') | Should Be $true
    }

    It 'contains no ACL mutation command' {
        $source = Get-Content -LiteralPath (Join-Path $opsRoot 'lib\backup_security.ps1') -Raw
        $source | Should Not Match '(?i)\bSet-Acl\b|\bicacls(?:\.exe)?\b'
    }
}

Describe 'activation task artifacts' {
    It 'keeps the XML task disabled' {
        [xml]$xml = Get-Content -LiteralPath (Join-Path $opsRoot 'tasks\PPLID-Main-Backup.xml') -Raw
        $manager = New-Object Xml.XmlNamespaceManager($xml.NameTable)
        $manager.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
        $xml.SelectSingleNode('/t:Task/t:Settings/t:Enabled', $manager).InnerText | Should Be 'false'
    }

    It 'places the activation gate before task registration' {
        $source = Get-Content -LiteralPath (Join-Path $opsRoot 'install_backup_task.ps1') -Raw
        $source.IndexOf('Test-PplidBackupActivationGate') | Should BeLessThan $source.IndexOf('Register-ScheduledTask')
    }
}
