# Agendador de Tarefas PPLID (GUI / Importar XML)

No servidor SERASA, `schtasks /Create` via PowerShell pode retornar **Acesso negado**, mas criar ou importar tasks pela **GUI** (`taskschd.msc`) funciona para o usuario logado (`SERASA\c92928a`). Use este guia como caminho principal **sem Admin**.

## As quatro tasks

| Nome canonico | XML em `ops/tasks/` | Quando roda | Script |
|---------------|---------------------|-------------|--------|
| `PPLID-GitHub-Sync` | [PPLID-GitHub-Sync.xml](tasks/PPLID-GitHub-Sync.xml) | A cada **2 min** (usuario logado) | `wscript` → `run_update_hidden.vbs` → `watch_all.ps1` (pipeline Railway-like) |
| `PPLID-Deploy-OnLogon` | [PPLID-Deploy-OnLogon.xml](tasks/PPLID-Deploy-OnLogon.xml) | Ao logon, **5 min depois** | `bootstrap_all.ps1` (sobe servicos sem rebuild) |
| `PPLID-Ops-Console` | [PPLID-Ops-Console.xml](tasks/PPLID-Ops-Console.xml) | Ao logon | `start_ops_console.ps1` |
| `PPLID-Main-Backup` | [PPLID-Main-Backup.xml](tasks/PPLID-Main-Backup.xml) | Diariamente as **02:00** (usuario logado) | `backup_main.ps1` |

Todas usam `InteractiveToken`, `LeastPrivilege` e `IgnoreNew` (nao sobrepoe execucoes). Sync, OnLogon e Main-Backup exigem **rede disponivel** (`RunOnlyIfNetworkAvailable`).

O backup tem limite de **4 horas** e, em caso de falha, faz ate **4 novas tentativas**, com intervalo de **30 minutos**. Como usa `InteractiveToken`, `c92928a` precisa estar conectado; o XML nao contem SID nem dominio fixos e o Agendador associa a conta durante a importacao. A acao usa `-Transport Rclone`, binario `C:\pplid\tools\rclone\rclone.exe`, configuracao `C:\pplid\config\rclone.conf` e remote `pplid-onedrive:`.

### Gates corporativos do backup MAIN

A task deve permanecer **desabilitada** ate todos os gates abaixo estarem concluidos:

1. Aplicativo/consentimento Entra aprovado para o tenant corporativo `EXPERIAN SERVICES CORP` e para o remote `pplid-onedrive:`.
2. `rclone.exe` presente no caminho canonico e `rclone.conf` provisionado para `pplid-onedrive:`.
3. Configuracao rclone sem token/segredo em texto aberto. O mecanismo aprovado e o arquivo de configuracao criptografado devem usar `rclone-config-encrypted-dpapi-password-command`, `credential-manager-password-command` ou `corporate-managed-token-provider`.
4. ACLs revisadas pela equipe responsavel. `Users`, `Authenticated Users` e `Everyone` nao podem ter `Write`, `Modify` ou controle equivalente em scripts, configuracao, `backend.env`, staging ou logs.
5. Preflight concluido sem erro:

   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\backup_main.ps1 -Transport Rclone -RcloneBinaryPath C:\pplid\tools\rclone\rclone.exe -RcloneConfigPath C:\pplid\config\rclone.conf -RcloneRemote pplid-onedrive: -ValidateOnly
   ```

6. Upload pequeno de homologacao concluido e baixado novamente, com conteudo/hash conferido.
7. Restore real de um dump de teste concluido em banco isolado.

Depois da aprovacao formal, a equipe responsavel deve criar
`C:\pplid\config\backup-approval.json` com a evidencia aprovada. O hash deve ser
o SHA-256 do binario homologado instalado em
`C:\pplid\tools\rclone\rclone.exe`:

```json
{
  "schemaVersion": 1,
  "approved": true,
  "tenant": "EXPERIAN SERVICES CORP",
  "remote": "pplid-onedrive:",
  "rcloneSha256": "<64 caracteres hexadecimais>",
  "approvedBy": "<equipe ou aprovador>",
  "approvalReference": "<chamado ou mudanca>",
  "approvedAtUtc": "<data ISO-8601>",
  "configProtection": {
    "approved": true,
    "mechanism": "rclone-config-encrypted-dpapi-password-command"
  }
}
```

O marker nao substitui a aprovacao: ele deve ser criado somente depois que a
evidencia corporativa existir. Qualquer troca do binario, tenant, remote ou
mecanismo de credencial exige nova verificacao e atualizacao formal do marker.

As ACLs devem ser corrigidas manualmente pela equipe de infraestrutura/seguranca
responsavel, seguindo a politica corporativa e preservando acesso para a conta de
operacao, administradores e `SYSTEM`. Este guia deliberadamente nao fornece
comando de alteracao de ACL.

Para auditar todos os gates sem registrar, habilitar ou alterar a task:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_backup_task.ps1 -ValidateActivation
```

O comando `-Enable` executa exatamente a mesma validacao antes de qualquer
chamada ao Agendador e recusa a ativacao se um gate falhar.

A retencao rclone deve continuar em **dry-run**, sem comandos de mutacao. O conjunto confirmado local deve ser preservado mesmo depois do upload; qualquer plano que proponha remocao remota ou exclusao da copia local bloqueia a habilitacao.

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

Para `PPLID-Main-Backup`, confirme especificamente a conta interativa `c92928a`, a execucao diaria as `02:00`, a opcao **Executar somente quando o usuario estiver conectado** e o estado **Desabilitada**. Nao habilite durante a importacao.

Os XMLs do repo **nao** trazem SID fixo; o Agendador associa ao usuario que importa.

**Encoding:** o Agendador exige **UTF-16 LE com BOM**. Se aparecer *"Formato de tarefa invalido"* / *"one root element"*, regenere os XMLs com:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\tasks\Repair-TaskXmls.ps1
```

Depois importe de novo em `taskschd.msc`.

Se somente o XML de backup precisar ser gerado ou reparado, preserve o conteudo versionado e regrave-o em UTF-16 LE com BOM:

```powershell
$xml = 'C:\PPLID\ops\tasks\PPLID-Main-Backup.xml'
$content = [System.IO.File]::ReadAllText($xml)
[System.IO.File]::WriteAllText($xml, $content, [System.Text.Encoding]::Unicode)
```

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

**Backup:** nao execute a task enquanto os gates corporativos estiverem pendentes. Faca preflight, upload pequeno e restore pelos procedimentos controlados acima; a task importada/registrada deve continuar desabilitada.

## Por que `schtasks /Create` falha e a GUI funciona?

`schtasks` e a GUI usam o **mesmo** Agendador de Tarefas. Em ambientes corporativos, politicas (GPO) costumam bloquear criacao via **linha de comando**, mas permitir via **interface grafica** para o mesmo usuario. Os scripts `install_*_task.ps1` tentam CLI primeiro; se falhar, use este guia.

## Scripts install (opcional)

```powershell
# Gera/copia XML e mostra caminho para importar na GUI
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_scheduled_task.ps1 -SkipSystemAccount -ExportXml
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_deploy_task.ps1 -SkipSystemAccount -ExportXml
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_backup_task.ps1 -ExportXml
```

Se `schtasks` retornar *Acesso negado*, ignore a mensagem de erro e importe o XML indicado.

O instalador de backup registra a task **desabilitada por padrao**:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_backup_task.ps1
```

Somente depois de concluir e registrar evidencias de todos os gates, habilite explicitamente:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_backup_task.ps1 -Enable
```

Esse comando registra novamente a definicao desabilitada e chama a habilitacao apenas porque `-Enable` foi informado. Se a GPO impedir o registro por CLI, importe o XML desabilitado e habilite manualmente pela GUI somente apos os mesmos gates.

## Apos reboot (validacao)

1. Faca logon como `c92928a`
2. Apos ~2 min: `deploy_all` deve subir os tres ambientes
3. Console deve abrir (task Ops-Console)
4. Sync continua a cada 2 min em background
5. Backup MAIN permanece desabilitado ate os gates; depois de `-Enable`, roda diariamente as 02:00 enquanto `c92928a` estiver conectado e houver rede

## Desinstalar

Apague cada task em `taskschd.msc` (clique direito → Excluir) ou:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_scheduled_task.ps1 -Uninstall
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_deploy_task.ps1 -Uninstall
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_ops_console_task.ps1 -Uninstall
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_backup_task.ps1 -Uninstall
```

(`-Uninstall` pode falhar com *Acesso negado* — use a GUI nesse caso.)
