# Replicação D-1 — Configuração no PostgreSQL

Documentação operacional da fonte de configuração em banco para o mode
`replicacao_auditoria_d1` (bot principal). O bot legado `replicacao_auditoria` e a
console Flask **não** usam esta fonte.

## Visão geral

Com `fonte_banco_ativa=False` (padrão após a migration), o bot continua lendo
`robot_config.json`, `Default.xlsx`, `Categoria.xlsx`, escala CSV e ledger CSV.

Com `fonte_banco_ativa=True`, o planejamento consulta **somente** o PostgreSQL
no início de cada ciclo, congela um snapshot (`config_version` + `config_hash`)
e **não** faz fallback para planilhas/JSON.

```text
Migration → fonte desativada → preview → apply → validação/paridade
          → ativação explícita → planejamento somente pelo banco
```

## Implantação

1. Aplicar migrations do app `replicacao_d1` (inclui `0003_config_banco`).
2. Abrir **Planejamento → Automações → Replicação de Auditoria (D-1)**.
3. No painel **Configuração permanente (banco)**, importar na ordem sugerida:
   - `Default.xlsx` (workflows + calculadora)
   - `Categoria.xlsx` (clientes / metas)
   - escala (`escala_auditores.csv`)
   - ledger legado (`meta_cliente_ledger.csv`), se houver histórico a preservar
4. Revisar o preview (inclusões, atualizações, inativações) e aplicar.
5. Conferir `GET /api/v1/replicacao-d1/config/snapshot/preview/` (ou a aba Geral).
6. Ativar a fonte (**Ativar fonte do banco**) — exige configuração completa;
   override administrativo com `force=true` fica auditado.
7. Validar plano na UI antes da primeira execução agendada.

## Rollback lógico

`POST /api/v1/replicacao-d1/config/fonte/desativar/` (ou botão na UI) desliga a
flag. O bot volta a usar planilhas/JSON. As planilhas antigas **não** são
apagadas nem movidas automaticamente — arquivar como evidência após a validação
operacional.

## Separação permanente × execução

| Permanente (banco) | Temporário (execução / request) |
|--------------------|---------------------------------|
| Agendamento, destinos BRFlow, retenção | `run_id`, data de referência |
| Calculadora, clientes, workflows, escala | `apenas_planejamento`, `gerar_novo_plano` |
| Meta Produ BRFlow (`meta_produ_diaria`) e Case (`meta_produ_diaria_case`) | `forcar_reexecucao`, `apenas_pendentes` |
| Ledger / metas de cliente | Matrícula/senha Okta (nunca persistidas) |
| `fonte_banco_ativa`, versão/hash | |

As metas de capacidade por auditor são **independentes**: BRFlow (fila G auditoria) e Case (Documentoscopia 3.1). Se `meta_produ_case` estiver vazia no robot_config legado, o planejamento usa a meta BRFlow como fallback.

### Mix Manual × Automático (matrícula)

No parquet detalhado-tratado, a coluna **matrícula** classifica o protocolo:

- `0` = Manual (usuário)
- `1` = Automático

Com `usar_amostra_mix_manual_automatico=True`, a amostra diária de cada workflow usa `amostra_pct_manual` / `amostra_pct_automatico` como **preferência** (pesos; normalizados se não somarem 100) — não como cota rígida. Se o workflow não tiver Manual suficiente, completa com Automático e vice-versa, até a amostra solicitada. Flag desligada por padrão (comportamento legado).

## API (prefixo `/api/v1/replicacao-d1/config/`)

| Método | Rota | Permissão |
|--------|------|-----------|
| GET/PATCH | `geral/`, `calculadora/` | VIEW / CONFIGURE |
| CRUD | `clientes/`, `workflows/`, `escala/`, `metas/` | VIEW / CONFIGURE |
| GET, POST | `ledger/`, `ledger/ajuste/` | VIEW / CONFIGURE |
| GET | `historico/`, `snapshot/preview/` | VIEW |
| POST | `import/preview/`, `import/apply/` | CONFIGURE |
| POST | `fonte/ativar/`, `fonte/desativar/` | CONFIGURE |

Códigos: `planejamento.automacao.view` e `planejamento.automacao.configure`.

`GET/POST /api/v1/automacoes/config/?mode=replicacao_auditoria_d1` passa a
ler/gravar permanentes no PostgreSQL **somente** com a fonte ativa; demais modes
permanecem em `robot_config.json`.

## Workflows pendentes

Nome desconhecido no parquet → `get_or_create` como `PENDENTE` / inativo, sem
entrar no plano. Complete cliente, fila e nomes D-1/Selenium na aba Workflows
e ative o registro.

## Observabilidade

- Cada run registra versão/hash em `ReplicacaoD1ConfigSnapshot` (quando persistido).
  Campos `config_version` / `config_hash` em `ReplicacaoD1Run` foram removidos (legado).
- Escritas de config geram `ReplicacaoD1ConfigHistorico` (before/after + usuário).
- Erros objetivos: banco indisponível, cadastro incompleto, escala ausente,
  workflow pendente — sem fallback silencioso.

### Logs do planejamento (`phase=planning_*`)

O bot emite logs estruturados via `plan_log` durante o planejamento (sem abrir Selenium).
No **painel LIVE** aparecem como linhas `PLANLOG|...` (stdout); no arquivo
`robot_replicacao_auditoria_d1.log` ficam com timestamp e nível INFO.
Filtrar por `phase=` ou `PLANLOG|`:

| Fase | Quando aparece |
|------|----------------|
| `planning_start` | Entrada do planejamento (run_id, data_ref, flags de fonte/fallback) |
| `planning_source` | Carga D-1: banco, parquet legado ou fallback híbrido |
| `planning_config` | Config/snapshot congelado (`config_version`, hash, workflows) |
| `planning_retro` | Pool retroativo (ativo/inativo, path db/hybrid/parquet) |
| `planning_allocate` | Balanceamento mensal, histórico, redistribuição |
| `planning_persist` | Gravação do plano no PostgreSQL |
| `planning_done` | Resumo final (workflows, protocolos, warnings) |
| `planning_error` | Falha capturada no wrapper do bot |

Exemplo de filtro: `phase=planning_source` ou `run_id=20260813_205239`.

**Modo só planejamento (`apenas_planejamento`):** ao concluir (sucesso ou falha), o subprocesso do robô encerra automaticamente (`exit=0`), sem precisar clicar em Parar.

**Timezone retroativo:** protocolos com `selection_reason` retroativo podem trazer `data_analise` naive do pool; na persistência (`persist_plan`) o valor é normalizado para timezone-aware antes do `bulk_create`.

## Fallback parquet (dias ausentes no banco)

Com `fonte_banco_ativa=True`, o planejamento usa **somente** `rotina_detalhado_bruto_record`
para volumetria. A flag global **`fallback_parquet_dias_ausentes`** (Config → Destinos/Execução)
permite completar dias **sem partição no PostgreSQL** com o parquet tratado do **dia exato**
(`brflow-detalhado-tratado_YYYYMMDD.parquet`).

| Cenário | Com flag desligada | Com flag ligada |
|---------|-------------------|-----------------|
| D-1 de referência ausente no banco | Planejamento falha | Lê parquet do dia, ingere lote auditável (`FonteLote`) e continua |
| Dia retroativo ausente no banco | Aviso; dia ignorado | Lê parquet do dia exato; `selection_reason` com sufixo `:parquet` |

**Distinção:** `fallback_ultimo_parquet` vale apenas no modo legado (`fonte_banco_ativa=False`).
No modo banco, não há fallback para “último parquet disponível” — só o arquivo do dia alvo.

Prioridade por dia: banco (se **qualquer** registro na partição) → parquet tratado (flag on) → falha/aviso.

## Artefatos que continuam em disco / SharePoint

Parquet de volumetria, CSVs de protocolos, relatórios Excel/CSV e evidências
históricas das planilhas antigas.
