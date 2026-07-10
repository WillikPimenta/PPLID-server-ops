# PPLID Server Ops

Scripts de automacao do servidor PPLID (sync Git, deploy, ops-console, Task Scheduler).
Usa `C:\PPLID\` como base da maquina.

## Estrutura

```
C:\PPLID\
├── machine.config.json   # config local (nao versionada)
├── repos\                # PPLID_MAIN, PPLID_DEV, PPLID_HOM
├── logs\
└── ops\                  # este repositorio (PPLID-server-ops)
    ├── ops-console\      # painel web de operacao (porta 5190)
    ├── config\
    │   └── env.config.json   # portas, ambientes, opsConsole
    └── tasks\            # XML para importar no Agendador de Tarefas (GUI)
```

## Setup inicial

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -MigrateLegacy
```

Opcoes uteis:

- `-LanIp 10.x.x.x` — IP LAN fixo (senao auto-detect)
- `-SkipClone` — nao clona repos (apos migracao)
- `-SkipTasks` / `-SkipSyncTask` — nao registra Task Scheduler de sync

O setup registra sync Git no **usuario logado** (`-SkipSystemAccount`), a cada 2 min, com protecao contra execucoes paralelas (mutex + `IgnoreNew` na task). Ops-console e **manual**.

## Configuracao minima

Edite `C:\PPLID\machine.config.json`:

```json
{
  "baseDir": "C:\\PPLID",
  "lanIp": "10.97.198.186"
}
```

Portas e ambientes ficam em `ops/config/env.config.json` (versionado neste repo).
Opcionalmente sobrescreva paths em `machine.config.json`:

```json
{
  "envConfigPath": "C:\\PPLID\\ops\\config\\env.config.json",
  "opsConsoleDir": "C:\\PPLID\\ops\\ops-console"
}
```

Segredos do console (`OPS_SESSION_SECRET`, senha bootstrap) vao em `ops-console/.env.local` (nao versionado).

## Comandos

| Acao | Comando |
|------|---------|
| Iniciar ops-console | `C:\PPLID\ops\start_ops_console.ps1` |
| Deploy manual (3 envs) | `C:\PPLID\ops\deploy_all.ps1` |
| Sync git (3 envs) | `C:\PPLID\ops\update_all.ps1` |
| Atualizar ops-console | `git pull` em `C:\PPLID\ops` + reiniciar console |
| Verificar stack | `C:\PPLID\ops\verify_stack.ps1` |
| Parar tudo | `C:\PPLID\ops\stop_all.ps1` |
| Validar ciclo completo | `C:\PPLID\ops\validate_ops_cycle.ps1` |
| Task sync (2 min) | `install_scheduled_task.ps1 -SkipSystemAccount` ou importar `tasks\PPLID-GitHub-Sync.xml` |
| Task deploy (ao logon + 2 min) | `install_deploy_task.ps1 -SkipSystemAccount` ou importar `tasks\PPLID-Deploy-OnLogon.xml` |
| Task console (ao login) | `install_ops_console_task.ps1 -SkipSystemAccount -OnLogon` ou importar `tasks\PPLID-Ops-Console.xml` |
| Ver XML / guia GUI | `install_scheduled_task.ps1 -ExportXml` — ver [TASK_SCHEDULER.md](TASK_SCHEDULER.md) |
| Kiosk Edge | `C:\PPLID\ops\install_ops_kiosk.ps1 -SkipUserCreation` |

## Tasks via GUI (sem Admin) — caminho recomendado

Se `schtasks /Create` retornar **Acesso negado**, use o **Agendador de Tarefas** (`taskschd.msc`):

1. **Acao** → **Importar Tarefa...**
2. Selecione um XML em `C:\PPLID\ops\tasks\`

| XML | Funcao |
|-----|--------|
| `PPLID-GitHub-Sync.xml` | Sync git a cada 2 min |
| `PPLID-Deploy-OnLogon.xml` | `deploy_all.ps1` 2 min apos logon |
| `PPLID-Ops-Console.xml` | Console ao logon |

Guia completo: **[TASK_SCHEDULER.md](TASK_SCHEDULER.md)** (editar tasks existentes, testar, migrar nomes legados).

Nomes legados a substituir: `PPLIDG_GitHub_Sync` → `PPLID-GitHub-Sync`, `PPLID-Deploy-Logo` → `PPLID-Deploy-OnLogon`, `PPLID_Ops_Console` → `PPLID-Ops-Console`.

Scripts `install_*_task.ps1` tentam CLI primeiro; em falha, exibem o caminho do XML para importar na GUI.

## Operacao sem admin

Fluxo diario (nao exige privilegio elevado):

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\deploy_all.ps1
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\start_ops_console.ps1
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\verify_stack.ps1
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\stop_all.ps1
```

Validacao automatizada do ciclo (stop → deploy → console → verify → stop), com timeout por etapa:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\validate_ops_cycle.ps1
```

Use `-SkipFinalStop` para manter os servicos rodando apos a validacao.

**Nota:** scripts de porta usam `netstat` via `ops/lib/port_utils.ps1` — evite `Get-NetTCPConnection` e `Test-NetConnection` neste host (podem travar).

### Ops Store (logs SQLite)

Logs de deploy, runtime e audit vao para `C:\PPLID\ops\data\ops-store.db` (WAL). Configuracao opcional em `machine.config.json`:

```json
{
  "opsStore": {
    "path": "C:\\PPLID\\ops\\data\\ops-store.db",
    "retentionDays": 90,
    "mirrorFileLogs": true
  }
}
```

| Acao | Comando |
|------|---------|
| Inicializar store | `powershell -File C:\PPLID\ops\setup.ps1` (ou `Initialize-OpsStore` via ops_store.ps1) |
| Migrar logs historicos | `powershell -File C:\PPLID\ops\migrate_logs_to_sqlite.ps1` |
| Testes store | `python C:\PPLID\ops\lib\test_ops_store.py` |

Com `mirrorFileLogs: false`, apenas SQLite e usado (elimina lock de arquivo no promote.log).

Auto-pull + deploy e boot apos login (preferir GUI — ver secao acima):

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_scheduled_task.ps1 -SkipSystemAccount -ExportXml
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_deploy_task.ps1 -SkipSystemAccount -ExportXml
```

Apos atualizar scripts ops, reimporte ou edite as tasks na GUI (intervalo 2 min, delay deploy 2 min).

### Protecao contra deploy perdido

O sync usa tres camadas:

1. **Mutex** em `update_all.ps1` — segunda execucao simultanea sai com exit 0 e log em `C:\PPLID\logs\update_all.log`.
2. **Task `IgnoreNew`** — Task Scheduler nao inicia nova instancia se a anterior ainda roda.
3. **Drift check** — se o repo no disco difere do SHA implantado (`deployedSha` / `.deployed.json`) ou `updatePending`, `update_repo.ps1` dispara deploy mesmo com "Sem alteracoes".

Deploy apos reboot: task `PPLID-Deploy-OnLogon` (ou `deploy_all.ps1` manual).

### Git em repos de outro usuario

Se `git fetch` falhar com *dubious ownership*, execute uma vez (ou peca ao TI):

```powershell
git config --global --add safe.directory C:/PPLID/repos/PPLID_MAIN
git config --global --add safe.directory C:/PPLID/repos/PPLID_DEV
git config --global --add safe.directory C:/PPLID/repos/PPLID_HOM
```

### Ticket para TI (remover tasks SYSTEM legadas)

Se instalacoes antigas registraram tasks como SYSTEM (boot/sync sem login), peca ao TI:

```powershell
schtasks /Delete /TN "PPLID-Ops-Console" /F
schtasks /Delete /TN "PPLID-GitHub-Sync" /F
```

Depois registre apenas a sync do usuario (comando acima).

## Desinstalar tasks

```powershell
.\install_scheduled_task.ps1 -Uninstall
.\install_deploy_task.ps1 -Uninstall
.\install_ops_console_task.ps1 -Uninstall
```

Se `-Uninstall` falhar com *Acesso negado*, exclua as tasks em `taskschd.msc`.
