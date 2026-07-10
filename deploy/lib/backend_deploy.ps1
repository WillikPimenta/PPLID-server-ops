. (Join-Path $PSScriptRoot "git_invoke.ps1")
. (Join-Path $PSScriptRoot "python_invoke.ps1")

function Get-PplidBackendDiffPaths {
    param(
        [Parameter(Mandatory = $true)]
        [string]$MirrorDir,
        [string]$FromSha = "",
        [Parameter(Mandatory = $true)]
        [string]$ToSha
    )

    if (-not $FromSha) {
        return @("backend/")
    }

    Push-Location $MirrorDir
    try {
        $lines = Invoke-PplidGit -Args @(
            "diff", "--name-only", $FromSha, $ToSha, "--", "backend/", "scripts/deploy/"
        ) -FailMessage "git diff backend falhou."
        return @($lines | Where-Object { $_ })
    } finally {
        Pop-Location
    }
}

function Test-PplidBackendChanged {
    param(
        [Parameter(Mandatory = $true)]
        [string]$MirrorDir,
        [string]$FromSha = "",
        [Parameter(Mandatory = $true)]
        [string]$ToSha
    )

    $paths = Get-PplidBackendDiffPaths -MirrorDir $MirrorDir -FromSha $FromSha -ToSha $ToSha
    return ($paths.Count -gt 0)
}

function Test-PplidReleaseHasFalhasModule {
    param(
        [Parameter(Mandatory = $true)]
        [string]$AppRoot
    )

    $falhasApp = Join-Path $AppRoot "backend\apps\falhas_criticas"
    return (Test-Path $falhasApp)
}

function Invoke-PplidBackendMigrate {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BackendDir,
        [Parameter(Mandatory = $true)]
        [string]$VenvPython,
        [scriptblock]$Log = $null
    )

    if ($Log) { & $Log "migrate..." }

    $migrateOut = Invoke-PplidPython -Python $VenvPython -Args @(
        "manage.py", "migrate", "--noinput", "--skip-checks"
    ) -WorkingDirectory $BackendDir -FailMessage "migrate falhou."

    $applied = @($migrateOut | Where-Object { $_ -match "Applying " })
    if ($Log) {
        if ($applied.Count -gt 0) {
            & $Log ("migrate aplicou $($applied.Count) migration(s).")
            foreach ($line in $applied) {
                & $Log ("  $line")
            }
        } else {
            & $Log "migrate: nenhuma migration pendente."
        }
    }

    if ($Log) { & $Log "migrate --check..." }
    Invoke-PplidPython -Python $VenvPython -Args @(
        "manage.py", "migrate", "--check"
    ) -WorkingDirectory $BackendDir -FailMessage "migrate --check falhou (migrations pendentes)."

    try {
        $showLines = Invoke-PplidPython -Python $VenvPython -Args @(
            "manage.py", "showmigrations", "--plan"
        ) -WorkingDirectory $BackendDir -FailMessage "showmigrations falhou."
        if ($Log -and $showLines.Count -gt 0) {
            $tail = $showLines | Select-Object -Last 8
            & $Log ("showmigrations (ultimas linhas): " + ($tail -join " | "))
        }
    } catch {
        if ($Log) { & $Log "showmigrations (aviso): $($_.Exception.Message)" }
    }
}

function Invoke-PplidBackendMigratePlan {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BackendDir,
        [Parameter(Mandatory = $true)]
        [string]$VenvPython,
        [scriptblock]$Log = $null
    )

    if ($Log) { & $Log "migrate --plan (pre-promote)..." }
    $planLines = Invoke-PplidPython -Python $VenvPython -Args @(
        "manage.py", "migrate", "--plan"
    ) -WorkingDirectory $BackendDir -FailMessage "migrate --plan falhou."

    $pending = @($planLines | Where-Object { $_ -match "\[ \]" })
    if ($Log) {
        if ($pending.Count -gt 0) {
            & $Log ("migrate --plan: $($pending.Count) migration(s) pendente(s) serao aplicadas no promote.")
        } else {
            & $Log "migrate --plan: schema em dia com o codigo da release."
        }
    }

    return $pending.Count
}

function Test-PplidBackendRoutes {
    param(
        [Parameter(Mandatory = $true)]
        [int]$BackendPort,
        [Parameter(Mandatory = $true)]
        [string]$AppRoot,
        [scriptblock]$Log = $null
    )

    $base = "http://127.0.0.1:$BackendPort"
    $errors = @()

    if (Test-PplidReleaseHasFalhasModule -AppRoot $AppRoot) {
        $healthUrl = "$base/falhas/health/"
        try {
            $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 10
            if ($resp.StatusCode -ne 200) {
                $errors += "falhas health [$healthUrl]: status $($resp.StatusCode)"
            } elseif ($Log) {
                & $Log "smoke OK: $healthUrl"
            }
        } catch {
            $errors += "falhas health [$healthUrl]: $($_.Exception.Message)"
        }

        $meUrl = "$base/api/v1/falhas/me/"
        try {
            $resp = Invoke-WebRequest -Uri $meUrl -UseBasicParsing -TimeoutSec 10
            if ($resp.StatusCode -eq 404) {
                $errors += "falhas api [$meUrl]: rota ausente (404)"
            } elseif ($Log) {
                & $Log "smoke OK: $meUrl (status $($resp.StatusCode))"
            }
        } catch {
            $statusCode = $null
            if ($_.Exception.Response) {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }
            if ($statusCode -eq 404) {
                $errors += "falhas api [$meUrl]: rota ausente (404)"
            } elseif ($statusCode -in 401, 403) {
                if ($Log) { & $Log "smoke OK: $meUrl (status $statusCode, rota registrada)" }
            } else {
                $errors += "falhas api [$meUrl]: $($_.Exception.Message)"
            }
        }
    }

    if ($errors.Count -gt 0) {
        throw ($errors -join "; ")
    }
}
