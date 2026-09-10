# Automações PPLID (Bots BRFlow)

Pacote Python de automação integrado ao projeto **PPLID**. A interface principal é a central em **Planejamento → Automação** (`/planejamento/automacao`) na intranet Vue.

O painel Flask (`run.py`) permanece disponível para debug local.

## Instalação (PPLID)

```bash
# Na raiz do repositório PPLID
pip install -e ./automacoes
cd backend
pip install -r requirements.txt
```

A API Django expõe os endpoints em `/api/v1/automacoes/*` (requer login + equipe Planejamento).

## Estrutura

```
PPLIDBOTS/
├─ app/
│  ├─ bots/                 # Módulos de automação (Selenium, CSV, etc.)
│  ├─ config/               # Configuração centralizada (paths, selectors, env)
│  ├─ core/                 # Utilitários compartilhados (callbacks, credentials)
│  ├─ infrastructure/       # Selenium, CSV, Teams, diagnostics
│  ├─ orchestration/        # robot_runner, robots_wrapper
│  ├─ scripts/              # Scripts de manutenção
│  ├─ services/             # robot_manager (Flask)
│  ├─ static/ + templates/  # Interface web
│  ├─ routes.py
│  └─ __init__.py
├─ docs/                    # Documentação técnica
├─ tests/
├─ archive/deprecated/
├─ data/escala/             # CSV escala auditores
├─ requirements.txt
├─ pyproject.toml
├─ .env.example
└─ run.py
```

## Como executar

1. Criar e ativar ambiente virtual.
2. Instalar dependências:

```bash
pip install -e ".[dev]"
```

3. Copiar variáveis de ambiente (opcional):

```bash
copy .env.example .env
```

4. Rodar o servidor:

```bash
python run.py
# ou: sistematest-web
```

**Segundo plano (sem CMD aberto)** — se `.bat`, PowerShell e `.vbs` forem bloqueados:

### A) Pelo Cursor (recomendado)

1. `Ctrl+Shift+P` → **Tasks: Run Task**
2. Escolha **Web: iniciar em background**
3. Pode fechar o terminal depois; o servidor continua rodando.

Para parar: **Tasks: Run Task** → **Web: parar background**.

Ou no terminal integrado:

```bash
python web_background.py start
python web_background.py stop
```

### B) Duplo clique em `.pyw` (sem janela de CMD)

- **Iniciar:** `Iniciar-Web.pyw`
- **Parar:** `Parar-Web.pyw`

Arquivo `.pyw` é executado pelo `pythonw.exe` (associacao do Windows), nao por script host.

### C) Atalho na area de trabalho

Se `.pyw` tambem for bloqueado, crie um atalho manualmente:

- **Destino:** `"C:\Program Files\Python312\pythonw.exe" "C:\Users\c93123a\PPLIDBOTS\Iniciar-Web.pyw"`
- **Iniciar em:** pasta do projeto (`PPLIDBOTS`)

Log: `%APPDATA%\PLAN_IDF_SERASA_BOTS\logs\web-server.log`

5. Acessar no navegador:

```
http://127.0.0.1:5000
```

Variáveis: `FLASK_HOST`, `FLASK_PORT` (default 5000), `FLASK_DEBUG` (default false).

## Fluxo de execução

- Interface envia comando para API Flask.
- `RobotProcessManager` inicia um subprocesso por robô.
- Subprocesso executa `python -m app.orchestration.robot_runner --mode <modo>` na raiz do projeto.
- O runner importa `app.bots.*` e chama `start(settings=...)`.
- Logs são salvos em memória + arquivos em `%APPDATA%/PLAN_IDF_SERASA_BOTS/logs/`.

## Modos ativos

| Mode ID | Label na UI | Onde aparece |
|---------|-------------|--------------|
| `nivel` | Nível hierárquico | Grade principal |
| `monitor` | Monitor de eventos | Grade principal |
| `excel` | Alterações no BRFlow | Grade principal |
| `production` | Produção (H/H) | Grade principal |
| `onedrive` | Monitorar OneDrive | Grade principal |
| `rotina` | Rotina diária | Grade principal |
| `confer` | Confer (Eventos) | Grade principal |
| `ged` | GED | Grade principal |
| `replicacao_auditoria_d1` | **Replicação de Auditoria** | Grade principal |
| `falhas_criticas` | **Falhas Críticas (Power BI + Report)** | Grade principal |
| `produtividade_case` | **Produtividade Case Manager** | Grade principal (sem Okta) |
| `replicacao_auditoria` | Replicação de Auditoria (legado) | Seção **Legados** (colapsável) |
| `tray_ui` | Monitorar bandeja (OneDrive + Cisco) | Grade principal |

### Bot produtividade_case (DocumentDB → Excel)

Extrai relatórios de produtividade do **Case Manager** (Amazon DocumentDB) e grava Excel em pastas sincronizadas **IDF Docs - Relatórios** (hora, tempo logado, consolidado; fechamento do mês anterior nos dias civis 1–2).

- **Sem Okta** — credenciais só via `.env`: `DOCDB_HOST`, `DOCDB_USER`, `DOCDB_PASSWORD`, `DOCDB_TLS_CA_FILE` (caminho do PEM **fora** do repositório).
- Tarefas selecionáveis na aba **Opções** do painel Automação.
- Temp local (`PRODUTIVIDADE_CASE_TEMP_DIR` / `C:\relatorios_temp`) → `os.replace` para OneDrive.
- Após cada Excel: `PRODUTIVIDADE_CASE_SAVED|<report_type>|<path>` → fila → `ProductivityRecord` (`source=case`, etapa `Análise Visual - GA - Case Manager`, `stage_goal=320`).
- Snapshot da fila (sem `blockedDate`): `CASE_FILA_SAVED|<path.json>` → sync → `CaseFilaSnapshot` / `CaseFilaAgg` (portal Indicadores → Case Manager).
- `tempo_logado` gera só Excel (não entra no HxH).
- Sync BRFlow apaga apenas `source=brflow` (não remove Case).
- Migrate: `python manage.py migrate produtividade` + `migrate produtividade_case`.
- **Após migrate `0007`/`0008` (cadastro destino + resultado origem):** é necessário **resync do consolidado** (tarefa `consolidado` do bot + drain da fila) para preencher `cadastro_destino_at`, série dual (cadastrados × concluídos) e matriz origem×destino. Sem resync, cadastrados/matriz ficam vazios ou incompletos.

A rotina diária usa os relatórios e conferências do bot principal em `brflow-auditoria-replic-d1/`. O bot legado continua em `brflow-auditoria-replic/` apenas para execuções manuais.

### Bot tray_ui (bandeja OneDrive + Cisco)

Monitoramento da bandeja com templates em **`%APPDATA%\PLAN_IDF_SERASA_BOTS\imagens\{onedrive|cisco}`** (override: `TRAY_UI_IMAGENS_DIR`). Na primeira execução o bot faz **seed** copiando arquivos ausentes de `app/bots/templates/`. Em runtime **não** usa fallback do repositório nem ícones da pasta de instalação do OneDrive/Cisco.

**Cisco:** status via `vpncli.exe state` (fallback `status`); se Connected → OK; se Disconnected → recovery visual; se inconclusivo → match na bandeja. Override: `CISCO_VPNCLI_PATH`, timeout `CISCO_VPNCLI_TIMEOUT_SECONDS`.

**OneDrive:** continua visual + processo/sync (sem PS1 neste bot).

```powershell
python tools/test_tray_ui_live.py --install-status
python tools/test_tray_ui_live.py --service cisco
python tools/test_tray_ui_live.py --service onedrive
python tools/import_install_icons.py   # opcional: regenerar PNGs de seed a partir da instalação
```

Requisitos: PNGs em AppData (após seed); Cisco Secure Client com `vpncli.exe` para status nativo; ajuste `TRAY_SCAN_REGION` se o estado visual ficar `unknown`. Sync infinito (OneDrive): validação 5s por 3 min, depois `taskkill` + `OneDrive.exe /background`.

```powershell
python tools/test_sync_watch_live.py
python tools/test_sync_watch_live.py --window-seconds 60 --simulate-restart
```

- `TRAY_SCAN_REGION`, `TRAY_ICON_TARGET_HEIGHT` (default 48), `TRAY_BG_TOLERANCE` (default 25), `TRAY_MATCH_SCALES`
- `ONEDRIVE_UI_SYNC_STUCK_*`, `ONEDRIVE_SIMULATE_RESTART=1`
- `CISCO_VPNCLI_PATH`, `CISCO_VPNCLI_TIMEOUT_SECONDS`
- `CISCO_UI_RES_DIR` — **deprecado** para matching do bot (ainda usado só por `import_install_icons.py`)

Constantes: [`app/config/constants.py`](app/config/constants.py) (`ROBOT_MODES_PRIMARY`, `ROBOT_MODES_LEGACY`).

## Documentação

Índice: [`docs/README.md`](docs/README.md).

| Documento | Conteúdo |
|-----------|----------|
| [`docs/replicacao-aud-d1-meta-mensal-e-retencao-planos.md`](docs/replicacao-aud-d1-meta-mensal-e-retencao-planos.md) | Bot **Replicação de Auditoria** (D-1): meta mensal, redistribuição, retenção de planos, APIs |
| [`docs/replicacao-aud-d1-config-banco.md`](docs/replicacao-aud-d1-config-banco.md) | Configuração D-1 no PostgreSQL: importação, ativação da fonte, rollback e APIs de cadastro |
| [`docs/fluxo-bot-rotina.md`](docs/fluxo-bot-rotina.md) | Fluxo completo da rotina diária (inclui conferência de replicados D-1) |

## Rotina bruto → PostgreSQL

Os parquets **brutos** gerados pelo bot Rotina são importados para o PostgreSQL pelo app Django `apps.rotina_bruto` (backend PPLID). O CPF do detalhado é gravado como **hash** (nunca em texto claro no banco).

### Relatórios importados

| Tipo | Pasta OneDrive (Bots) | Arquivo |
|------|------------------------|---------|
| `detalhado` | `brflow-detalhado-bruto/` | `brflow-detalhado-bruto_YYYYMMDD.parquet` |
| `prod` | `brflow-prod-bruto/` | `brflow-prod-bruto_YYYYMMDD.parquet` |
| `monitor` | `brflow-monitor-tratado/` | `brflow-monitor-tratado_YYYYMMDD.parquet` |
| `confer_busca` | `confer-buscarpIrregularidade-tratado/` | `confer-buscarpIrregularidade-tratado_YYYYMM.csv` |
| `ged_detalhado` | `ged-detalhado-tratado/` | `ged-detalhado-tratado_YYYYMM.parquet` (ou `_N`) |
| `ged_irregularidade` | `ged-irregularidade-tratado/` | `ged-irregularidade-tratado_YYYYMM_N.csv` |

Pastas padrão em [`app/config/paths.py`](app/config/paths.py): `PASTA_DETALHADO_BRUTO`, `PASTA_PRODUTIVIDADE_D1_BRUTA`, `PASTA_MONITOR_TRATADO`, `PASTA_CONFER_BUSCAR_PROTOCOLO_TRATADO`, `PASTA_GED_TRATADO`, `PASTA_GED_IRREGULARIDADE_TRATADO`.

### Passo a passo — primeiro PC / carga inicial

1. **Instalar o pacote e o backend** (se ainda não fez):

```bash
pip install -e ./automacoes
cd backend
pip install -r requirements.txt
```

2. **Configurar pastas no `backend/.env`** (caminho local do OneDrive; ajuste o usuário):

```env
ROTINA_DETALHADO_BRUTO_DIR=C:\Users\SEU_USUARIO\OneDrive - EXPERIAN SERVICES CORP\Planejamento - IDF - Bases\Bots\brflow-detalhado-bruto
ROTINA_PROD_BRUTO_DIR=C:\Users\SEU_USUARIO\OneDrive - EXPERIAN SERVICES CORP\Planejamento - IDF - Bases\Bots\brflow-prod-bruto
ROTINA_MONITOR_TRATADO_DIR=C:\Users\SEU_USUARIO\OneDrive - EXPERIAN SERVICES CORP\Planejamento - IDF - Bases\Bots\brflow-monitor-tratado
ROTINA_CONFER_BUSCA_TRATADO_DIR=C:\Users\SEU_USUARIO\OneDrive - EXPERIAN SERVICES CORP\Planejamento - IDF - Bases\Bots\confer-buscarpIrregularidade-tratado
ROTINA_GED_DETALHADO_TRATADO_DIR=C:\Users\SEU_USUARIO\OneDrive - EXPERIAN SERVICES CORP\Planejamento - IDF - Bases\Bots\ged-detalhado-tratado
ROTINA_GED_IRREGULARIDADE_TRATADO_DIR=C:\Users\SEU_USUARIO\OneDrive - EXPERIAN SERVICES CORP\Planejamento - IDF - Bases\Bots\ged-irregularidade-tratado
```

3. **Aplicar migrations**:

```bash
cd backend
python manage.py migrate rotina_bruto
```

4. **Carga histórica** (todos os arquivos das seis pastas; leva ~20 min — não interrompa):

```bash
python manage.py sync_rotina_bruto --all-types --force
```

5. **Conferir** (opcional):

```bash
python manage.py shell -c "from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord, RotinaProdBrutoRecord, RotinaMonitorTratadoRecord; print(RotinaDetalhadoBrutoRecord.objects.count(), RotinaProdBrutoRecord.objects.count(), RotinaMonitorTratadoRecord.objects.count())"
```

### Passo a passo — outro PC (mesmo banco ou banco local)

1. `git pull` e dependências (`pip install -e ./automacoes`, `pip install -r backend/requirements.txt`).
2. `python manage.py migrate rotina_bruto`.
3. Ajustar os `ROTINA_*_DIR` no `backend/.env` (cada PC tem seu caminho OneDrive).
4. Se vários PCs apontarem para o **mesmo PostgreSQL**, use a **mesma `SECRET_KEY`** (hash de CPF fica idêntico).
5. Carga inicial nesse PC: `python manage.py sync_rotina_bruto --all-types --force`.

### Dia a dia (automático)

Após cada save do bot Rotina, o subprocesso emite no stdout:

```
ROTINA_BRUTO_SAVED|detalhado|C:\...\brflow-detalhado-bruto_YYYYMMDD.parquet
ROTINA_BRUTO_SAVED|prod|...
ROTINA_BRUTO_SAVED|monitor|...
ROTINA_BRUTO_SAVED|confer_busca|C:\...\confer-buscarpIrregularidade-tratado_YYYYMM.csv
ROTINA_BRUTO_SAVED|ged_detalhado|C:\...\ged-detalhado-tratado_YYYYMM.parquet
ROTINA_BRUTO_SAVED|ged_irregularidade|C:\...\ged-irregularidade-tratado_YYYYMM_N.csv
```

O `RobotProcessManager` repassa ao Django, que importa o arquivo com `force=True` (substitui a partição daquele dia/mês — reexecução no mesmo período não duplica linhas).

Pontos de emissão no código:

- [`app/bots/rotina/io.py`](app/bots/rotina/io.py) — detalhado bruto
- [`app/bots/rotina/tasks/produtividade.py`](app/bots/rotina/tasks/produtividade.py) — prod D-1 bruto
- [`app/bots/rotina/tasks/monitor.py`](app/bots/rotina/tasks/monitor.py) — monitor tratado
- [`app/bots/rotina/tasks/confer.py`](app/bots/rotina/tasks/confer.py) — confer busca protocolo (mensal)
- [`app/bots/rotina/tasks/irregularidade.py`](app/bots/rotina/tasks/irregularidade.py) — GED irregularidade (quinzena)
- [`app/bots/bot_ged.py`](app/bots/bot_ged.py) — GED detalhado (mensal/quinzena)

Requisito: backend Django rodando (portal) com `apps.rotina_bruto` e hook em [`backend/apps/automacoes/rotina_bruto_hooks.py`](../backend/apps/automacoes/rotina_bruto_hooks.py).

### Comandos manuais

```bash
cd backend

# Último arquivo de um tipo
python manage.py sync_rotina_bruto --type detalhado --force

# Todos os arquivos de um tipo
python manage.py sync_rotina_bruto --type prod --all --force

# Tudo (carga histórica)
python manage.py sync_rotina_bruto --all-types --force

# Filtrar por período
python manage.py sync_rotina_bruto --all-types --from-date 2026-06-01 --to-date 2026-06-18 --force
```

### Problemas comuns

**“Já existe uma sincronização de rotina bruto em andamento”** — backfill interrompido deixou log aberto. Feche e rode de novo:

```bash
python manage.py shell -c "
from django.utils import timezone
from apps.rotina_bruto.models import RotinaBrutoSyncLog
now = timezone.now()
for log in RotinaBrutoSyncLog.objects.filter(finished_at__isnull=True):
    log.finished_at = now
    log.success = False
    log.message = log.message or 'Sincronizacao interrompida.'
    log.save()
"
python manage.py sync_rotina_bruto --all-types --force
```

**Schema alterado** (novas migrations em `rotina_bruto`): `migrate` + `--all-types --force` para repopular.

**Backfill parcial** (só detalhado, prod/monitor zerados): rode `--all-types --force` de novo até o resumo final sem falhas.

## Monitor de eventos HxH (Produção)

O bot **Produção (H/H)** baixa o Monitor de Eventos BRFlow (**dia corrente**) após cada extração de produtividade e aplica o **mesmo tratamento da rotina** (`_pretratar_monitor_verifica_usuario` + `tratar_arquivo`), usando a produtividade do mesmo ciclo como referência.

Na engrenagem de configuração da Produção H/H, o campo **Dias para baixar do BRFlow** controla quantos dias são extraídos em cada ciclo, incluindo hoje. O padrão é 3 e o intervalo aceito é de 1 a 31 dias.

| Item | Valor |
|------|--------|
| Pasta OneDrive | `monitor-eventos-tratado/` |
| Arquivo | `monitor-eventos-tratado_YYYY-MM-DD.parquet` |
| Hook stdout | `MONITOR_EVENTOS_SAVED\|C:\...\monitor-eventos-tratado_YYYY-MM-DD.parquet` |
| Tabela PostgreSQL | `monitor_evento_record` (app `apps.monitor_eventos`) |
| Sync | Snapshot: truncate + reload (igual produtividade HxH) |

Constantes: [`app/config/paths.py`](app/config/paths.py) (`PASTA_MONITOR_EVENTOS_TRATADO`, `PREFIXO_MONITOR_EVENTOS_TRATADO`).

Configuração no `backend/.env`:

```env
MONITOR_EVENTOS_SOURCE_DIR=C:\Users\SEU_USUARIO\OneDrive - EXPERIAN SERVICES CORP\Planejamento - IDF - Bases\Bots\monitor-eventos-tratado
```

Migrations e sync manual:

```bash
cd backend
python manage.py migrate monitor_eventos
python manage.py sync_monitor_eventos --force
```

### Dia a dia (automático)

Após cada save do bot Produção, o subprocesso emite `MONITOR_EVENTOS_SAVED|...`. O Django importa com `force=True` via [`backend/apps/automacoes/monitor_eventos_hooks.py`](../backend/apps/automacoes/monitor_eventos_hooks.py).

Desativar o acoplamento: `"baixar_monitor_com_producao": false` na config do bot Produção.

### Diferença vs rotina D-1

| | Produção HxH | Rotina D-1 |
|---|--------------|------------|
| Fluxo Selenium | `monitor.py` (hoje) | `rotina/tasks/monitor.py` (D-1) |
| Pasta sync | `monitor-eventos-tratado/` | `brflow-monitor-tratado/` |
| Hook HxH / rotina | `MONITOR_EVENTOS_SAVED` | `ROTINA_BRUTO_SAVED\|monitor` (parquet tratado) |
| Tabela PostgreSQL | `monitor_evento_record` (tratado, snapshot HxH) | `rotina_monitor_tratado_record` (tratado, por dia) |
| Tratamento | `tratar_arquivo` (prod do mesmo ciclo) | `tratar_arquivo` (prod D-1) |

## Observações

- Código legado (ex.: rotina antiga) está em `archive/deprecated/`.
- Bot `replicacao_auditoria` (legado) permanece disponível na UI, mas não é usado pela rotina diária.
- `SERASA_ROBOS_SRC` está **deprecated** — emite warning se usado; prefira `pip install -e .`.
- Variáveis opcionais: ver [`.env.example`](.env.example).

## Calibração do progresso (% por tempo médio)

A barra de progresso na UI usa **`progress_profiles.json`**, gerado a partir dos logs reais de cada robô. Cada etapa (`PROGRESS|…|mensagem`) recebe um peso proporcional ao **tempo médio** observado nos logs, em vez de percentuais fixos no código.

### Passo a passo

1. **Execute os robôs normalmente** — os logs em `%APPDATA%/PLAN_IDF_SERASA_BOTS/logs/{modo}.log` acumulam linhas `PROGRESS|pct|mensagem` com timestamp.
2. **Gere o perfil** (na pasta `automacoes`):

```bash
python tools/build_progress_profiles.py
```

Opções úteis:

```bash
python tools/build_progress_profiles.py --logs-dir "C:\caminho\para\logs"
python tools/build_progress_profiles.py --output "C:\caminho\config\progress_profiles.json"
python tools/build_progress_profiles.py --modes nivel,monitor,rotina
```

3. **Reinicie o serviço de automações** (Flask/Django) para recarregar o JSON.
4. **Recalibre** após mudanças grandes de fluxo ou quando a barra voltar a parecer desproporcional.

O arquivo é gravado em [`PASTA_CONFIG/progress_profiles.json`](app/config/paths.py) (mesma pasta de `cycle_runs.json`). Com menos de 2 execuções no log, o script usa **pesos iguais** entre as etapas encontradas.

Implementação: [`app/services/progress_profiles.py`](app/services/progress_profiles.py), [`app/core/bot_runtime.py`](app/core/bot_runtime.py), script [`tools/build_progress_profiles.py`](tools/build_progress_profiles.py).
