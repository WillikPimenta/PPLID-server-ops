#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [uri]$Url,
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$EnvironmentName = 'MAIN',
    [string]$PythonPath = (Get-Command python.exe -ErrorAction Stop).Source,
    [string]$DataDirectory = (Join-Path $env:ProgramData 'PPLID\Availability'),
    [ValidateRange(15, 3600)]
    [int]$IntervalSec = 30,
    [ValidateRange(1, 14)]
    [int]$TimeoutSec = 5
)

$ErrorActionPreference = 'Stop'
if ($Url.Scheme -notin @('http', 'https') -or $Url.UserInfo -or $Url.Query -or $Url.Fragment -or $Url.AbsoluteUri.Contains('"')) {
    throw 'Use an HTTP(S) readiness URL without credentials, query or fragment.'
}
$taskName = "PPLID-Availability-$EnvironmentName"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "Task $taskName already exists. Review it before changing its configuration."
}
$python = (Resolve-Path -LiteralPath $PythonPath).Path
$directory = [IO.Path]::GetFullPath($DataDirectory)
if ($directory.Contains('"') -or $python.Contains('"')) { throw 'Paths cannot contain double quotes.' }
$probeSource = Join-Path $PSScriptRoot 'independent_availability_probe.py'
if (-not (Test-Path -LiteralPath $probeSource -PathType Leaf)) { throw 'Independent probe script not found.' }
$null = New-Item -ItemType Directory -Path $directory -Force
$probePath = Join-Path $directory 'independent_availability_probe.py'
Copy-Item -LiteralPath $probeSource -Destination $probePath -Force
$evidence = Join-Path $directory 'evidence'
$arguments = '"{0}" --url "{1}" --name "{2}" --output-dir "{3}" --interval {4} --timeout {5}' -f `
    $probePath, $Url.AbsoluteUri, $EnvironmentName, $evidence, $IntervalSec, $TimeoutSec
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $directory
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$null = Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'Independent HTTP availability evidence, separate from Ops Console and its SQLite database.'
Start-ScheduledTask -TaskName $taskName
Write-Output "Task installed: $taskName. Evidence directory: $evidence"
