<#
.SYNOPSIS
    Registra task PPLID-Main-Backup (backup diario do MAIN para o OneDrive).

.DESCRIPTION
    Registra a task para a sessao interativa de c92928a, diariamente as 02:00.
    Por padrao, registra a task desabilitada. Use -Enable somente depois de
    concluir os gates corporativos de Entra/rclone e restauracao.
    Sem Admin: use -ExportXml e importe ops/tasks/PPLID-Main-Backup.xml na GUI.
#>
param(
    [switch]$Uninstall,
    [switch]$ExportXml,
    [switch]$Enable,
    [switch]$ValidateActivation
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\task_gui.ps1")
. (Join-Path $PSScriptRoot "lib\backup_security.ps1")

$TaskName = "PPLID-Main-Backup"
$TemplateFile = "PPLID-Main-Backup.xml"
$BackupScript = Join-Path $PSScriptRoot "backup_main.ps1"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$ExpectedUser = "c92928a"
$RcloneBinaryPath = "C:\pplid\tools\rclone\rclone.exe"
$RcloneConfigPath = "C:\pplid\config\rclone.conf"
$RcloneRemote = "pplid-onedrive:"
$ApprovalMarkerPath = "C:\pplid\config\backup-approval.json"
$ExpectedTenant = "EXPERIAN SERVICES CORP"

function Assert-PplidBackupActivationReady {
    $aclPaths = @(
        $PSScriptRoot,
        $BackupScript,
        (Join-Path $PSScriptRoot 'install_backup_task.ps1'),
        (Join-Path $PSScriptRoot 'lib\backup_security.ps1'),
        (Join-Path $PSScriptRoot 'lib\backup_rclone.ps1'),
        (Join-Path $PSScriptRoot 'lib\backup_postgres.ps1'),
        (Join-Path $PSScriptRoot 'lib\backup_resources.ps1'),
        (Split-Path -Parent $RcloneConfigPath),
        $RcloneBinaryPath,
        $RcloneConfigPath,
        $ApprovalMarkerPath,
        'C:\pplid\deploy\MAIN\shared\backend.env',
        'C:\pplid\backups\staging',
        'C:\pplid\logs'
    )
    $validateOnly = {
        & $PowerShell -NoProfile -ExecutionPolicy Bypass -File $BackupScript `
            -Transport Rclone -RcloneBinaryPath $RcloneBinaryPath `
            -RcloneConfigPath $RcloneConfigPath -RcloneRemote $RcloneRemote -ValidateOnly
        return $LASTEXITCODE
    }
    $gate = Test-PplidBackupActivationGate -RcloneBinaryPath $RcloneBinaryPath `
        -RcloneConfigPath $RcloneConfigPath -ApprovalMarkerPath $ApprovalMarkerPath `
        -ExpectedTenant $ExpectedTenant -ExpectedRemote $RcloneRemote -AclPath $aclPaths `
        -ValidateOnlyInvoker $validateOnly
    if (-not $gate.Passed) {
        foreach ($failure in $gate.Failures) { Write-Error "Activation gate: $failure" }
        if ($null -ne $gate.AclAudit) {
            foreach ($finding in $gate.AclAudit.Findings) {
                Write-Error ("ACL gate: {0} | {1} | {2} | {3}" -f `
                    $finding.Path, $finding.Identity, $finding.Rights, $finding.Reason)
            }
        }
        throw 'Ativacao recusada: um ou mais gates tecnicos nao foram aprovados.'
    }
    Write-Host 'Gates tecnicos de ativacao aprovados.'
    return $gate
}

function Remove-PplidBackupTask {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    cmd /c "schtasks /Delete /TN `"$TaskName`" /F" 2>$null | Out-Null
}

if ($Enable -or $ValidateActivation) {
    Assert-PplidBackupActivationReady | Out-Null
    if ($ValidateActivation -and -not $Enable) {
        Write-Host 'Validacao de ativacao concluida sem registrar ou alterar a task.'
        exit 0
    }
}

if ($ExportXml) {
    Show-PplidTaskXmlExport -TemplateFileName $TemplateFile -TaskLabel $TaskName
    exit 0
}

if ($Uninstall) {
    Remove-PplidBackupTask
    Write-Host "Task '$TaskName' removida (se existia)."
    exit 0
}

if (-not (Test-Path -LiteralPath $BackupScript)) {
    throw "Script nao encontrado: $BackupScript"
}

if ($env:USERNAME -ine $ExpectedUser) {
    throw "Execute este instalador na sessao interativa de '$ExpectedUser'. Usuario atual: '$env:USERNAME'."
}

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BackupScript`" -Transport Rclone -RcloneBinaryPath `"$RcloneBinaryPath`" -RcloneConfigPath `"$RcloneConfigPath`" -RcloneRemote `"$RcloneRemote`"" `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At "02:00"
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -RestartCount 4 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -Disable
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Backup diario do banco MAIN para o OneDrive." `
        -Force `
        -ErrorAction Stop | Out-Null

    if ($Enable) {
        Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
        Write-Host "Task '$TaskName' registrada e habilitada explicitamente para $currentUser."
    } else {
        Write-Host "Task '$TaskName' registrada DESABILITADA para $currentUser."
        Write-Host "Conclua os gates corporativos e execute novamente com -Enable para habilitar."
    }
    exit 0
} catch {
    Write-Warning "Register-ScheduledTask falhou: $($_.Exception.Message)"
}

Write-PplidTaskGuiFallback -TemplateFileName $TemplateFile
exit 1
