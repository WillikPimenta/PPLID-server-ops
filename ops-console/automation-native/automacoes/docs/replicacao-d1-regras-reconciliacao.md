# Replicação D-1 — Regras de reconciliação (defaults conservadores)

Documento operacional enquanto decisões de negócio finais não forem fechadas no plano mestre.
Complementa [`replicacao-aud-d1-config-banco.md`](replicacao-aud-d1-config-banco.md) e o serviço
`backend/apps/replicacao_d1/services/reconciliation.py` (`RECONCILIATION_RULE_VERSION=1`).

## Vocabulário operacional

| Status | Significado |
|---|---|
| `planejado` | Protocolo no plano, ainda sem upload |
| `recebido` | Upload BRFlow OK (`SALVO_OK` / `UPLOAD_OK`), sem confirmação D-1 |
| `replicado` | Confirmação única encontrada na janela |
| `pendente` | Sem confirmação na janela **ou** ambiguidade |
| `falhou` | Erro operacional BRFlow |
| `divergente` | Replicado órfão (confirmação sem plano correspondente) |
| `ignorado` | Falha BRFlow registrada; reconciliação não tenta match |

## Janela de confirmação (`missing`)

**Default conservador (implementado):**

1. Busca confirmação na **data de referência D-1** (exata).
2. Se não houver match único, tenta **D+1**.
3. Não consulta o dia anterior: uma confirmação D-1 só pode ser vinculada à data exata
   ou ao dia seguinte, evitando atribuir um protocolo a um run anterior sem evidência.
4. Se nenhum candidato único → `pendente` + reconciliação `missing`.

Não marcar `replicado` quando a confirmação só existir fora dessa janela.

## Ambiguidade (`ambiguous`)

**Default conservador (implementado):**

- Mais de um `ReplicacaoD1Replicado` candidato para o mesmo protocolo normalizado na janela
  → `status_operacional=pendente` (nunca `replicado` automático).
- Persistir `ReplicacaoD1Reconciliacao.status=ambiguous` com motivo contendo a contagem.

Resolução manual ou regra de negócio futura deve ser versionada (`regra_version`).

## Falha BRFlow

- `ERRO`, `FALHOU`, `FALHA`, `TIMEOUT`, `CANCELADO` → `falhou`.
- Reconciliação `ignored`; não busca confirmação.

## Upload vs confirmação (dashboard / shadow)

| Métrica | Fonte |
|---|---|
| Upload OK | Protocolos `recebido` + `replicado` (ou `SALVO_OK` no Excel shadow) |
| Confirmados | Protocolos `replicado` |
| Divergentes | Replicados sem plano na janela (`find_divergent_replicados`) |

Shadow mode (`REPLICACAO_D1_FF_SHADOW_MODE=true`) compara contagens Excel vs DB e agregados
upload/confirmação após sync; divergências vão para log `[shadow_d1]` e resumo em `SyncLog.message`.

## Idempotência

`reconcile_protocolos_for_run` pode ser reexecutado: segunda passagem retorna `0` updates se
estado já estiver correto.

## Pendências de negócio (não alterar sem decisão)

- Tratamento de reprocessamento do mesmo `run_id`.
- Expansão da janela `missing` além de D / D+1.
- Desempate automático em `ambiguous` (ex.: workflow origem, timestamp).

Até lá, manter defaults acima e registrar exceções via reconciliação persistida.
