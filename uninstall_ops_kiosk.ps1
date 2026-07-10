param(
    [string]$RepoDir = "",
    [string]$KioskUser = "PPLID_Kiosk",
    [switch]$AllUsersStartup
)

$install = Join-Path $PSScriptRoot "install_ops_kiosk.ps1"
& $install -RepoDir $RepoDir -KioskUser $KioskUser -Uninstall -AllUsersStartup:$AllUsersStartup
