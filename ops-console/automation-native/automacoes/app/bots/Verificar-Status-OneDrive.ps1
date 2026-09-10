# Verificar-Status-OneDrive.ps1

Write-Host "Verificando status do OneDrive..." -ForegroundColor Cyan

$Resultado = [ordered]@{
    OneDriveRodando = $false
    PastaOneDriveEncontrada = $false
    CaminhoOneDrive = $null
    ContaAutenticada = $true
    ArquivosComProblema = @()
    Motivos = @()
    PossivelDessync = $false
}

function Add-MotivoDessync {
    param([string]$Motivo)
    if ($Motivo -and ($Resultado.Motivos -notcontains $Motivo)) {
        $Resultado.Motivos += $Motivo
    }
    $Resultado.PossivelDessync = $true
}

function Invoke-SimulatedDessyncCheck {
    Write-Host "MODO TESTE: simulando OneDrive dessincronizado" -ForegroundColor Magenta

    $Resultado.OneDriveRodando = $true
    $Resultado.PastaOneDriveEncontrada = $true
    $Resultado.CaminhoOneDrive = 'C:\Simulacao\OneDrive - TESTE'
    $Resultado.ContaAutenticada = $false
    Add-MotivoDessync 'Conta OneDrive nao autenticada (ClientNotSignedInBalloonState=1)'
    Add-MotivoDessync 'Conta corporativa desvinculada: 12-DeleteAccountSettingsReason::UserTriggeredUnlink (simulado)'
    Add-MotivoDessync 'Arquivos com possivel problema de sincronizacao: 3 (simulado)'

    Write-Host ""
    Write-Host "Motivos de dessincronizacao:" -ForegroundColor Yellow
    $Resultado.Motivos | ForEach-Object { Write-Host " - $_" }

    Write-Host ""
    Write-Host "STATUS FINAL: Possivel dessincronizacao detectada." -ForegroundColor Red
    exit 2
}

if ($env:ONEDRIVE_SIMULATE_DESSYNC -eq '1') {
    Invoke-SimulatedDessyncCheck
}

function Get-EnvPathValue {
    param([string]$Name)

    foreach ($target in @('Process', 'User', 'Machine')) {
        $value = [Environment]::GetEnvironmentVariable($Name, $target)
        if ($value -and (Test-Path -LiteralPath $value)) {
            return $value
        }
    }
    return $null
}

function Get-OneDriveFolderPaths {
    $paths = New-Object System.Collections.Generic.List[string]

    foreach ($name in @('OneDriveCommercial', 'OneDriveConsumer', 'OneDrive')) {
        $value = Get-EnvPathValue -Name $name
        if ($value) {
            [void]$paths.Add($value)
        }
    }

    Get-ChildItem 'HKCU:\Software\Microsoft\OneDrive\Accounts' -ErrorAction SilentlyContinue | ForEach-Object {
        $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
        if ($props.UserFolder -and (Test-Path -LiteralPath $props.UserFolder)) {
            [void]$paths.Add([string]$props.UserFolder)
        }
    }

    return ,@($paths.ToArray() | Select-Object -Unique)
}

function Test-StatusTextoProblematico {
    param([string]$StatusTexto)

    if ([string]::IsNullOrWhiteSpace($StatusTexto)) {
        return $false
    }

    $normalizado = $StatusTexto.ToLowerInvariant()
    $padroesProblema = 'syncing|sincroniz|pending|pendente|problem|problema|error|erro|falha|failed|conflict|conflito|paused|pausado|processing|processando|disponibilidade reduzida|not available|indispon'
    $padroesOk = 'up to date|atualizado|disponivel localmente|available locally|always keep on this device|manter sempre neste dispositivo|online only|somente online'

    if ($normalizado -match $padroesOk) {
        return $false
    }

    return $normalizado -match $padroesProblema
}

function Test-OneDriveAccountLinked {
    $root = Get-ItemProperty 'HKCU:\Software\Microsoft\OneDrive' -ErrorAction SilentlyContinue
    if ($root -and $root.ClientNotSignedInBalloonState -eq 1) {
        return $false
    }

    $commercialPath = Get-EnvPathValue -Name 'OneDriveCommercial'
    if (-not $commercialPath) {
        $commercialPath = Get-EnvPathValue -Name 'OneDrive'
    }
    if (-not $commercialPath -or -not (Test-Path -LiteralPath $commercialPath)) {
        return $false
    }

    $business = Get-ItemProperty 'HKCU:\Software\Microsoft\OneDrive\Accounts\Business1' -ErrorAction SilentlyContinue
    if ($business -and $business.UserFolder) {
        return Test-Path -LiteralPath $business.UserFolder
    }

    if ($root -and $root.LastBusinessUnlinkedReason) {
        return $false
    }

    return $true
}

function Clear-StaleOneDriveSessionFlags {
    $key = 'HKCU:\Software\Microsoft\OneDrive'
    $removed = @()

    foreach ($name in @('LastBusinessUnlinkedReason', 'LastBusinessUnlinkedTimeStamp')) {
        $existing = Get-ItemProperty $key -Name $name -ErrorAction SilentlyContinue
        if ($null -ne $existing.$name) {
            Remove-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue
            $removed += $name
        }
    }

    if ($removed.Count -gt 0) {
        Write-Host ('Flags obsoletas removidas do registro: {0}' -f ($removed -join ', ')) -ForegroundColor Green
    }
}

function Get-StatusColumnIndex {
    param($Folder)

    $aliases = @(
        'Status',
        'Sync Status',
        'Status de disponibilidade',
        'Availability Status',
        'Estado',
        'Estado de disponibilidade'
    )

    for ($i = 0; $i -lt 400; $i++) {
        $nomeColuna = $Folder.GetDetailsOf($null, $i)
        if ([string]::IsNullOrWhiteSpace($nomeColuna)) {
            continue
        }

        foreach ($alias in $aliases) {
            if ($nomeColuna -eq $alias) {
                return $i
            }
        }

        $lower = $nomeColuna.ToLowerInvariant()
        if ($lower -match 'status|estado|availability|disponibilidade|sync') {
            return $i
        }
    }

    return -1
}

# 1. Verifica processo
$Processo = Get-Process -Name OneDrive -ErrorAction SilentlyContinue

if ($Processo) {
    $Resultado.OneDriveRodando = $true
    Write-Host "OneDrive.exe esta rodando." -ForegroundColor Green
} else {
    Write-Host "OneDrive.exe nao esta rodando." -ForegroundColor Red
    Add-MotivoDessync "Processo OneDrive.exe nao encontrado"
}

# 2. Verifica sessao/conta no registro
$OneDriveRoot = Get-ItemProperty 'HKCU:\Software\Microsoft\OneDrive' -ErrorAction SilentlyContinue
$ContaVinculada = Test-OneDriveAccountLinked
$NaoAutenticado = $false

if ($OneDriveRoot) {
    if ($OneDriveRoot.ClientNotSignedInBalloonState -eq 1) {
        $NaoAutenticado = $true
        $Resultado.ContaAutenticada = $false
        Add-MotivoDessync "Conta OneDrive nao autenticada (ClientNotSignedInBalloonState=1)"
        Write-Host "Conta OneDrive nao autenticada." -ForegroundColor Red
    }

    if ($OneDriveRoot.LastBusinessUnlinkedReason) {
        if (-not $ContaVinculada -or $NaoAutenticado) {
            $Resultado.ContaAutenticada = $false
            Add-MotivoDessync "Conta corporativa desvinculada: $($OneDriveRoot.LastBusinessUnlinkedReason)"
            Write-Host "Conta corporativa desvinculada: $($OneDriveRoot.LastBusinessUnlinkedReason)" -ForegroundColor Red
        } else {
            Clear-StaleOneDriveSessionFlags
            Write-Host "Conta corporativa aparenta vinculada; flag obsoleta de desvinculacao ignorada." -ForegroundColor Yellow
        }
    }

    if ($OneDriveRoot.ClientEverSignedIn -eq 0) {
        $Resultado.ContaAutenticada = $false
        Add-MotivoDessync "OneDrive nunca concluiu autenticacao neste perfil"
        Write-Host "OneDrive nunca concluiu autenticacao neste perfil." -ForegroundColor Red
    }
}

# 3. Detecta pasta do OneDrive
$PossiveisPastas = Get-OneDriveFolderPaths
$CaminhoOneDrive = $null
if (@($PossiveisPastas).Count -gt 0) {
    $CaminhoOneDrive = @($PossiveisPastas)[0]
}

if ($CaminhoOneDrive) {
    $Resultado.PastaOneDriveEncontrada = $true
    $Resultado.CaminhoOneDrive = $CaminhoOneDrive
    Write-Host ('Pasta OneDrive encontrada: {0}' -f $CaminhoOneDrive) -ForegroundColor Green
} else {
    Write-Host "Nenhuma pasta OneDrive encontrada." -ForegroundColor Red
    Add-MotivoDessync "Pasta OneDrive nao encontrada nas variaveis de ambiente ou registro"
}

# 4. Verifica status dos arquivos pelo Shell do Windows
if ($CaminhoOneDrive) {
    Write-Host "Analisando status dos arquivos..." -ForegroundColor Yellow

    $maxArquivos = 5000
    try {
        $maxRaw = [Environment]::GetEnvironmentVariable('ONEDRIVE_VERIFY_MAX_FILES', 'Process')
        if (-not $maxRaw) {
            $maxRaw = [Environment]::GetEnvironmentVariable('ONEDRIVE_VERIFY_MAX_FILES', 'User')
        }
        if ($maxRaw) {
            $maxArquivos = [Math]::Max(100, [int]$maxRaw)
        }
    } catch {
        $maxArquivos = 5000
    }

    $shell = New-Object -ComObject Shell.Application
    $folder = $shell.Namespace($CaminhoOneDrive)

    if ($folder) {
        $indiceStatus = Get-StatusColumnIndex -Folder $folder

        if ($indiceStatus -lt 0) {
            Write-Host "Nao foi possivel localizar a coluna de status do Explorer." -ForegroundColor Yellow
            Add-MotivoDessync "Coluna de status do Explorer nao encontrada; verificacao de arquivos inconclusiva"
        } else {
            $analisados = 0
            $itens = Get-ChildItem -LiteralPath $CaminhoOneDrive -Recurse -File -ErrorAction SilentlyContinue

            foreach ($item in $itens) {
                if ($analisados -ge $maxArquivos) {
                    Write-Host "Limite de analise atingido ($maxArquivos arquivos)." -ForegroundColor Yellow
                    break
                }

                $analisados++
                try {
                    $pastaItem = $shell.Namespace($item.DirectoryName)
                    if (-not $pastaItem) { continue }

                    $arquivoShell = $pastaItem.ParseName($item.Name)
                    if (-not $arquivoShell) { continue }

                    $status = $pastaItem.GetDetailsOf($arquivoShell, $indiceStatus)
                    if (Test-StatusTextoProblematico -StatusTexto $status) {
                        $Resultado.ArquivosComProblema += [PSCustomObject]@{
                            Arquivo = $item.FullName
                            Status = $status
                        }
                    }
                } catch {
                    # Ignora arquivos inacessiveis
                }
            }

            if ($Resultado.ArquivosComProblema.Count -gt 0) {
                Add-MotivoDessync ("Arquivos com possivel problema de sincronizacao: {0}" -f $Resultado.ArquivosComProblema.Count)
                Write-Host "Foram encontrados arquivos com possivel problema de sincronizacao." -ForegroundColor Red
            } else {
                Write-Host "Nenhum arquivo com erro ou pendencia aparente encontrado." -ForegroundColor Green
            }
        }
    }
}

Write-Host ""
Write-Host "Resumo:" -ForegroundColor Cyan
$Resultado | Format-List

if ($Resultado.Motivos.Count -gt 0) {
    Write-Host ""
    Write-Host "Motivos de dessincronizacao:" -ForegroundColor Yellow
    $Resultado.Motivos | ForEach-Object { Write-Host " - $_" }
}

if ($Resultado.ArquivosComProblema.Count -gt 0) {
    Write-Host ""
    Write-Host "Arquivos com problema:" -ForegroundColor Yellow
    $Resultado.ArquivosComProblema | Select-Object -First 20 | Format-Table -AutoSize
}

if ($Resultado.PossivelDessync) {
    Write-Host ""
    Write-Host "STATUS FINAL: Possivel dessincronizacao detectada." -ForegroundColor Red
    exit 2
} else {
    if (Test-OneDriveAccountLinked) {
        Clear-StaleOneDriveSessionFlags
    }
    Write-Host ""
    Write-Host "STATUS FINAL: Nenhum sinal forte de dessincronizacao detectado." -ForegroundColor Green
    exit 0
}
