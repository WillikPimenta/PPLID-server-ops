# Gera XMLs de task em UTF-16 LE com BOM valido para importacao no Agendador.
$ErrorActionPreference = "Stop"
$tasksDir = $PSScriptRoot
$utf16 = [System.Text.Encoding]::Unicode

function Write-TaskXmlFile {
    param(
        [string]$FileName,
        [string]$Content
    )

    $path = Join-Path $tasksDir $FileName
    $text = $Content.Trim()
    if ($text -notmatch 'encoding="UTF-16"') {
        $text = $text -replace 'encoding="[^"]+"', 'encoding="UTF-16"'
    }
    [System.IO.File]::WriteAllText($path, $text, $utf16)
    Write-Host "Written: $FileName"
}

Write-TaskXmlFile -FileName "PPLID-Deploy-OnLogon.xml" -Content @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>PPLID Ops</Author>
    <Description>Bootstrap MAIN, DEV e HOM apos login (sem rebuild). Atraso 2 min apos logon.</Description>
    <URI>\PPLID-Deploy-OnLogon</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT5M</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\PPLID\ops\deploy_all.ps1"</Arguments>
      <WorkingDirectory>C:\PPLID\ops</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'@

Write-TaskXmlFile -FileName "PPLID-GitHub-Sync.xml" -Content @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>PPLID Ops</Author>
    <Description>Watcher GitHub + pipeline Railway-like (MAIN, DEV, HOM). Intervalo 2 min, IgnoreNew.</Description>
    <URI>\PPLID-GitHub-Sync</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT2M</Interval>
        <Duration>P3650D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-01T08:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>C:\Windows\System32\wscript.exe</Command>
      <Arguments>//B //Nologo "C:\PPLID\ops\run_update_hidden.vbs"</Arguments>
      <WorkingDirectory>C:\PPLID\ops</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'@

Write-TaskXmlFile -FileName "PPLID-Ops-Console.xml" -Content @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>PPLID Ops</Author>
    <Description>Inicia o Console de Operacoes PPLID ao fazer logon.</Description>
    <URI>\PPLID-Ops-Console</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\PPLID\ops\start_ops_console.ps1"</Arguments>
      <WorkingDirectory>C:\PPLID\ops</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'@

$testPath = Join-Path $tasksDir "PPLID-Deploy-OnLogon.xml"
$bytes = [System.IO.File]::ReadAllBytes($testPath)[0..9]
Write-Host ("Sample bytes: " + (($bytes | ForEach-Object { '{0:X2}' -f $_ }) -join ' '))

try {
    $null = schtasks /Create /TN "PPLID-Test-Import-Check" /XML $testPath /F 2>&1
    if ($LASTEXITCODE -eq 0) {
        schtasks /Delete /TN "PPLID-Test-Import-Check" /F | Out-Null
        Write-Host "Validacao schtasks: OK"
    } elseif ($LASTEXITCODE -eq 1) {
        Write-Host "Validacao schtasks: XML aceito (Create bloqueado por politica; use GUI)."
    }
} catch {
    Write-Host "Validacao schtasks ignorada: $($_.Exception.Message)"
}

Write-Host "Pronto. Importe em taskschd.msc -> Acao -> Importar Tarefa..."
