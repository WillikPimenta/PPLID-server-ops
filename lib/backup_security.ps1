Set-StrictMode -Version 2.0

function Get-PplidBackupBroadIdentityNames {
    @(
        'Everyone', 'Todos',
        'Authenticated Users', 'Usuarios autenticados', 'Usuários autenticados',
        'BUILTIN\Users', 'BUILTIN\Usuarios', 'BUILTIN\Usuários',
        'Users', 'Usuarios', 'Usuários'
    )
}

function Test-PplidBackupAclReadOnly {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string[]]$Path,
        [string[]]$SensitivePath = @(),
        [scriptblock]$AclProvider
    )

    $broadSids = @('S-1-1-0', 'S-1-5-11', 'S-1-5-32-545')
    $broadNames = @(Get-PplidBackupBroadIdentityNames | ForEach-Object { $_.ToLowerInvariant() })
    # Composite rights such as Write, Modify and FullControl include Synchronize,
    # which is also present in ReadAndExecute. Check only granular mutation bits.
    $mutationMask = [int64]([Security.AccessControl.FileSystemRights]::WriteData -bor
        [Security.AccessControl.FileSystemRights]::CreateFiles -bor
        [Security.AccessControl.FileSystemRights]::AppendData -bor
        [Security.AccessControl.FileSystemRights]::CreateDirectories -bor
        [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
        [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership)
    $readMask = [int64]([Security.AccessControl.FileSystemRights]::ReadData -bor
        [Security.AccessControl.FileSystemRights]::ListDirectory -bor
        [Security.AccessControl.FileSystemRights]::ReadExtendedAttributes -bor
        [Security.AccessControl.FileSystemRights]::ReadAttributes -bor
        [Security.AccessControl.FileSystemRights]::ReadPermissions -bor
        [Security.AccessControl.FileSystemRights]::ExecuteFile -bor
        [Security.AccessControl.FileSystemRights]::Traverse)
    $sensitivePaths = @{}
    foreach ($sensitive in @($SensitivePath)) {
        if (-not [string]::IsNullOrWhiteSpace($sensitive)) {
            $sensitivePaths[[IO.Path]::GetFullPath($sensitive).TrimEnd('\')] = $true
        }
    }
    $findings = New-Object System.Collections.Generic.List[object]

    foreach ($itemPath in @($Path | Select-Object -Unique)) {
        $fullPath = [IO.Path]::GetFullPath($itemPath)
        $isSensitivePath = $sensitivePaths.ContainsKey($fullPath.TrimEnd('\'))
        try {
            if ($null -eq $AclProvider) {
                if (-not (Test-Path -LiteralPath $fullPath)) { throw 'path not found' }
                $acl = Get-Acl -LiteralPath $fullPath -ErrorAction Stop
            } else {
                $acl = & $AclProvider $fullPath
                if ($null -eq $acl) { throw 'ACL provider returned no result' }
            }
        } catch {
            $findings.Add([PSCustomObject]@{
                Path = $fullPath; Identity = $null; Rights = $null; Reason = 'acl_unreadable'
            })
            continue
        }

        foreach ($ace in @($acl.Access)) {
            if ([string]$ace.AccessControlType -ine 'Allow') { continue }
            $identity = [string]$ace.IdentityReference
            $sid = $null
            try {
                if ($ace.IdentityReference -is [Security.Principal.SecurityIdentifier]) {
                    $sid = $identity
                } else {
                    $sid = [string]$ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier])
                }
            } catch { }
            $isBroad = (($null -ne $sid -and $broadSids -contains $sid) -or
                $broadNames -contains $identity.ToLowerInvariant())
            $rightsValue = [int64]$ace.FileSystemRights
            $reason = $null
            if ($isBroad -and (($rightsValue -band $mutationMask) -ne 0)) {
                $reason = 'broad_identity_has_write_access'
            } elseif ($isBroad -and $isSensitivePath -and (($rightsValue -band $readMask) -ne 0)) {
                $reason = 'broad_identity_has_read_access_to_sensitive_path'
            }
            if ($null -ne $reason) {
                $findings.Add([PSCustomObject]@{
                    Path = $fullPath
                    Identity = $identity
                    Rights = [string]$ace.FileSystemRights
                    Reason = $reason
                })
            }
        }
    }

    [PSCustomObject]@{
        Passed = ($findings.Count -eq 0)
        Findings = $findings.ToArray()
        CheckedPaths = @($Path | Select-Object -Unique)
    }
}

function Test-PplidRcloneConfigProtection {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)]$ApprovalMarker
    )

    try { $raw = [IO.File]::ReadAllText([IO.Path]::GetFullPath($ConfigPath)) }
    catch { return [PSCustomObject]@{ Passed = $false; Reason = 'config_unreadable' } }

    $encrypted = $raw.TrimStart().StartsWith('RCLONE_ENCRYPT_V0:', [StringComparison]::Ordinal)
    $containsPlainSecret = (-not $encrypted -and $raw -match '(?im)^\s*(token|access_token|refresh_token|client_secret|password)\s*=')
    $protection = $ApprovalMarker.PSObject.Properties['configProtection']
    $approvedMechanisms = @(
        'rclone-config-encrypted-dpapi-password-command',
        'credential-manager-password-command',
        'corporate-managed-token-provider'
    )
    if ($null -eq $protection -or $null -eq $protection.Value) {
        return [PSCustomObject]@{ Passed = $false; Reason = 'config_protection_not_approved' }
    }
    $approved = ($protection.Value.approved -eq $true)
    $mechanism = [string]$protection.Value.mechanism
    if (-not $approved -or $approvedMechanisms -notcontains $mechanism) {
        return [PSCustomObject]@{ Passed = $false; Reason = 'config_protection_not_approved' }
    }
    if ($containsPlainSecret) {
        return [PSCustomObject]@{ Passed = $false; Reason = 'plaintext_secret_in_rclone_config' }
    }
    [PSCustomObject]@{ Passed = $true; Reason = 'ready'; EncryptedConfig = $encrypted; Mechanism = $mechanism }
}

function Test-PplidBackupActivationGate {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RcloneBinaryPath,
        [Parameter(Mandatory = $true)][string]$RcloneConfigPath,
        [Parameter(Mandatory = $true)][string]$ApprovalMarkerPath,
        [Parameter(Mandatory = $true)][string]$ExpectedTenant,
        [Parameter(Mandatory = $true)][string]$ExpectedRemote,
        [Parameter(Mandatory = $true)][string[]]$AclPath,
        [string]$BackendEnvPath,
        [string[]]$SensitivePath = @(),
        [scriptblock]$AclProvider,
        [scriptblock]$ValidateOnlyInvoker
    )

    $failures = New-Object System.Collections.Generic.List[string]
    foreach ($required in @($RcloneBinaryPath, $RcloneConfigPath, $ApprovalMarkerPath)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            $failures.Add('required_file_missing:' + [IO.Path]::GetFullPath($required))
        }
    }
    if ($failures.Count -gt 0) {
        return [PSCustomObject]@{ Passed = $false; Failures = $failures.ToArray(); Marker = $null; AclAudit = $null }
    }

    try { $marker = [IO.File]::ReadAllText([IO.Path]::GetFullPath($ApprovalMarkerPath)) | ConvertFrom-Json }
    catch {
        $failures.Add('approval_marker_invalid_json')
        return [PSCustomObject]@{ Passed = $false; Failures = $failures.ToArray(); Marker = $null; AclAudit = $null }
    }
    if ($marker.approved -ne $true) { $failures.Add('approval_not_granted') }
    if ([string]$marker.tenant -cne $ExpectedTenant) { $failures.Add('unexpected_tenant') }
    if ([string]$marker.remote -cne $ExpectedRemote) { $failures.Add('unexpected_remote') }
    if ([string]::IsNullOrWhiteSpace([string]$marker.approvedBy) -or
        [string]::IsNullOrWhiteSpace([string]$marker.approvalReference)) {
        $failures.Add('approval_evidence_incomplete')
    }

    $expectedHash = ([string]$marker.rcloneSha256).ToUpperInvariant()
    if ($expectedHash -notmatch '^[A-F0-9]{64}$') {
        $failures.Add('invalid_rclone_sha256_marker')
    } else {
        try { $actualHash = (Get-FileHash -LiteralPath $RcloneBinaryPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToUpperInvariant() }
        catch { $actualHash = ''; $failures.Add('rclone_hash_unreadable') }
        if ($actualHash -and $actualHash -cne $expectedHash) { $failures.Add('rclone_hash_mismatch') }
    }

    $configProtection = Test-PplidRcloneConfigProtection -ConfigPath $RcloneConfigPath -ApprovalMarker $marker
    if (-not $configProtection.Passed) { $failures.Add($configProtection.Reason) }

    $automaticSensitivePaths = @($RcloneConfigPath, $ApprovalMarkerPath)
    if (-not [string]::IsNullOrWhiteSpace($BackendEnvPath)) {
        $automaticSensitivePaths += $BackendEnvPath
    } else {
        $automaticSensitivePaths += @($AclPath | Where-Object {
            [IO.Path]::GetFileName([string]$_) -ieq 'backend.env'
        })
    }
    $allAclPaths = @($AclPath + $automaticSensitivePaths + $SensitivePath | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    } | Select-Object -Unique)
    $allSensitivePaths = @($automaticSensitivePaths + $SensitivePath | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    } | Select-Object -Unique)
    $aclAudit = Test-PplidBackupAclReadOnly -Path $allAclPaths -SensitivePath $allSensitivePaths -AclProvider $AclProvider
    if (-not $aclAudit.Passed) { $failures.Add('acl_audit_failed') }

    try {
        $validateExitCode = if ($null -eq $ValidateOnlyInvoker) { 1 } else { [int](& $ValidateOnlyInvoker) }
    } catch { $validateExitCode = 1 }
    if ($validateExitCode -ne 0) { $failures.Add('validate_only_failed:' + $validateExitCode) }

    [PSCustomObject]@{
        Passed = ($failures.Count -eq 0)
        Failures = $failures.ToArray()
        Marker = $marker
        ConfigProtection = $configProtection
        AclAudit = $aclAudit
        ValidateOnlyExitCode = $validateExitCode
    }
}
