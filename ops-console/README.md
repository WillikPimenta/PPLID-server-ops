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
| `POST /api/v1/actions/rollback/{ENV}` | Rollback 1-clique |
| `POST /api/v1/actions/redeploy/{ENV}` | Re-deploy |
| `POST /api/v1/actions/restart/{ENV}` | Reinicia serviço |
| `POST /api/v1/actions/promote` | Promove SHA DEV → HOM |

Cookie de sessão: `ops_session` (HttpOnly, assinado com `OPS_SESSION_SECRET`).

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
