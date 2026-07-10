<#
.SYNOPSIS
    Configura modo kiosk (Edge tela cheia) para o Console de Operacoes PPLID.
#>
param(
    [string]$ConfigPath = "",
    [string]$KioskUser = "PPLID_Kiosk",
    [string]$KioskPassword = "",
    [switch]$Uninstall,
    [switch]$SkipUserCreation,
    [switch]$AllUsersStartup,
    [switch]$RequireAdminForUser
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\paths.ps1")

if (-not $ConfigPath) {
    $ConfigPath = Get-PplidEnvConfigPath -ScriptRoot $PSScriptRoot
}

function Get-OpsKioskUrl {
    param([string]$ConfigPath)
    if (-not (Test-Path $ConfigPath)) {
        return "http://127.0.0.1:5190/?kiosk=1"
    }
    $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    $base = $cfg.opsConsole.kioskUrl
    if (-not $base) {
        $machine = Get-PplidMachineConfig
        $lan = if ($machine.lanIp) { $machine.lanIp } elseif ($cfg.lanIp) { $cfg.lanIp } else { "127.0.0.1" }
        $port = if ($cfg.opsConsolePort) { $cfg.opsConsolePort } else { 5190 }
        $base = "http://${lan}:${port}"
    }
    $base = $base.TrimEnd("/")
    if ($base -notmatch "\?") {
        return "$base/?kiosk=1"
    }
    if ($base -notmatch "kiosk=1") {
        return "$base&kiosk=1"
    }
    return $base
}

function Find-EdgeExecutable {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

function Test-IsAdministrator {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$KioskUrl = Get-OpsKioskUrl -ConfigPath $ConfigPath
$ShortcutName = "PPLID Ops Console Kiosk.lnk"
$IsAdmin = Test-IsAdministrator

if ($AllUsersStartup -and -not $IsAdmin) {
    throw "A opcao -AllUsersStartup exige PowerShell como Administrador."
}

if (-not $IsAdmin) {
    if (-not $SkipUserCreation -and $RequireAdminForUser) {
        throw "Criar usuario local exige Administrador. Use -SkipUserCreation ou execute como admin."
    }
    if (-not $SkipUserCreation) {
        Write-Host "Sem privilegio de administrador: instalando apenas atalho no Startup do usuario atual."
        $SkipUserCreation = $true
    }
} elseif (-not $SkipUserCreation) {
    Write-Host "Modo administrador: usuario local '$KioskUser' sera criado se nao existir."
}

$StartupDir = if ($AllUsersStartup) {
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Startup"
} else {
    [Environment]::GetFolderPath("Startup")
}

if ($Uninstall) {
    $shortcutPath = Join-Path $StartupDir $ShortcutName
    if (Test-Path $shortcutPath) {
        Remove-Item $shortcutPath -Force
        Write-Host "Removido: $shortcutPath"
    } else {
        Write-Host "Atalho kiosk nao encontrado em $StartupDir"
    }
    exit 0
}

$edge = Find-EdgeExecutable
if (-not $edge) {
    throw "Microsoft Edge nao encontrado. Instale o Edge para usar o modo kiosk."
}

if (-not $SkipUserCreation) {
    $existing = Get-LocalUser -Name $KioskUser -ErrorAction SilentlyContinue
    if (-not $existing) {
        if (-not $KioskPassword) {
            $KioskPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 16 | ForEach-Object { [char]$_ })
            Write-Host "Senha gerada para ${KioskUser}: $KioskPassword"
        }
        $secure = ConvertTo-SecureString $KioskPassword -AsPlainText -Force
        New-LocalUser -Name $KioskUser -Password $secure -FullName "PPLID Console Kiosk" -Description "Sessao dedicada ao monitor de operacoes" | Out-Null
        Add-LocalGroupMember -Group "Users" -Member $KioskUser -ErrorAction SilentlyContinue
        Write-Host "Usuario local criado: $KioskUser"
    } else {
        Write-Host "Usuario '$KioskUser' ja existe."
    }
}

if (-not (Test-Path $StartupDir)) {
    New-Item -ItemType Directory -Path $StartupDir -Force | Out-Null
}

$wsh = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path $StartupDir $ShortcutName
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $edge
$shortcut.Arguments = "--kiosk `"$KioskUrl`" --no-first-run --disable-features=msEdgeSidebarV2"
$shortcut.WorkingDirectory = Split-Path $edge -Parent
$shortcut.Description = "PPLID Console de Operacoes (kiosk)"
$shortcut.Save()

Write-Host ""
Write-Host "Kiosk Edge configurado."
Write-Host "  URL: $KioskUrl"
Write-Host "  Atalho: $shortcutPath"
Write-Host ""
Write-Host "Desinstalar: install_ops_kiosk.ps1 -Uninstall"
