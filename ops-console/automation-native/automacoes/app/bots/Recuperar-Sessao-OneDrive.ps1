# Recuperar-Sessao-OneDrive.ps1
# Objetivo: tentar recuperar sessao/autenticacao do OneDrive sem apagar arquivos locais.

$LogPath = "$env:TEMP\OneDrive_RecuperarSessao_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - $Message"
    Write-Host $line
    Add-Content -Path $LogPath -Value $line
}

function Get-OneDrivePath {
    $paths = @(
        "$env:LOCALAPPDATA\Microsoft\OneDrive\OneDrive.exe",
        "C:\Program Files\Microsoft OneDrive\OneDrive.exe",
        "C:\Program Files (x86)\Microsoft OneDrive\OneDrive.exe"
    )

    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }

    return $null
}

function Get-OneDriveBusinessTenantId {
    $candidates = @(
        'HKCU:\Software\Microsoft\OneDrive\Accounts\Business1',
        'HKCU:\Software\Microsoft\OneDrive'
    )

    foreach ($path in $candidates) {
        $props = Get-ItemProperty $path -ErrorAction SilentlyContinue
        if (-not $props) { continue }

        foreach ($name in @('BusinessTenantId', 'TenantId', 'CID')) {
            $value = [string]$props.$name
            if ($value -match '^[0-9a-fA-F-]{36}$') {
                return $value
            }
        }
    }

    return $null
}

function Start-OneDriveLoginPrompts {
    param(
        [Parameter(Mandatory = $true)][string]$OneDriveExe
    )

    Write-Log "Abrindo interface principal do OneDrive (sem /background)..."
    Start-Process -FilePath $OneDriveExe
    Start-Sleep -Seconds 4

    $tenantId = Get-OneDriveBusinessTenantId
    if ($tenantId) {
        Write-Log "Abrindo assistente corporativo (/configure_business) para tenant $tenantId..."
        Start-Process -FilePath $OneDriveExe -ArgumentList "/configure_business:$tenantId"
    } else {
        Write-Log "Abrindo assistente corporativo (/configure_business)..."
        Start-Process -FilePath $OneDriveExe -ArgumentList '/configure_business'
    }
    Start-Sleep -Seconds 4

    Write-Log "Abrindo protocolo odopen://launch..."
    try {
        Start-Process 'odopen://launch'
    } catch {
        Write-Log "Falha ao abrir odopen://launch: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 2

    Write-Log "Abrindo contas corporativas/escolares do Windows (ms-settings:workplace)..."
    Start-Process 'ms-settings:workplace'
    Start-Sleep -Seconds 2

    Write-Log "Abrindo contas do Windows (ms-settings:accounts)..."
    Start-Process 'ms-settings:accounts'
}

Write-Log "Iniciando tentativa de recuperacao de sessao do OneDrive."

if ($env:ONEDRIVE_SIMULATE_RECOVER -eq '1') {
    Write-Log "MODO TESTE: simulando recuperacao (sem reiniciar OneDrive nem abrir janelas)"
    Write-Log "SIMULACAO: Stop-Process OneDrive"
    Write-Log "SIMULACAO: OneDrive.exe /background"
    Write-Log "SIMULACAO: OneDrive.exe (janela principal)"
    Write-Log "SIMULACAO: OneDrive.exe /configure_business"
    Write-Log "SIMULACAO: odopen://launch"
    Write-Log "SIMULACAO: ms-settings:workplace"
    Write-Log "Processo concluido (simulado)."
    exit 0
}

$OneDriveExe = Get-OneDrivePath

if (-not $OneDriveExe) {
    Write-Log "ERRO: OneDrive.exe nao encontrado."
    Write-Log "Verifique se o OneDrive esta instalado."
    exit 1
}

Write-Log "OneDrive encontrado em: $OneDriveExe"

Write-Log "Encerrando processos do OneDrive..."
Stop-Process -Name OneDrive -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5

Write-Log "Iniciando OneDrive em segundo plano..."
Start-Process -FilePath $OneDriveExe -ArgumentList '/background'
Start-Sleep -Seconds 15

$Process = Get-Process -Name OneDrive -ErrorAction SilentlyContinue

if ($Process) {
    Write-Log "OneDrive iniciado com sucesso."
} else {
    Write-Log "OneDrive nao iniciou corretamente. Tentando iniciar normalmente..."
    Start-Process -FilePath $OneDriveExe
    Start-Sleep -Seconds 10
}

Start-OneDriveLoginPrompts -OneDriveExe $OneDriveExe

Write-Log "Concluido. Se necessario, conclua o login nas janelas abertas do OneDrive."
Write-Log "Arquivo de log: $LogPath"
