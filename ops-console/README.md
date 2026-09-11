# Console de Operações PPLID

Painel web para monitorar os ambientes MAIN, DEV e HOM (deploy, branches, links e health em tempo real).

Este codigo vive em **PPLID-server-ops** (`C:\PPLID\ops\ops-console`), separado dos repos PPLID por branch.

## Acesso

- URL (LAN): `http://<lanIp>:5190` (definido em `../config/env.config.json`)
- URL (local): `http://localhost:5190`
- Modo kiosk (tela cheia): `http://localhost:5190/?kiosk=1`

## Iniciar manualmente

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\start_ops_console.ps1
```

Ou diretamente:

```powershell
cd C:\PPLID\ops\ops-console
python server.py C:\PPLID\ops\config\env.config.json
```

## Atualizar o console

Na **Visão geral**, use o botão **Verificar atualização**. O console consulta o repositório ops no GitHub; se houver nova versão, pede confirmação, executa `git pull`, reinstala dependências Python se necessário e reinicia automaticamente.

Alternativa manual:

```powershell
cd C:\PPLID\ops
git pull
powershell -ExecutionPolicy Bypass -File .\start_ops_console.ps1 -Restart
```

## Cópia local para desenvolvimento

No clone local deste repositório, use o modo isolado:

```powershell
cd C:\caminho\PPLID-server-ops
powershell -ExecutionPolicy Bypass -File .\start_ops_console.ps1 -Local
```

Para executar os bots usando um checkout local do PPLID, informe a pasta que
contém `backend` e `automacoes`:

```powershell
powershell -ExecutionPolicy Bypass -File .\start_ops_console.ps1 -Local `
  -PplidDir C:\Users\c93123a\PPLID -Restart
```

O launcher prepara automaticamente o **runtime nativo de automações**
(`automation-runtime/native-bundle` + `.venv` com Django/Selenium). Depois disso,
**Validar Okta** fica disponível em **Automações**. Publicar um bundle a partir de
MAIN/DEV/HOM continua opcional para congelar a versão de uma release específica.

O checkout local também precisa de um PostgreSQL acessível pelo
`config/machine.config.local.json` e pelos arquivos
`deploy\<AMBIENTE>\shared\backend.env` locais.

Na primeira execução, o launcher cria automaticamente:

- `config/env.config.local.json`, a partir do exemplo;
- `config/machine.config.local.json`;
- `.local/`, contendo logs, repositórios e runtime dos testes;
- `ops-console/.venv`, com as dependências Python do console;
- runtime nativo dos bots (código + `.venv` com requirements de `automation-native`).

O console local escuta somente em `127.0.0.1:5191`, portanto não conflita com a
instância do servidor (`5190`) e não expõe a máquina na rede. Acesse
`http://localhost:5191` (usuário inicial `admin1`, senha `admin`).

Essa instância é independente: ela monitora apenas os processos, arquivos e
bancos configurados no computador local. Para mudar a porta ou os ambientes,
edite `config/env.config.local.json`; para apontar os repositórios locais,
ajuste `baseDir` em `config/machine.config.local.json`.

## Bloqueio por senha

O console inicia **bloqueado** (`opsConsole.startLocked` em `env.config.json`). É preciso desbloquear para ver dados.

| Ação | Como |
|------|------|
| Desbloquear | Tela de bloqueio: usuário + senha |
| Bloquear de novo | Botão **Bloquear sessão** no header |
| Auto-bloqueio | Após `idleLockMinutes` sem atividade (padrão 15 min) |

### Autenticação

1. **Bootstrap (testes)** — se `bootstrapAuth.enabled: true`:
   - Usuário: `admin1`
   - Senha: `admin` (ou `OPS_BOOTSTRAP_PASSWORD` em `.env.local`)
   - Banner *Modo teste (bootstrap)* no canto da tela.

2. **Django (produção)** — valida contra o backend definido em `opsConsole.authEnv` (ex.: `MAIN` → porta 8000).

Ordem: tenta bootstrap primeiro; se falhar, tenta Django.

**Produção:** defina `bootstrapAuth.enabled: false` e use `OPS_SESSION_SECRET` forte em `.env.local` (copie de `.env.local.example`).

### APIs protegidas

`GET /api/v1/ha` retorna o estado sanitizado do no local, parceiro, testemunha,
fencing e papel PostgreSQL. O painel inclui esse resumo na tela principal.

Sem sessão desbloqueada, `GET /api/v1/overview`, commits e logs retornam **401**.

| Rota | Descrição |
|------|-----------|
| `GET /api/v1/auth/status` | Estado locked / usuário / idle |
| `POST /api/v1/auth/unlock` | Body JSON `{ "username", "password" }` |
| `POST /api/v1/auth/lock` | Bloqueia (cookie com `locked: true`) |
| `GET /api/v1/overview` | Status agregado (requer desbloqueio) |
| `GET /api/v1/commits/{MAIN\|DEV\|HOM}?sha=` | Detalhes Git + logs |
| `GET /api/v1/logs/{MAIN\|DEV\|HOM}?lines=80` | Últimas linhas do log de deploy |
| `GET /api/v1/database/{ENV}` | Métricas Postgres |
| `GET /api/v1/env/{ENV}` | Variáveis de ambiente (segredos mascarados) |
| `GET /api/v1/host/summary` | CPU, RAM, discos, rede, GPU e processos PPLID do computador |
| `GET /api/v1/host/series?metric=&hours=` | Série histórica de uma métrica do host (máx. 7 dias/360 pontos) |
| `GET /api/v1/diagnostics/snapshot` | Baixa diagnóstico JSON sanitizado |
| `POST /api/v1/actions/rollback/{ENV}` | Rollback 1-clique |
| `POST /api/v1/actions/redeploy/{ENV}` | Re-deploy |
| `POST /api/v1/actions/restart/{ENV}` | Reinicia serviço |
| `POST /api/v1/actions/promote` | Promove SHA DEV → HOM |
| `POST /api/v1/actions/disable/{ENV}` | Desativa ambiente (para processos + pausa watch/deploy/probes) |
| `POST /api/v1/actions/enable/{ENV}` | Ativa ambiente (grava flag + inicia serviços) |

Cookie de sessão: `ops_session` (HttpOnly, assinado com `OPS_SESSION_SECRET`).

## Desempenho do computador

A rota **Host** coleta os recursos do computador uma única vez a cada 15 segundos, independentemente do número de ambientes ou navegadores conectados. As séries usam o mesmo SQLite do monitoramento, sob o alvo lógico `HOST`, e são retidas por 7 dias por padrão.

- CPU total e por núcleo, RAM, memória virtual comprometida (commit/limite), pagefile, discos e tráfego de rede.
- Maiores consumidores de commit privado por processo, para identificar vazamentos antes do esgotamento do host.
- Processos PPLID agrupados por ambiente/serviço, sem expor linha de comando ou variáveis.
- NVIDIA via `nvidia-smi` (uso, VRAM, temperatura e potência); outros drivers recebem inventário e indicam métricas não disponibilizadas.
- Alertas sustentados de CPU/RAM/VRAM, limites de disco e temperatura, com recuperação e deduplicação.
- O console continua operando em modo degradado se a telemetria ou o driver de GPU não estiver disponível.

Configuração em `machine.config.json`:

```json
{
  "hostMonitoring": {
    "enabled": true,
    "intervalSec": 15,
    "retentionDays": 7,
    "processLimit": 12,
    "gpuEnabled": true,
    "thresholds": {
      "cpuWarnPct": 85,
      "cpuCriticalPct": 95,
      "memoryWarnPct": 85,
      "memoryCriticalPct": 95,
      "commitWarnPct": 85,
      "commitCriticalPct": 95,
      "diskWarnFreePct": 15,
      "diskCriticalFreePct": 8,
      "gpuMemoryWarnPct": 90,
      "gpuTemperatureWarnC": 80,
      "gpuTemperatureCriticalC": 90
    }
  }
}
```

`start_ops_console.ps1` cria `ops-console/.venv`, instala `requirements.txt` do
console e prepara o runtime nativo de automações (código + `.venv` dos bots)
quando necessário. O console não depende mais do ambiente Python de DEV ou MAIN.

Os módulos de Host, monitoramento, banco, configuração e drawers são carregados sob demanda. A Visão Geral inicia somente com o núcleo da interface (aproximadamente 110 KB de JavaScript não comprimido); acompanhamento detalhado de deploy é pré-carregado automaticamente quando há pipeline ativo.

## Ativar / desativar ambientes

Cada card do overview tem **Desativar** / **Ativar** (painel Config e drawer). O estado fica em `ops/config/env.config.json` no bloco do ambiente:

```json
"DEV": {
  "enabled": false,
  ...
}
```

- Ausência de `enabled` = ligado (compatível com configs antigas).
- **Desativar**: `stop_env.ps1` + `enabled: false`. O Sync (`watch_all`), bootstrap, health probes e monitoring **não** tocam mais nesse ambiente — evita carga desnecessária.
- **Ativar**: `enabled: true` + `start_env.ps1`.
- Ações de deploy/restart/promote em ambiente desativado são rejeitadas pela API.
- Não é possível desativar o `opsConsole.authEnv` (padrão MAIN) se `bootstrapAuth.enabled` estiver `false` (evita lockout do console).

## Modo kiosk Windows

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\install_ops_kiosk.ps1 -SkipUserCreation
```

Desinstalar:

```powershell
powershell -ExecutionPolicy Bypass -File C:\PPLID\ops\uninstall_ops_kiosk.ps1
```

## Testes

```powershell
cd C:\PPLID\ops\ops-console
python -m pytest tests/
```

## Automações resilientes (piloto)

A rota `/automations` opera os bots **Produção (H/H)** e **Rotina diária** fora da
árvore de releases. O ops-console é autossuficiente: no start/restart ele
sincroniza `ops-console/automation-native` para o runtime nativo e instala as
dependências dos bots quando o `.venv` falta ou os `requirements.txt` mudam.

Fluxo típico:

1. Abrir **Automações** e confirmar o badge **Runtime pronto**
2. Escolher até 2 bancos de destino
3. Validar credenciais Okta (headless por padrão no servidor)
4. Iniciar o bot

Publicar um runtime a partir de MAIN/DEV/HOM continua disponível via API
(`POST /api/v1/automations/runtime/publish`) para congelar a versão de uma
release específica. Sem publicação, o console usa o bundle nativo `ops-native`.

Cada publicação cria um bundle imutável em
`C:\PPLID\ops\data\automation-runtime\bundles`. O supervisor recebe a marca
`--pplid-supervised`, portanto a limpeza de órfãos do deploy preserva a execução.
Banco e versão ficam congelados no início do run; mudar o seletor afeta apenas
execuções futuras. Publicação e rollback ficam bloqueados enquanto um bot do
piloto estiver ativo.

Credenciais são transmitidas apenas pelo ambiente do processo e não são gravadas
em configurações, estado, logs ou auditoria. O diretório pode ser sobrescrito em
`machine.config.json` por `automationRuntime.root`. Use
`automationRuntime.oktaHeadless` (padrão `true`) para controlar o Chrome da
validação Okta no servidor.

Arquivos `.env` nunca são copiados para os bundles. O supervisor carrega o arquivo
autoritativo `deploy\<ENV>\shared\backend.env` do banco congelado no início do run.

O arquivo `deploy-status.json` (em `C:\PPLID\logs\`) é atualizado pelos scripts de deploy e sync. O console cruza com `/api/v1/health/` de cada ambiente.
