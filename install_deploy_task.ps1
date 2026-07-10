<#
.SYNOPSIS
    Registra task PPLID-Deploy-OnLogon (deploy_all apos login/reboot).

.DESCRIPTION
    Sem Admin: use -SkipSystemAccount ou -ExportXml e importe ops/tasks/PPLID-Deploy-OnLogon.xml na GUI.
#>
param(
    [int]$DelayMinutes = 2,
    [switch]$Uninstall,
    [switch]$SkipSystemAccount,
    [switch]$ExportXml
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\task_gui.ps1")

$TaskName = "PPLID-Deploy-OnLogon"
$TemplateFile = "PPLID-Deploy-OnLogon.xml"
$DeployScript = Join-Path $PSScriptRoot "deploy_all.ps1"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

function Test-IsAdministrator {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Remove-PplidDeployTask {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    cmd /c "schtasks /Delete /TN `"$TaskName`" /F" 2>$null | Out-Null
}

if ($ExportXml) {
    Show-PplidTaskXmlExport -TemplateFileName $TemplateFile -TaskLabel $TaskName
    exit 0
}

if ($Uninstall) {
    Remove-PplidDeployTask
    Write-Host "Task '$TaskName' removida (se existia)."
    exit 0
}

if (-not (Test-Path $DeployScript)) {
    throw "Script nao encontrado: $DeployScript"
}

$useSystem = (-not $SkipSystemAccount) -and (Test-IsAdministrator)
$taskAction = "$PowerShell -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$DeployScript`""
$delayStr = "{0:D4}:{1:D2}" -f $DelayMinutes, 0

if ($useSystem) {
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $trigger.Delay = (New-TimeSpan -Minutes $DelayMinutes).ToString("c")

    $action = New-ScheduledTaskAction -Execute $PowerShell -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$DeployScript`""
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
        Write-Host "Task '$TaskName' registrada (SYSTEM, delay ${DelayMinutes} min apos logon)."
        exit 0
    } catch {
        Write-Warning "Register-ScheduledTask falhou: $($_.Exception.Message)"
    }
} else {
    $schtasksResult = cmd /c "schtasks /Create /TN `"$TaskName`" /TR `"$taskAction`" /SC ONLOGON /DELAY $delayStr /F" 2>&1
    Write-Host $schtasksResult
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Task '$TaskName' registrada para o usuario atual ($env:USERNAME), delay ${DelayMinutes} min."
        exit 0
    }
}

Write-PplidTaskGuiFallback -TemplateFileName $TemplateFile
exit 1
