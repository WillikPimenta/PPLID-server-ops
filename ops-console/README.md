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

`start_ops_console.ps1` cria `ops-console/.venv` e instala `requirements.txt` na primeira execução. O console não depende mais do ambiente Python de DEV ou MAIN.

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

## Dados

O arquivo `deploy-status.json` (em `C:\PPLID\logs\`) é atualizado pelos scripts de deploy e sync. O console cruza com `/api/v1/health/` de cada ambiente.
