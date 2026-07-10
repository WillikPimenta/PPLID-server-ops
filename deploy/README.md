# Pipeline de deploy Railway-like

## Portas por ambiente

| Ambiente | Branch | Backend | Frontend | PostgreSQL |
|----------|--------|---------|----------|------------|
| MAIN     | main   | **8000** | 5173 | pplid_main |
| DEV      | dev    | **8001** | 5174 | pplid_dev  |
| HOM      | hom    | **8002** | 5175 | pplid_hom  |

Modulos Django servidos fora do Vue (ex.: `/falhas/`) devem ser acessados na **porta do backend** do ambiente correto.

## Estrutura

```
C:\PPLID\deploy\{MAIN|DEV|HOM}\
  mirror\           # clone git (fetch only)
  staging\          # area temporaria
  releases\{sha}\   # artefato imutavel pos-build
  current\          # junction -> release ativa
  previous\         # junction -> ultima boa
  logs\runs\{runId}\
  deploy-state.json
```

## Fluxo

1. `watch_github.ps1` — compara `origin/{branch}` com `activeSha`
2. `deploy_pipeline.ps1` — orquestra build → validate → promote
3. Falha antes de `promote_release.ps1` **nao** para producao

### Deploy com mudancas no backend

Quando `git diff` detecta alteracoes em `backend/` ou `scripts/deploy/`:

1. **build_staging.ps1** — invalida `meta.built` e forca rebuild; grava `backendChanged` em `meta.json`
2. **validate_staging.ps1** — `manage.py check --deploy` obrigatorio + `migrate --plan` (somente leitura)
3. **promote_release.ps1** — `migrate --noinput` + `migrate --check` + `collectstatic` + restart
4. **health_check.ps1** — `/api/v1/health/` e, se existir `apps/falhas_criticas`, `/falhas/health/`

Funcoes compartilhadas em `ops/deploy/lib/backend_deploy.ps1`.

Deploy apenas de frontend ainda executa migrate no promote (idempotente) e health basico.

## Comandos

```powershell
# Watcher (uma vez)
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\watch_github.ps1 -Environment DEV

# Primeira release a partir do last good (migracao)
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\bootstrap_first_release.ps1 -Environment DEV

# Bootstrap (logon)
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\bootstrap_all.ps1

# Recovery
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\recover_stuck_deploy.ps1 -Environment DEV -Force

# Rollback manual
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\rollback_release.ps1 -Environment DEV -RunId manual

# Retencao automatica de releases (max 5 por ambiente; protege current/previous/state)
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\prune_releases.ps1 -Environment DEV
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\prune_releases.ps1 -Environment DEV -DryRun

# Limpar release especifica (exige -Sha)
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\cleanup_release.ps1 -Environment DEV -Sha abc1234

# Validacao
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\test_pipeline_lib_scope.ps1
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\test_frontend_start.ps1 -TargetEnvironment HOM
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\validate_deploy.ps1
```

## Tasks

- `PPLID-GitHub-Sync` → `wscript` → `run_update_hidden.vbs` → `watch_all.ps1`
- `PPLID-Deploy-OnLogon` → `bootstrap_all.ps1` (delay **5 min** apos logon)

## Troubleshooting

### `Preparing worktree (detached HEAD ...)` no PowerShell 5.1

Git escreve progresso em stderr. PowerShell 5.1 pode tratar isso como erro nativo. O pipeline usa `Invoke-PplidGit` (`deploy/lib/git_invoke.ps1`) com `$ErrorActionPreference = SilentlyContinue` e valida apenas `$LASTEXITCODE`.

### `activeSha` nulo apos recovery

Execute:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\recover_stuck_deploy.ps1 -Environment DEV -Force
```

O script restaura `activeSha`/`lastGoodSha` de `deploy-status.json`, `PPLID_DEV.deployed.json` ou `current/meta.json` (junction ativa).

### `deploy-state.json invalido` repetido no log

PowerShell 5.1 lanca erro ao iterar `$state.Keys` em `[ordered]@{}` enquanto o hashtable e modificado. Corrigido em `deploy/lib/deploy_state.ps1` (`@($state.Keys)`). Se o estado foi zerado, rode `recover_stuck_deploy.ps1 -Force`.

### Health falha pos-promote (frontend lento)

`vite preview` pode demorar > 30s apos restart. `Start-PplidFrontend` aguarda a porta (timeout 30s). `promote_release.ps1` aguarda ate 8 tentativas (10s entre cada) antes de rollback.

### Frontend X vermelho na ops-console

Sintoma: drawer mostra Backend OK mas Frontend com ✗ na porta do ambiente (ex.: HOM `:5175`).

1. Confirme se a porta escuta: `. C:\PPLID\ops\lib\port_utils.ps1; Test-PortListening -Port 5175` (ajuste a porta).
2. Leia `C:\PPLID\logs\PPLID_HOM.frontend.out.log` (substitua HOM pelo ambiente).
3. Causas comuns:
   - **`Ok to proceed? (y)` no log** — `start_env.ps1` antigo usava `npx vite` (prompt interativo). Corrigido: usa `node_modules\.bin\vite.cmd` local via `Start-PplidFrontend`.
   - **OOM Node (`Fatal process out of memory`)** — reinicie só o frontend ou todo o ambiente; monitore memoria.
   - **Proxy ECONNREFUSED** — backend down na porta esperada; suba backend primeiro.
4. Restart isolado (nao reinicia backend):
   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\PPLID\repos\PPLID_HOM\scripts\deploy\start_frontend.ps1 -Environment HOM
   ```
   Na ops-console: **Reiniciar Frontend** chama `start_frontend.ps1` (nao mais `start_env` completo).
5. Logs de runtime na console: drawer **Logs** → sub-abas Frontend / Backend / Deploy, ou API `GET /api/v1/service-logs/HOM?service=frontend&lines=200`.
6. Smoke de subida:
   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\test_frontend_start.ps1 -TargetEnvironment HOM
   ```

### Bootstrap vs sync em paralelo

`bootstrap_all.ps1` adia execucao se `Global\PPLID-GitHub-Sync` estiver ativo. `bootstrap_env.ps1` respeita `Enter-DeployLock` por ambiente.

### 404 em `/falhas/` apos deploy

1. Confirme o **ambiente e a porta**: DEV usa `:8001`, MAIN usa `:8000` (branch `main` ainda nao inclui o modulo).
2. Teste direto: `http://127.0.0.1:8001/falhas/health/` (DEV).
3. O frontend usa `frontend/.env` (gerado por `sync_env_files.ps1`) e `frontend/.env.[mode]` para proxy/portas no `npm run dev` (`--mode main|dev|hom`).
4. Se o deploy concluiu mas a rota falha, verifique `promote.log` (`migrate --check`) e `health_check` (`falhas` smoke).

Validacao manual completa do modulo:

```powershell
cd C:\PPLID\repos\PPLID_DEV\backend
$env:PPLID_BACKEND_URL = "http://127.0.0.1:8001"
.\.venv\Scripts\python.exe scripts\validate_falhas_http.py --base-url http://127.0.0.1:8001
```

### Promote cross-env (DEV → HOM)

Checklist antes de promover:

1. DEV online com SHA desejado (`deploy-state.json` → `activeSha`).
2. HOM idle (`status` ≠ building/validating/promoting).
3. Commit existe no mirror HOM: `git -C deploy\HOM\mirror rev-parse <sha>`.
4. Smoke do lib no escopo correto:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\test_pipeline_lib_scope.ps1 -TargetEnvironment HOM
```

Promote manual (copia artefatos de DEV via `PPLID_PROMOTE_SOURCE`):

```powershell
$env:PPLID_PROMOTE_SOURCE = "DEV"
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy\deploy_pipeline.ps1 `
  -Environment HOM -TargetSha <sha> -TargetShaFull <sha_full> -Trigger manual-promote
```

Ou use o botao **Promover → HOM** no drawer DEV da ops-console.

### `Get-CommitStatusUpdates` nao reconhecido na Preparacao

Causa: dot-source de `lib.ps1` **dentro de uma funcao** PowerShell — funcoes carregadas somem ao retornar.

Correcao: `deploy_pipeline.ps1` carrega `lib.ps1` no **escopo do script** (via `Get-PipelineDeployLibPath` + `. $libPath` antes do `try`).

Nunca dot-source `lib.ps1` dentro de funcoes auxiliares do pipeline.

## Matriz de testes (T1–T9)

| Teste | Comando / acao | Pass |
|-------|----------------|------|
| T1 | `watch_github.ps1 -Environment DEV` | `releases/{sha}/meta.json`, `current` atualizado |
| T2 | `verify_stack.ps1`, `validate_deploy.ps1` | exit 0, health OK |
| T3 | Console `/api/v1/overview` | DEV online, F/B/DB OK |
| T4 | Repetir watcher | log `noop`, sem rebuild |
| T5 | Falha simulada em validate | `current` preservado |
| T6 | Dois watchers simultaneos | segundo skip (lock) |
| T7 | Task sync manual + 2 ciclos | lock adquirido/liberado |
| T8 | Reboot + logon + 7 min | stack UP (manual) |
| T9 | `recover_stuck_deploy.ps1 -Force` | `activeSha` preservado |

Resultados auditaveis: `C:\PPLID\logs\audit_*\test_results.md`
