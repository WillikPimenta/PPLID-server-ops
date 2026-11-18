# Health / OPS-console monitoring — Waitress & probe tunables

## Waitress (`scripts/deploy/lib.ps1` → `Start-PplidBackend`)

| Parameter | Default | Env override | Notes |
|-----------|---------|--------------|-------|
| `channel_timeout` | **90s** (was 600) | `PPLID_WAITRESS_CHANNEL_TIMEOUT` | Drop idle/stuck channels sooner; do not raise to mask saturation |
| `threads` | Waitress default (4) | `PPLID_WAITRESS_THREADS` | Raise only after measuring queue/latency/DB |
| `connection_limit` | Waitress default (100) | `PPLID_WAITRESS_CONNECTION_LIMIT` | Same — capacity is not the primary fix |
| `backlog` | Waitress default | `PPLID_WAITRESS_BACKLOG` | Optional |

Rollback: set `PPLID_WAITRESS_CHANNEL_TIMEOUT=600` before restart.

## OPS-console probe coordinator (`health_probe.py`)

| Setting | Default |
|---------|---------|
| success cache TTL | 5s |
| probe timeout | 3s |
| backoff initial / max | 5s / 60s |
| stale window | 120s |
| offline after N failures | 3 |
| global concurrency | 3 |

## Frontend polling (`public/js/utils.js` + `app.js`)

| Mode | Interval |
|------|----------|
| Normal | 5s (`LITE_REFRESH_MS`) |
| Deploy active | 2s |
| Hidden tab | paused |
| Overlap | coalesced (single in-flight) |

## Django health

| Route | Behavior |
|-------|----------|
| `GET /api/v1/health/live/` | O(1) liveness, no DB |
| `GET /api/v1/health/` / `ready/` | readiness (SELECT 1), no showmigrations |
| `GET /api/v1/health/deep/` | cached/async migrations diagnostics |
| `?deep=1` on `/health/` | compat: async cache only, never blocks Waitress |

## Migrations in OPS-console

`showmigrations` only via `server_ops.fetch_migrations_status` / `?migrations=1` on database metrics — not overview, overview-lite, or collector.

## Coleta e cobertura de disponibilidade

O health check possui um coletor separado das consultas de PostgreSQL, APIs e logs.
O padrão é uma sondagem por ambiente a cada 30 segundos, inclusive sem navegador
aberto. O agendamento usa relógio monotônico, desconta o tempo de execução e pula
horários perdidos sem criar rajadas de sondagens. MAIN, DEV e HOM são consultados
em paralelo, respeitando o limite global e o single-flight do coordenador.
Sondagens agendadas pedem um resultado novo em vez de receber stale-while-revalidate;
o cache e o backoff usados pela visão geral continuam funcionando.

Configure `monitoring.availabilityIntervalSec` em `machine.config.json` (mínimo 15,
padrão 30 segundos) e reinicie o console. `collectorIntervalSec` continua controlando
as outras categorias e não altera a validade das novas sondagens de disponibilidade.
Cada observação registra horário original, frequência e tolerância, em uma transação
SQLite para as quatro métricas. Respostas antigas não são regravadas como novas;
timeouts que reutilizam o último resultado válido não contam como sucesso.

A cobertura é `tempo observado / duração da janela`. O tempo sem cobertura é o
complemento desse mesmo cálculo. Uma amostra vale até a próxima sondagem ou até seu
intervalo registrado, o que vier primeiro. Apenas quando a próxima sondagem chega
com até 2 segundos de atraso essa pequena diferença é coberta. A tolerância não
estende a última amostra nem preenche sondagens perdidas. Falhas e sucessos têm a
mesma regra. Duração sem evidência continua desconhecida.

Amostras antigas sem metadados mantêm a referência de 60 segundos e tolerância zero;
lacunas históricas não são preenchidas. A nova fórmula pode alterar o percentual
anterior, que contava faixas ocupadas em vez de duração observada. A resposta informa
`health.coverageMethod = observed-duration-v2`. Não use a configuração atual para
recalcular a validade de amostras com frequência registrada.

As barras do detalhe têm largura temporal compatível com a frequência registrada.
Cada barra usa a mesma duração observada do total e informa cobertura no tooltip.
Hachuras indicam cobertura parcial; cinza indica ausência de observação, e vermelho
indica falhas. O painel mostra intervalo médio real, idade da última amostra e maior
lacuna no período. No detalhe histórico, a idade é relativa ao fim do intervalo.
O endpoint de diagnóstico do coletor também informa `availabilityCollector`, com
estado da thread, frequência e última gravação por ambiente.

## Sonda independente no mesmo host

Sem um segundo servidor, execute `tools/independent_availability_probe.py` em outro
processo no mesmo host, usando Python 3.10 ou superior. A sonda não depende do
processo ou banco do Ops Console e continua gravando durante uma parada do console.
Ela exige JSON com `status` igual a `ok`, `healthy` ou `degraded`,
além de HTTP 2xx; páginas HTML de login não contam como sucesso.

Exemplo PowerShell, substituindo os caminhos e a porta pelos do ambiente:

```powershell
python .\ops-console\tools\independent_availability_probe.py --url http://127.0.0.1:8000/api/v1/health/ready/ --name MAIN --output-dir C:\monitor\evidence --once
python .\ops-console\tools\independent_availability_probe.py --url http://127.0.0.1:8000/api/v1/health/ready/ --name MAIN --output-dir C:\monitor\evidence --interval 30 --timeout 5
```

Valide primeiro com `--once` (saída 0: sucesso; 1: falha; 2: argumentos inválidos).
Para instalar no Agendador de Tarefas do Windows, execute em PowerShell administrador:

```powershell
.\ops-console\tools\Register-AvailabilityProbe.ps1 -Url http://127.0.0.1:8000/api/v1/health/ready/ -EnvironmentName MAIN -PythonPath C:\Python312\python.exe
```

O instalador copia a sonda para `%ProgramData%\PPLID\Availability`, registra a tarefa
`PPLID-Availability-MAIN` como SYSTEM, inicia a coleta e configura início no boot,
reinício após erro e proteção contra instâncias simultâneas. O Python deve estar
disponível para SYSTEM; use um caminho absoluto de instalação para todos os usuários.
O instalador recusa substituir tarefas existentes. Para outro ambiente, forneça outra
porta/nome; os arquivos identificam o ambiente em cada observação. Nenhuma tarefa é
instalada automaticamente pelo console ou pela execução dos testes.

Os arquivos
`availability-AAAA-MM-DD.jsonl` usam dias UTC, têm evidências por observação e devem
ter retenção e monitoramento de espaço configurados no observador. Falha ao persistir
encerra a sonda com erro para permitir supervisão. A saída não registra URL, corpo ou
detalhes de exceções. Não forneça credenciais na URL.

Essas evidências independentes são uma fonte separada; não entram automaticamente no
denominador do health check local nem são importadas pelo painel. Se o observador
também parar, o período sem sondagens é desconhecido. No mesmo host, uma queda da
máquina continua sem observação: uma sonda local não comprova disponibilidade externa
nem acessibilidade via DNS, proxy ou rede pública. Se houver outro host no futuro,
o mesmo script pode rodar nele apontando para a URL pública. A instalação e os alertas
do supervisor precisam ser configurados na infraestrutura de destino.
