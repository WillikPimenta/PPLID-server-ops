# Dados operacionais D-1 — ingestão, dashboard e retenção

Documentação da centralização bot→banco para o dashboard de replicação D-1.

## Fontes e consumo

| Artefato | Ingestão | Consulta UI |
|----------|----------|-------------|
| Excel do plano (`replicacao_aud_d1_relatorio_*.xlsx`) | `BotDbSyncJob` → `sync_replicacao_d1_to_db` | `GET /api/v1/replicacao-d1/dashboard/` |
| CSV replicados (`brflow-replicadosd1-tratado_*.csv`) | `BotDbSyncJob` → `sync_replicados_to_db` | mesmo dashboard |
| Config permanente | API `/replicacao-d1/config/*` | `ReplicacaoD1ConfigView` |

A UI **não** lê caminhos locais durante a navegação do dashboard.

## Modelo operacional

- **`ReplicacaoD1Run`** — cabeçalho do plano/execução
- **`ReplicacaoD1WorkflowDia`** — grão workflow × run
- **`ReplicacaoD1Protocolo`** — protocolos planejados + `status_operacional`
- **`ReplicacaoD1Replicado`** — confirmação BRFlow
- **`ReplicacaoD1SyncLog`** — auditoria técnica de cada importação (admin)
- **`BotDataIngestion`** (`common`) — lote idempotente por arquivo/run/data

### Status operacional (`status_operacional`)

| Valor | Significado |
|-------|-------------|
| `planejado` | Presente no plano (default antes da reconciliação) |
| `recebido` | Processado pelo bot (SALVO_OK), sem confirmação CSV |
| `replicado` | Confirmação persistida no CSV de replicados |
| `pendente` | Planejado sem resultado na janela esperada |
| `falhou` | Erro BRFlow ou destino |
| `divergente` | Confirmação sem plano correspondente |
| `ignorado` | Fora de escopo (reservado) |

Reconciliação automática após sync de plano ou replicados (`services/reconciliation.py`).

## API pública (sem infraestrutura)

Endpoints operacionais **não** expõem:

- caminhos locais (`source_file`, `C:\...`)
- chaves normalizadas internas (`protocolo_normalizado`)
- versão/hash de config duplicados no run (use `ReplicacaoD1ConfigSnapshot`)

O campo `parquet_referencia` do run é exposto como **`referencia_dados`** (nome do parquet, não path).

## Retenção

Settings:

- `REPLICACAO_D1_SYNC_LOG_RETENTION_DAYS` (default **90**)
- `REPLICACAO_D1_INGESTION_RETENTION_DAYS` (default **180**)

Comando:

```bash
python manage.py purge_replicacao_d1_retention --dry-run
python manage.py purge_replicacao_d1_retention
```

Remove apenas `ReplicacaoD1SyncLog` e `BotDataIngestion` antigos. **Não** apaga runs, protocolos, replicados nem snapshots de config.

## Colunas removidas de `replicacao_d1_run` (migration 0008)

| Coluna | Motivo |
|--------|--------|
| `source_file` | Duplicava auditoria já em `ReplicacaoD1SyncLog` / `BotDataIngestion` |
| `config_version` | Nunca populada; snapshot oficial em `ReplicacaoD1ConfigSnapshot` |
| `config_hash` | Idem |

Versão/hash de config por execução: `ReplicacaoD1ConfigSnapshot` (`run_id`).

## Dashboard

Rota portal: `/secao/indicadores/replicacao-d1` (redirect legado: `/planejamento/automacao/replicacao-d1/dashboard`)

API: `GET /api/v1/replicacao-d1/dashboard/?data_de=&data_ate=&status=&protocolo=`

Detalhe: `GET /api/v1/replicacao-d1/runs/<run_id>/protocolos/<protocolo>/`

## Modal D-1 (operação)

O modal de configuração do robô D-1 passou a usar o banco para:

| Recurso | API |
|---------|-----|
| Lista de runs (ok/pend/erro) | `GET /api/v1/replicacao-d1/runs/?operacional=1` |
| Projeção de consumo e capacidade | `GET /api/v1/replicacao-d1/config/projecao/?competencia=YYYY-MM` |
| Ajuste manual de consumo | `POST /api/v1/replicacao-d1/config/ledger/ajuste/` |
| Purge de ledger por run | `POST /api/v1/replicacao-d1/config/ledger/purge-runs/` |

Ainda no bot (filesystem): validação de plano, limpeza de JSONs locais (`/automacoes/replicacao-d1/limpar-planos/`). Quando `remover_ledger` estiver marcado, o portal também chama o purge no banco.
