. (Join-Path $PSScriptRoot "deploy_paths.ps1")
. (Join-Path $PSScriptRoot "deploy_logging.ps1")

function Get-PplidDepsFingerprintFromDir {
    param([string]$ReleaseDir)

    $parts = @()
    foreach ($rel in @(
        "backend\requirements.txt",
        "frontend\package-lock.json",
        "frontend\package.json",
        "automacoes\pyproject.toml"
    )) {
        $p = Join-Path $ReleaseDir $rel
        if (Test-Path $p) {
            $parts += (Get-FileHash -Path $p -Algorithm SHA256).Hash
        }
    }
    if (-not $parts.Count) { return "" }
    return ($parts -join "|")
}

function Initialize-PplidPipCache {
    $cacheDir = Get-PplidPipCacheDir
    if (-not (Test-Path $cacheDir)) {
        New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
    }
    $env:PIP_CACHE_DIR = $cacheDir
    $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
    return $cacheDir
}

function Find-PplidVenvSourceForFingerprint {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$Environment,
        [Parameter(Mandatory = $true)]
        [string]$Fingerprint
    )

    if (-not $Fingerprint) { return $null }

    $paths = Get-PplidDeployEnvPaths -Environment $Environment
    $candidates = @()
    foreach ($linkName in @("Current", "Previous")) {
        $linkPath = $paths.$linkName
        if (-not (Test-Path $linkPath)) { continue }
        try {
            $target = (Get-Item $linkPath -Force).Target
            if ($target -is [Array]) { $target = $target[0] }
            if ($target) { $candidates += [string]$target }
        } catch { }
    }

    foreach ($dir in $candidates) {
        $venv = Join-Path $dir "backend\.venv"
        if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { continue }
        $fp = Get-PplidDepsFingerprintFromDir -ReleaseDir $dir
        if ($fp -eq $Fingerprint) { return $venv }
    }
    return $null
}

function Copy-PplidVenv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceVenv,
        [Parameter(Mandatory = $true)]
        [string]$TargetVenv
    )

    if (-not (Test-Path $SourceVenv)) { return $false }
    $parent = Split-Path $TargetVenv -Parent
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (Test-Path $TargetVenv) {
        Remove-Item -LiteralPath $TargetVenv -Recurse -Force -ErrorAction SilentlyContinue
    }

    $null = robocopy $SourceVenv $TargetVenv /E /NFL /NDL /NJH /NJS /nc /ns /np /XD "__pycache__" 2>&1
    return (Test-Path (Join-Path $TargetVenv "Scripts\python.exe"))
}

function Copy-PplidCrossEnvArtifacts {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("MAIN", "DEV", "HOM")]
        [string]$SourceEnvironment,
        [Parameter(Mandatory = $true)]
        [string]$TargetSha,
        [Parameter(Mandatory = $true)]
        [string]$TargetReleaseDir
    )

    $sourceRelease = Get-PplidReleaseDir -Environment $SourceEnvironment -Sha $TargetSha
    $sourceMetaFile = Join-Path $sourceRelease "meta.json"
    if (-not (Test-Path $sourceMetaFile)) { return $false }

    try {
        $sourceMeta = Get-Content $sourceMetaFile -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $false
    }
    if ($sourceMeta.built -ne $true) { return $false }

    $targetFp = Get-PplidDepsFingerprintFromDir -ReleaseDir $TargetReleaseDir
    $sourceFp = [string]$sourceMeta.depsFingerprint
    if (-not $sourceFp) {
        $sourceFp = Get-PplidDepsFingerprintFromDir -ReleaseDir $sourceRelease
    }
    if ($sourceFp -and $targetFp -and $sourceFp -ne $targetFp) { return $false }

    $copied = $false
    $sourceVenv = Join-Path $sourceRelease "backend\.venv"
    $targetVenv = Join-Path $TargetReleaseDir "backend\.venv"
    if (Copy-PplidVenv -SourceVenv $sourceVenv -TargetVenv $targetVenv) {
        $copied = $true
    }

    $sourceDist = Join-Path $sourceRelease "frontend\dist"
    $targetDist = Join-Path $TargetReleaseDir "frontend\dist"
    if (Test-Path $sourceDist) {
        $distParent = Split-Path $targetDist -Parent
        if (-not (Test-Path $distParent)) {
            New-Item -ItemType Directory -Path $distParent -Force | Out-Null
        }
        if (Test-Path $targetDist) {
            Remove-Item -LiteralPath $targetDist -Recurse -Force -ErrorAction SilentlyContinue
        }
        $null = robocopy $sourceDist $targetDist /E /NFL /NDL /NJH /NJS /nc /ns /np 2>&1
        $copied = $true
    }

    return $copied
}

function Get-PipOutputTail {
    param(
        [string]$OutputFile,
        [int]$Tail = 20
    )
    if (-not (Test-Path $OutputFile)) { return @() }
    try {
        return @(Get-Content -Path $OutputFile -Tail $Tail -ErrorAction Stop)
    } catch {
        return @()
    }
}

function Get-PipOutputNewLines {
    param(
        [string]$OutputFile,
        [int]$AfterLine = 0
    )

    if (-not (Test-Path $OutputFile)) {
        return @(), $AfterLine
    }
    try {
        $all = @(Get-Content -Path $OutputFile -ErrorAction Stop)
        if ($AfterLine -ge $all.Count) {
            return @(), $all.Count
        }
        $new = @($all | Select-Object -Skip $AfterLine)
        return $new, $all.Count
    } catch {
        return @(), $AfterLine
    }
}

function Write-PipProgressMarkers {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [string]$LogName,
        [string[]]$Lines
    )

    if (-not $Environment -or -not $RunId -or -not $Lines) { return }
    foreach ($line in $Lines) {
        if (-not $line) { continue }
        $trimmed = $line.Trim()
        if ($trimmed -match '^(?i)(Collecting|Downloading|Installing)\s+(\S+)') {
            $verb = $Matches[1].ToLower()
            $package = $Matches[2]
            Write-DeployLogInfo -Environment $Environment -RunId $RunId `
                -Message "pip_progress: $verb $package" -LogName $LogName
        }
    }
}

function Parse-PipFailure {
    param(
        [string[]]$Lines,
        [int]$ExitCode = 1,
        [int]$TimeoutSec = 600
    )

    $text = ($Lines | Where-Object { $_ }) -join "`n"
    $detail = @{
        rootCause      = "unknown"
        package        = $null
        versions       = @()
        command        = $null
        recommendation = "Revise os logs completos do pip e tente novamente."
        message        = "pip install falhou."
    }

    if ($ExitCode -eq 124) {
        $detail.rootCause = "timeout"
        $detail.message = "pip install excedeu o tempo limite ($TimeoutSec s)."
        $detail.recommendation = "Aumente o timeout ou verifique rede/cache do pip; reexecute o deploy."
        return $detail
    }

    if ($text -match '(?i)ResolutionImpossible|dependency resolver|conflicting dependencies') {
        $detail.rootCause = "dependency_conflict"
        $detail.message = "pip install falhou devido a conflito entre versoes de bibliotecas."
        $detail.recommendation = "Revise requirements.txt e pyproject.toml; alinhe versoes conflitantes."
    } elseif ($text -match '(?i)No matching distribution|Could not find a version') {
        $detail.rootCause = "missing_distribution"
        $detail.message = "pip install falhou: pacote ou versao indisponivel."
        $detail.recommendation = "Verifique nome/versao do pacote e compatibilidade com Python."
    } elseif ($text -match '(?i)ModuleNotFoundError:.*pip\._vendor') {
        $detail.rootCause = "corrupt_venv"
        $detail.message = "pip install falhou: ambiente virtual (.venv) corrompido."
        $detail.recommendation = "O pipeline recriara o venv automaticamente no proximo deploy; se persistir, remova backend/.venv manualmente."
    } elseif ($text -match '(?i)ERROR:') {
        $detail.rootCause = "pip_error"
        $detail.message = "pip install falhou com erro reportado pelo pip."
        $detail.recommendation = "Revise a saida completa do pip abaixo."
    }

    if ($text -match '(?i)because\s+([\w\-\.]+)\s+version') {
        $detail.package = $Matches[1]
    } elseif ($text -match '(?i)Package\s+[''""]([^''""]+)[''""]') {
        $detail.package = $Matches[1]
    }

  $versionMatches = [regex]::Matches($text, '\b\d+\.\d+(?:\.\d+)?(?:[a-z0-9]+)?\b')
    if ($versionMatches.Count -gt 0) {
        $detail.versions = @($versionMatches | ForEach-Object { $_.Value } | Select-Object -Unique | Select-Object -First 6)
    }

    return $detail
}

function Write-PipOutputToDeployLog {
    param(
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment,
        [string]$RunId,
        [string]$LogName,
        [string[]]$Lines
    )

    if (-not $Environment -or -not $RunId -or -not $Lines) { return }
    foreach ($line in $Lines) {
        if (-not $line) { continue }
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        $level = "INFO"
        if ($trimmed -match '(?i)^ERROR:|^CRITICAL:') { $level = "ERROR" }
        elseif ($trimmed -match '(?i)^WARNING:|^WARN:') { $level = "WARN" }
        Write-DeployLogEntry -Environment $Environment -RunId $RunId -Level $level -Message $trimmed -LogName $LogName
    }
}

function Install-PplidAutomacoesEditable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ReleaseDir,
        [Parameter(Mandatory = $true)]
        [string]$VenvPython,
        [int]$TimeoutSec = 180,
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment = "",
        [string]$RunId = "",
        [string]$LogName = "build.log"
    )

    $automacoesDir = Join-Path $ReleaseDir "automacoes"
    if (-not (Test-Path (Join-Path $automacoesDir "pyproject.toml"))) {
        return @{ exitCode = -1; outputTail = @(); command = ""; errorDetail = $null }
    }
    Initialize-PplidPipCache | Out-Null
    return Invoke-PplidPipInstall -VenvPython $VenvPython -Args @("-e", $automacoesDir) -TimeoutSec $TimeoutSec `
        -Environment $Environment -RunId $RunId -LogName $LogName
}

function Invoke-PplidPipInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$VenvPython,
        [Parameter(Mandatory = $true)]
        [string[]]$Args,
        [int]$TimeoutSec = 3600,
        [ValidateSet("MAIN", "DEV", "HOM")][string]$Environment = "",
        [string]$RunId = "",
        [string]$LogName = "build.log"
    )

    Initialize-PplidPipCache | Out-Null
    $pipArgs = @("-m", "pip", "install") + $Args + @("--progress-bar", "off")
    $cmdDisplay = "pip install " + ($Args -join " ")

    if ($Environment -and $RunId) {
        Write-DeployLogInfo -Environment $Environment -RunId $RunId -Message "Running: $cmdDisplay" -LogName $LogName
    }

    $outputFile = Join-Path $env:TEMP ("pplid-pip-{0}-{1}.log" -f $RunId, ([guid]::NewGuid().ToString("N").Substring(0, 8)))
    $job = Start-Job -ScriptBlock {
        param($Python, $PipArgs, $OutFile)
        try {
            & $Python @PipArgs *> $OutFile
        } catch {
            $_ | Out-File -FilePath $OutFile -Append -Encoding utf8
        }
        return $LASTEXITCODE
    } -ArgumentList $VenvPython, $pipArgs, $outputFile

    $lineOffset = 0
    $exitCode = 1
    $timedOut = $false
    $deadline = (Get-Date).AddSeconds($TimeoutSec)

    while ($true) {
        $newLines, $lineOffset = Get-PipOutputNewLines -OutputFile $outputFile -AfterLine $lineOffset
        if ($newLines.Count -gt 0) {
            Write-PipOutputToDeployLog -Environment $Environment -RunId $RunId -LogName $LogName -Lines $newLines
            Write-PipProgressMarkers -Environment $Environment -RunId $RunId -LogName $LogName -Lines $newLines
        }

        $jobState = (Get-Job -Id $job.Id -ErrorAction SilentlyContinue).State
        if ($jobState -in @("Completed", "Failed", "Stopped")) {
            break
        }
        if ((Get-Date) -gt $deadline) {
            Stop-Job -Job $job -ErrorAction SilentlyContinue
            $timedOut = $true
            break
        }
        Start-Sleep -Milliseconds 800
    }

    if ($timedOut) {
        $exitCode = 124
    } else {
        $received = Receive-Job -Job $job
        if ($null -ne $received) { $exitCode = [int]$received } else { $exitCode = 1 }
    }
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue

    $newLines, $lineOffset = Get-PipOutputNewLines -OutputFile $outputFile -AfterLine $lineOffset
    if ($newLines.Count -gt 0) {
        Write-PipOutputToDeployLog -Environment $Environment -RunId $RunId -LogName $LogName -Lines $newLines
        Write-PipProgressMarkers -Environment $Environment -RunId $RunId -LogName $LogName -Lines $newLines
    }

    $tail = Get-PipOutputTail -OutputFile $outputFile -Tail 20

    if ($exitCode -eq 124 -and $Environment -and $RunId -and (Test-Path $outputFile)) {
        try {
            $runDir = Get-PplidDeployRunDir -Environment $Environment -RunId $RunId
            if (-not (Test-Path $runDir)) {
                New-Item -ItemType Directory -Path $runDir -Force | Out-Null
            }
            Copy-Item -Path $outputFile -Destination (Join-Path $runDir "deps_backend.pip.log") -Force
            Write-DeployLogWarn -Environment $Environment -RunId $RunId `
                -Message "pip timeout: log completo preservado em deps_backend.pip.log" -LogName $LogName
        } catch { }
    }

    if (Test-Path $outputFile) {
        Remove-Item $outputFile -Force -ErrorAction SilentlyContinue
    }

    $errorDetail = $null
    if ($exitCode -ne 0) {
        $errorDetail = Parse-PipFailure -Lines $tail -ExitCode $exitCode -TimeoutSec $TimeoutSec
        $errorDetail.command = $cmdDisplay
    }

    return @{
        exitCode    = $exitCode
        outputTail  = $tail
        command     = $cmdDisplay
        errorDetail = $errorDetail
    }
}
