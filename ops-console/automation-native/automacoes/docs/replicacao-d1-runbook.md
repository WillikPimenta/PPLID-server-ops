# Runbook operacional — Replicação D-1



## Falha no sync bot→banco



1. Verificar fila: painel Automações ou `BotDbSyncJob` com `domain=replicacao_d1`.

2. Conferir log de ingestão (`BotDataIngestion`) — status, `rows_loaded`, `error_summary`.

3. Reprocessar com permissão `CONFIGURE`: `POST /api/v1/replicacao-d1/sync/` com `force=true`.

4. Se estrutura inválida: corrigir Excel/CSV fonte; sync não apaga partição válida com carga vazia.



## Run parcial ou falho



1. Consultar `ReplicacaoD1Run.status_canonical` e workflows com `status_operacional=falhou`.

2. Manifesto local: `{pasta_run}/manifest.json`.

3. Reexecução: bot com `forcar_reexecucao` / `apenas_pendentes` conforme estado JSON.



## Backfill histórico



Somente **dev/local** até homologação formal. Produção: dry-run + auditoria, sem `--apply`.



```powershell

cd backend

python manage.py audit_replicacao_d1_data --json > tmp/audit_d1_before.json

python manage.py backfill_replicacao_d1_data --dry-run --json > tmp/backfill_dryrun.json

python manage.py backfill_replicacao_d1_data --apply --batch-size 2000 --json > tmp/backfill_apply.json

python manage.py audit_replicacao_d1_data --json > tmp/audit_d1_after.json

```



Critérios: contagens before/after documentadas; nenhuma linha apagada; normalizações aplicadas.



## Retenção de arquivos



- Gate exige ingestão com hash + run fechado antes de apagar artefatos (com `fonte_banco_ativa`).

- Auditar: `python manage.py audit_replicacao_d1_retention`

- Purge logs técnicos: `python manage.py purge_replicacao_d1_retention --dry-run`



## Aceite de rollout (gate 15.1)



```powershell

# Bot — suite estendida

cd automacoes

python -m pytest tests/test_replicacao_aud_d1_planning.py `

  tests/test_replicacao_aud_d1_agendamento.py `

  tests/test_replicacao_d1_db_bridge.py `

  tests/test_replicacao_d1_brflow_workflows.py `

  tests/test_replicacao_d1_filtros_pesquisa.py `

  tests/test_replicacao_d1_listagem.py `

  tests/test_replicacao_d1_workflow_upload.py `

  tests/test_replicacao_situacao_brflow.py -q



# Backend

cd ../backend

python manage.py check

python manage.py makemigrations --check --dry-run

python manage.py validate_replicacao_d1_rollout

python manage.py audit_replicacao_d1_data --json

python manage.py test apps.replicacao_d1.tests apps.common.tests --keepdb



# Frontend

cd ../frontend

npm run build

```



Esperado: `validate_replicacao_d1_rollout` 5/5 OK; testes verdes; build OK.



## Shadow mode (dev/homolog)



Ativar comparação pós-sync Excel vs banco (contagens + upload vs confirmação):



```env

REPLICACAO_D1_FF_SHADOW_MODE=true

```



Logs: prefixo `[shadow_d1]`; divergências também em `ReplicacaoD1SyncLog.message`.



Regras de reconciliação: [`replicacao-d1-regras-reconciliacao.md`](replicacao-d1-regras-reconciliacao.md).



## Rollback (sem perder dados no PostgreSQL)



Desative comportamentos novos via variáveis de ambiente — os dados já ingeridos permanecem no banco:



| Variável | Efeito ao `False` |

|---|---|

| `REPLICACAO_D1_FF_NEW_INGESTION` | Sync não cria `BotDataIngestion` |

| `REPLICACAO_D1_FF_NEW_RECONCILIATION` | Sync não reconcilia protocolos |

| `REPLICACAO_D1_FF_DASHBOARD_DB` | Dashboard legado (se ainda existir fallback) |

| `REPLICACAO_D1_FF_DASHBOARD_NO_FILES` | Permite leitura de Excel/parquet no dashboard |

| `REPLICACAO_D1_FF_SHADOW_MODE` | Desliga comparação shadow pós-sync |



Exemplo homolog:



```env

REPLICACAO_D1_FF_NEW_RECONCILIATION=false

REPLICACAO_D1_FF_SHADOW_MODE=true

```



Validar após mudança: `python manage.py validate_replicacao_d1_rollout`



- Dados operacionais ficam no PostgreSQL; flags não apagam runs/protocolos.

- Não apagar runs/protocolos sem backup; retenção de arquivos é reversível apenas se cópia existir.



## Diagnóstico de workflows não processados

O resultado por workflow é persistido no PostgreSQL e deve ser consultado antes dos logs:

- `salvo`: alteração confirmada pelo BRFlow;
- `sem_alteracao`: a quantidade alvo já estava configurada; não é erro nem novo upload;
- `pulado`/`inativo`: execução não aplicável, com `motivo_codigo` e `motivo_resumo`;
- `nao_salvo`/`falhou`: bloqueio operacional que deixa o run parcial ou falho;
- `cancelado`: workflow não iniciado por solicitação de parada.

Na Central de Automações, abra **Planos recentes → Ver detalhes** para consultar etapa,
tentativa, quantidades e motivo. No portal, filtre os workflows pelo `run_id`.

Para `SALVAMENTO_NAO_CONFIRMADO`, verifique o screenshot e o log da fase
`confirmacao_salvamento`; o bot não considera o fechamento forçado do painel como sucesso.
Depois de corrigir a causa, reutilize o mesmo Run ID com **Apenas pendentes**. Não use
**Forçar reexecução** quando os workflows já salvos não precisarem ser reenviados.


## Checklist homolog (sem produção)



- [ ] Gate 15.1 verde (bot + BE + FE build)

- [ ] Backfill dev documentado (`tmp/audit_d1_before.json`, `audit_d1_after.json`)

- [ ] `validate_replicacao_d1_rollout` 5/5

- [ ] Flags documentadas (tabela acima)

- [ ] Shadow mode inspecionado em sync de teste

- [ ] Smoke manual: dashboard → filtro KPI por status → detalhe protocolo → sync force (usuário `CONFIGURE`)



**Fora de escopo imediato:** apply backfill produção; dashboard oficial em prod; decisões de negócio finais (ver plano mestre).
