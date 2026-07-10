function Get-PplidTaskXmlPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TemplateFileName
    )

    $tasksDir = Join-Path (Split-Path $PSScriptRoot -Parent) "tasks"
    return Join-Path $tasksDir $TemplateFileName
}

function Write-PplidTaskGuiFallback {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TemplateFileName
    )

    $xmlPath = Get-PplidTaskXmlPath -TemplateFileName $TemplateFileName
    $guidePath = Join-Path (Split-Path $PSScriptRoot -Parent) "TASK_SCHEDULER.md"

    Write-Host ""
    Write-Host "Nao foi possivel registrar via linha de comando (Acesso negado e comum sem Admin)." -ForegroundColor Yellow
    Write-Host "Importe a task pela GUI do Agendador de Tarefas:" -ForegroundColor Yellow
    Write-Host "  1. Win+R -> taskschd.msc"
    Write-Host "  2. Acao -> Importar Tarefa..."
    Write-Host "  3. Arquivo: $xmlPath"
    Write-Host "  4. Confirme o usuario logado (ex.: SERASA\c92928a)"
    Write-Host ""
    Write-Host "Guia: $guidePath"
    Write-Host ""
}

function Show-PplidTaskXmlExport {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TemplateFileName,
        [string]$TaskLabel = ""
    )

    $xmlPath = Get-PplidTaskXmlPath -TemplateFileName $TemplateFileName
    if (-not (Test-Path $xmlPath)) {
        throw "XML nao encontrado: $xmlPath"
    }

    $label = if ($TaskLabel) { $TaskLabel } else { [System.IO.Path]::GetFileNameWithoutExtension($TemplateFileName) }
    Write-Host "XML canonico para importar na GUI: $xmlPath"
    Write-Host "Task: $label"
    Write-Host ""
    Write-Host "Passos: taskschd.msc -> Acao -> Importar Tarefa... -> selecione o XML acima."
    Write-Host "Detalhes: $(Join-Path (Split-Path $PSScriptRoot -Parent) 'TASK_SCHEDULER.md')"
}
