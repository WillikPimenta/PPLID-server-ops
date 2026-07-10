<#
.SYNOPSIS
    Registra task PPLID-GitHub-Sync (git pull + deploy automatico).

.DESCRIPTION
    Modo recomendado sem admin: -SkipSystemAccount ou -ExportXml + importar ops/tasks/PPLID-GitHub-Sync.xml na GUI.
    Usa MultipleInstances IgnoreNew (mutex em update_all.ps1 como backup).
#>
param(
    [int]$IntervalMinutes = 2,
    [switch]$Uninstall,
    [switch]$SkipSystemAccount,
    [switch]$ExportXml
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\paths.ps1")
. (Join-Path $PSScriptRoot "lib\task_gui.ps1")

$TaskName = "PPLID-GitHub-Sync"
$TemplateFile = "PPLID-GitHub-Sync.xml"
$UpdateScript = Join-Path $PSScriptRoot "update_all.ps1"
$VbsScript = Join-Path $PSScriptRoot "run_update_hidden.vbs"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$Wscript = "$env:SystemRoot\System32\wscript.exe"

function Test-IsAdministrator {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Remove-PplidSyncTask {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    cmd /c "schtasks /Delete /TN `"$TaskName`" /F" 2>$null | Out-Null
}

function Register-PplidSyncTaskSchtasks {
    param(
        [bool]$UseSystem
    )

    $taskAction = "`"$Wscript`" //B //Nologo `"$VbsScript`""
    if ($UseSystem) {
        return cmd /c "schtasks /Create /TN `"$TaskName`" /TR `"$taskAction`" /SC MINUTE /MO $IntervalMinutes /RU SYSTEM /RP /RL HIGHEST /F" 2>&1
    }

    return cmd /c "schtasks /Create /TN `"$TaskName`" /TR `"$taskAction`" /SC MINUTE /MO $IntervalMinutes /F" 2>&1
}

if ($ExportXml) {
    Show-PplidTaskXmlExport -TemplateFileName $TemplateFile -TaskLabel $TaskName
    exit 0
}

if ($Uninstall) {
    Remove-PplidSyncTask
    Write-Host "Task '$TaskName' removida (se existia)."
    exit 0
}

if (-not (Test-Path $UpdateScript)) {
    throw "Script nao encontrado: $UpdateScript"
}

$useSystem = (-not $SkipSystemAccount) -and (Test-IsAdministrator)
if (-not $useSystem -and -not $SkipSystemAccount) {
    Write-Warning "Execute como Administrador para registrar a task como SYSTEM."
    Write-Warning "Registrando para o usuario atual ($env:USERNAME)..."
}

Remove-PplidSyncTask

$arguments = "//B //Nologo `"$VbsScript`""
$action = New-ScheduledTaskAction -Execute $Wscript -Argument $arguments -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

$registered = $false
try {
    if ($useSystem) {
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
    }

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        $registered = $true
        Write-Host "Task '$TaskName' registrada via Register-ScheduledTask (intervalo: $IntervalMinutes min, IgnoreNew)."
    }
} catch {
    Write-Warning "Register-ScheduledTask falhou: $($_.Exception.Message)"
    Write-Warning "Tentando fallback schtasks..."
}

if (-not $registered) {
    $result = Register-PplidSyncTaskSchtasks -UseSystem $useSystem
    Write-Host $result
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Task '$TaskName' registrada via schtasks (intervalo: $IntervalMinutes min)."
        Write-Host "Acao: $PowerShell $arguments"
        exit 0
    }
}

Write-PplidTaskGuiFallback -TemplateFileName $TemplateFile
exit 1
