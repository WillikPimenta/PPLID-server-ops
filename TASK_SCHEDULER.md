# Agendador de Tarefas PPLID (GUI / Importar XML)

No servidor SERASA, `schtasks /Create` via PowerShell pode retornar **Acesso negado**, mas criar ou importar tasks pela **GUI** (`taskschd.msc`) funciona para o usuario logado (`SERASA\c92928a`). Use este guia como caminho principal **sem Admin**.

## As tres tasks

| Nome canonico | XML em `ops/tasks/` | Quando roda | Script |
|---------------|---------------------|-------------|--------|
| `PPLID-GitHub-Sync` | [PPLID-GitHub-Sync.xml](tasks/PPLID-GitHub-Sync.xml) | A cada **2 min** (usuario logado) | `wscript` → `run_update_hidden.vbs` → `watch_all.ps1` (pipeline Railway-like) |
| `PPLID-Deploy-OnLogon` | [PPLID-Deploy-OnLogon.xml](tasks/PPLID-Deploy-OnLogon.xml) | Ao logon, **5 min depois** | `bootstrap_all.ps1` (sobe servicos sem rebuild) |
| `PPLID-Ops-Console` | [PPLID-Ops-Console.xml](tasks/PPLID-Ops-Console.xml) | Ao logon | `start_ops_console.ps1` |

Todas usam `InteractiveToken`, `LeastPrivilege` e `IgnoreNew` (nao sobrepoe execucoes). Sync e OnLogon exigem **rede disponivel** (`RunOnlyIfNetworkAvailable`).

## Pipeline de deploy (Railway-like)

- **Watcher** (`ops/deploy/watch_github.ps1`): `git fetch` no mirror, compara SHA remoto vs `deploy-state.json`.
- **Build** em `C:\PPLID\deploy\{ENV}\releases\{sha}` sem parar producao.
- **Promote** atualiza junction `current/` e reinicia servicos; falha de build nao derruba o ambiente.
- **Recovery** manual: `ops/recover_stuck_deploy.ps1 -Environment DEV -Force`
- **Validacao**: `ops/validate_deploy.ps1`

### Nomes legados (migrar)

Se ainda existirem tasks antigas, **desative ou apague** antes de importar as novas (evita sync/deploy duplicado):

| Legado | Substituir por |
|--------|----------------|
| `PPLID_GitHub_Sync` | `PPLID-GitHub-Sync` |
| `PPLIDG_GitHub_Sync` | `PPLID-GitHub-Sync` |
| `PPLID-Deploy-Logo` | `PPLID-Deploy-OnLogon` |
| `PPLID_Ops_Console` | `PPLID-Ops-Console` |

## Opcao A — Editar tasks existentes (mais rapido)

1. `Win + R` → `taskschd.msc`
2. **Biblioteca do Agendador de Tarefas** → localize a task

**PPLIDG_GitHub_Sync** (ou `PPLID-GitHub-Sync`):

- **Gatilhos** → Editar → Repetir tarefa a cada: **2 minutos**
- **Duracao**: Indefinidamente (ou o maximo permitido)
- **Configuracoes** → Se a tarefa ja estiver em execucao: **Nao iniciar uma nova instancia**

**PPLID-Deploy-Logo** (ou `PPLID-Deploy-OnLogon`):

- **Gatilhos** → Editar → Ao fazer logon → **Atrasar tarefa por: 5 minutos**
- **Acoes**: `powershell.exe ... C:\PPLID\ops\deploy_all.ps1`

**PPLID_Ops_Console** — geralmente ja esta correta; confira acao apontando para `start_ops_console.ps1`.

## Opcao B — Importar XML do repositorio

1. Abra `taskschd.msc`
2. Menu **Acao** → **Importar Tarefa...**
3. Selecione o XML em `C:\PPLID\ops\tasks\` (ex.: `PPLID-Deploy-OnLogon.xml`)
4. Na importacao:
   - Marque **Executar somente quando o usuario estiver conectado**
   - Confirme a conta **SERASA\c92928a** (ou seu usuario de operacao)
   - Informe a senha se solicitado
5. Se ja existir task com o mesmo nome: apague a antiga **antes** de importar

Os XMLs do repo **nao** trazem SID fixo; o Agendador associa ao usuario que importa.

**Encoding:** o Agendador exige **UTF-16 LE com BOM**. Se aparecer *"Formato de tarefa invalido"* / *"one root element"*, regenere os XMLs com:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\tasks\Repair-TaskXmls.ps1
```

Depois importe de novo em `taskschd.msc`.

## Exportar backup das tasks atuais

1. `taskschd.msc` → selecione a task
2. Menu **Acao** → **Exportar...**
3. Salve em local seguro (ex.: `Downloads\Tarefas\`)

## Testar sem reiniciar

1. **Deploy:** selecione `PPLID-Deploy-OnLogon` → clique direito → **Executar**
2. Aguarde alguns minutos
3. Verifique:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\verify_stack.ps1
```

Ou abra o Console de Operacoes e confira cards **Online** para MAIN/DEV/HOM.

**Sync:** execute `PPLID-GitHub-Sync` manualmente e confira `C:\PPLID\logs\update_all.log`.

## Por que `schtasks /Create` falha e a GUI funciona?

`schtasks` e a GUI usam o **mesmo** Agendador de Tarefas. Em ambientes corporativos, politicas (GPO) costumam bloquear criacao via **linha de comando**, mas permitir via **interface grafica** para o mesmo usuario. Os scripts `install_*_task.ps1` tentam CLI primeiro; se falhar, use este guia.

## Scripts install (opcional)

```powershell
# Gera/copia XML e mostra caminho para importar na GUI
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_scheduled_task.ps1 -SkipSystemAccount -ExportXml
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_deploy_task.ps1 -SkipSystemAccount -ExportXml
```

Se `schtasks` retornar *Acesso negado*, ignore a mensagem de erro e importe o XML indicado.

## Apos reboot (validacao)

1. Faca logon como `c92928a`
2. Apos ~2 min: `deploy_all` deve subir os tres ambientes
3. Console deve abrir (task Ops-Console)
4. Sync continua a cada 2 min em background

## Desinstalar

Apague cada task em `taskschd.msc` (clique direito → Excluir) ou:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_scheduled_task.ps1 -Uninstall
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_deploy_task.ps1 -Uninstall
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_ops_console_task.ps1 -Uninstall
```

(`-Uninstall` pode falhar com *Acesso negado* — use a GUI nesse caso.)
