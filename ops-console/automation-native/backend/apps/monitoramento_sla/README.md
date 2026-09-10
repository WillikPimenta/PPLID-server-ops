# -*- coding: utf-8 -*-
"""
Monitoramento de SLA útil (Indicadores)

## Origem
- Protocolos: `rotina_detalhado_bruto_record` (BRFlow detalhado bruto)
- Dimensões / projeção: Megazord (`dim_*`, `projecao_sla`)
- Parquets em `monitoramento_sla/*.parquet` são só referência estrutural / homologação

## Gaps da origem
- `hora_cadastro` / `hora_conclusao` não persistem no bruto → default 00:00:00
- `tipo_conclusao` ausente no detalhado BRFlow

## Flags §24
Ver `apps.monitoramento_sla.flags` (RANK_VOLUME_OP, OPEN_DEFAULT_DENTRO, …).

## Sync (automático)

Após o bot **rotina detalhado** gravar no DB, a fila `BotDbSyncJob` enfileira
`domain=monitoramento_sla` (sem botão no portal).

Manual / ops:
```
python manage.py bootstrap_monitoramento_sla [--skip-dim]   # dim + FY26 + sync
python manage.py sync_monitoramento_sla --days 90
python manage.py import_sla_consolidado_parquet [path]   # homologação Power BI (FY26)
```

## Resumo (layout analítico)

`GET /api/v1/monitoramento-sla/resumo/?start_date=2026-01-01&end_date=2026-12-31`

Agrega `sla_util_consolidado` com:

- KPIs (SLA, ajustado, meta 98,5%, vs período anterior, volume realizado/esperado/atingimento)
- tipo de conclusão (filtro cruzado `tipo_conclusao`)
- workflows impactados + detalhe
- calendário (tendência = mesmo dia da semana anterior; cor vs meta)
- série temporal com drilldown (`granularity`: auto|year|quarter|month|week|day|hour)
- heatmap hora×dia (`hour`, `dow` como filtros locais)

Gate/throttle/cache: `MONITORAMENTO_SLA_*`. Sem “Impactado por instabilidade”.
"""
