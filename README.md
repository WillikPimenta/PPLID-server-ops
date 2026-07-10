# PPLID Server Ops

Scripts de automacao do servidor PPLID (sync Git, deploy, ops-console, Task Scheduler).
Independente de usuario Windows — usa `C:\PPLID\` como base da maquina.

## Estrutura

```
C:\PPLID\
├── machine.config.json   # config local (nao versionada)
├── repos\                # PPLID_MAIN, PPLID_DEV, PPLID_HOM
├── logs\
└── ops\                  # este repositorio
```

## Setup inicial

Execute **como Administrador** na maquina servidor:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -MigrateLegacy
```

Opcoes uteis:

- `-LanIp 10.x.x.x` — IP LAN fixo (senao auto-detect)
- `-SkipClone` — nao clona repos (apos migracao)
- `-SkipTasks` — nao registra Task Scheduler

## Configuracao minima

Edite `C:\PPLID\machine.config.json`:

```json
{
  "baseDir": "C:\\PPLID",
  "lanIp": "10.97.198.186"
}
```

## Comandos

| Acao | Comando |
|------|---------|
| Iniciar ops-console | `C:\PPLID\ops\start_ops_console.ps1` |
| Deploy manual (3 envs) | `C:\PPLID\ops\deploy_all.ps1` |
| Sync git (3 envs) | `C:\PPLID\ops\update_all.ps1` |
| Task sync (boot) | `C:\PPLID\ops\install_scheduled_task.ps1` |
| Task console (boot) | `C:\PPLID\ops\install_ops_console_task.ps1` |
| Kiosk Edge | `C:\PPLID\ops\install_ops_kiosk.ps1 -SkipUserCreation` |

Tasks registradas como **SYSTEM** quando executadas como Admin (funcionam sem login).

## Desinstalar tasks

```powershell
.\install_scheduled_task.ps1 -Uninstall
.\install_ops_console_task.ps1 -Uninstall
```
