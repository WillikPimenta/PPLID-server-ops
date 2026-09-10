# Métricas de Excelência Operacional (EO)

Documento de referência — Sprint 1 (confiabilidade).  
**Não alterar fórmulas silenciosamente**: qualquer mudança deve atualizar este arquivo e os testes de regressão.

## Fórmula central

```
EO% = round(clamp(0, 100, (1 − falhas / auditados) × 100), 1)
```

- Se `auditados ≤ 0` → `EO% = null` (não calcular).
- Se `falhas > auditados` → EO fica em **0,0%** (clamp), não negativo.
- Tabelas `qualidade_auditado` e `qualidade_falha` são **independentes** (sem join por protocolo).
## EO ponderado por criticidade (tela Agentes)

A visualização operacional preserva o EO real e acrescenta:

```
EO ponderado = round(clamp(0, 100, (1 − impacto ponderado / auditados) × 100), 1)
impacto ponderado = soma dos pesos das falhas
```

### Regra temporal do impacto (corte 01/08/2026)

A data que decide o peso é **sempre** `QualidadeFalha.data`, independentemente do
`date_axis` (auditoria/análise) selecionado na tela.

Constante: `IMPACT_WEIGHT_CUTOVER = date(2026, 8, 1)` em `analytics.failure_weight`.

| Condição | Peso |
|---|---|
| `data` anterior a 01/08/2026 | **1,0** (impacto 1:1, sem matriz) |
| `data` nula | **1,0** (legado) |
| `data >= 01/08/2026` | matriz de criticidade abaixo |

Em períodos mistos, a parcela histórica (antes do corte) soma 1 ponto por falha/protocolo
e a parcela a partir de 01/08 usa a matriz. Não confundir com:
- **EO real** (baseado na quantidade de falhas);
- **métrica oficial / visão completa** (população em `official_metric.py`, corte 01/07/2026).

A criticidade vem de `categoria_falha`. A dificuldade usa primeiro
`nivel_dificuldade_confer` e, quando vazia, `nivel_dificuldade`.

Exceção retroativa: quando a dificuldade efetiva contém `Cenários Avaliativos`,
o peso é **0** para `data >= 04/01/2026`, independentemente do corte da matriz.
Antes de 04/01/2026 (ou com data nula), permanece o peso histórico **1,0**.

| Categoria / condição | Fácil | Média | Difícil |
|---|---:|---:|---:|
| Crítica | 3,5 | 3,0 | 2,5 |
| Não Crítica | 1,0 | 1,0 | 1,0 |
| Procedimento + etapa contendo `Análise Visual` | 1,0 | 1,0 | 1,0 |
| Procedimento + etapa contendo `Sobreposição` ou `Validação` | 1,5 | 1,3 | 1,0 |

- Nível de dificuldade efetivo contendo `Cenários avaliativos` recebe peso **0** com
  `data >= 04/01/2026`.
  Antes do corte, vale **1,0**.
- Categoria/dificuldade não reconhecida recebe peso **1**, evitando descarte silencioso.
- Cliente **CLARO - FORMALIZAÇÃO** (`id_cliente=9999`): qualquer falha (incl. categoria vazia / “Não informada”)
  classifica como **Procedimento** e recebe peso **1,0** (não aplica a majoração de Sobreposição/Validação).
- Cliente **GAQ** (`id_cliente=41`): cliente interno de réplica/auditoria — fora do portfólio
  padrão do report executivo e das métricas operacionais de contestação (salvo filtro explícito).
- Em grain `protocolo`, usa-se o maior peso entre as linhas do mesmo protocolo.
- A lista de protocolos do Cliente 360 (`_investigation_protocols`) segue a mesma regra
  (max por protocolo; não soma linhas de etapas distintas).
- O ranking operacional é ordenado pelo menor EO ponderado e maior impacto, mas continua exibindo falhas e EO reais.

## Grain (granularidade)

| Valor | Contagem |
|-------|----------|
| `etapa` (default) | `Count(id)` — uma linha = um item |
| `protocolo` | `Count(distinct protocolo)` (exclui protocolo em branco) |

Na aba Agentes, os dois cards de volume seguem o grão selecionado:

- `etapa`: **Análises auditadas** = linhas de auditados; **Registros com falha** = linhas de falhas.
- `protocolo`: **Protocolos auditados** e **Protocolos com falha** = protocolos distintos.
- A taxa dos cards usa sempre o mesmo grão no numerador e no denominador.

### Escopo operacional (cards, hierarquia de líder e tabela)

População compartilhada na aba **Agentes** (parâmetros via `operational_agentes_scope_params`):

| Regra | Efeito |
|-------|--------|
| `workforce_only=1` | Matrículas com `AgentHistory` sobreposto ao período |
| `responsibility_scope=lider` | Exclui ocorrências em janelas de facilitador |
| `agent_linked_only=1` | Falhas processuais sem vínculo de agente ficam fora |
| `tipo_conclusao=Manual` | Somente tipificação Manual (residual incluído) |

- **Cards de volume** e **hierarquia de líder** usam a soma dos segmentos temporais agente×líder (exclui bucket “Sem atribuição na data”).
- **Tabela detalhe** e **export CSV** na aba Agentes enviam os mesmos parâmetros; o payload `operational_detail_scope` expõe `{ auditados, falhas }` para validação na UI.
- **Drill-down** na hierarquia pode enviar `lider`, `segment_start` e `segment_end` para restringir à vigência do segmento.
- **Hierarquia de facilitador** (`facilitator_hierarchy`) replica a estrutura temporal da de líderes, com métricas só das janelas de treinamento/acompanhamento.
- **Tabela detalhe facilitador** usa `operational_facilitator_scope_params` (`responsibility_scope=facilitador` + `tipo_conclusao=Manual` + vínculo HC). O payload `facilitator_detail_scope` expõe `{ auditados, falhas }` para validação na UI.
- **Drill-down facilitador** pode enviar `facilitador_matricula`, `matricula`, `segment_start` e `segment_end` para restringir à vigência da janela.

### Responsável efetivo (`responsavel_*`)

Campos enriquecidos via `build_responsavel_lookup` (Agent History + janelas de facilitador):

| Campo API | Descrição |
|-----------|-----------|
| `responsavel_nome` | Nome do facilitador (se em janela) ou líder HC na data |
| `responsavel_matricula` | LAN ID do responsável |
| `responsabilidade` | `lider` ou `facilitador` |
| `regra_responsabilidade` | Jornada (`onboarding`, `reboarding`, `upgrade`) quando facilitador; vazio se líder |

- **`lider` (TSV)** permanece o valor gravado na base/importação — distinto do responsável HC.
- **`lider_responsavel`** (legado) espelha `responsavel_nome` quando `responsabilidade=lider`; preferir `responsavel_*` em integrações novas.
- Listagens paginadas (`/auditados`, `/falhas`) e export workforce incluem os campos acima.

### Export CSV workforce

Quando `workforce_only=1` **ou** `responsibility_scope` ∈ `{lider, facilitador}`, o CSV insere **após Matrícula**:

| Coluna CSV | Origem |
|------------|--------|
| Responsável | `responsavel_nome` |
| Tipo responsável | `Líder` / `Facilitador` |
| Matrícula responsável | `responsavel_matricula` |
| Jornada | `regra_responsabilidade` (vazio se líder) |

- A coluna **Líder** existente (= TSV) é mantida nas falhas.
- Filename com sufixo `_facilitador` quando `responsibility_scope=facilitador` (`qualidade_operacional_falhas_facilitador.csv`, `qualidade_operacional_auditados_facilitador.csv`).

O payload `workforce_coverage` torna explícitas as linhas sem matrícula, da matrícula
técnica `sistema` e de matrículas sem `AgentHistory` válido no período. Essas últimas
continuam fora do ranking, mas deixam de ser descartadas silenciosamente na interface.

## Escopo HC — Claro Formalização / Confer

População sujeita à validação de equipe (`services/claro_confer_scope.py`):

| Identificação | Origem |
|---------------|--------|
| `id_cliente=9999` | alias TSV **CLARO - FORMALIZAÇÃO** |
| `id_cliente=83` **e** `id_workflow=450` | projeção Intranet Compliance/Reinspeção Claro |

**Regra:** falhas e auditados dessa população só entram no indicador quando o agente
(`matricula`) possui `AgentHistory.team = Operacional/Compliance` vigente na **data do
evento** (campo definido por `date_axis`: `data` ou `data_analise`).

Excluídos automaticamente:

- matrícula vazia ou sem histórico HC na data do evento;
- agente em outra equipe na data (ex.: `Operacional/Fraud`, Capacitação).

**Alcance:** qualquer agregação ou exportação que consuma `filtered_auditados` /
`filtered_falhas` (KPIs, ranking, Resumo, Cliente 360°, contestações, CSV). Os dados
permanecem na base; a regra é de escopo de métrica, não de exclusão física.

## Eixos de data

Parametro `date_axis`:

| Valor | Falhas | Auditados |
|-------|--------|-----------|
| `auditoria` (**default** — BE + UI desde `21946ea`) | `data` (Data / auditoria) | `data` (Data) |
| `analise` | `data_analise` (Data de Análise) | `data_analise` (Data de Análise) |

KPIs, série temporal e listagens usam o mesmo campo de período em auditados e falhas conforme o eixo.

## Filtro Data Prazo (`exclude_out_of_deadline`)

Toggle global **Data Prazo** na barra de filtros do EO. Quando ativo (`exclude_out_of_deadline=true`):

- **Escopo:** somente a base de **falhas** (`filtered_falhas`). Auditados e export de auditados **não** são filtrados.
- **Regra:** exclui falhas com prazo conhecido **≥ 120 dias**, onde prazo = `data` (auditoria) − `data_analise` (origem).
- **Bordas:** se `data` ou `data_analise` estiver ausente, ou se as datas estiverem invertidas (`data < data_analise`), a linha **permanece** no recorte (o filtro não se aplica).
- **EO%:** numerador (falhas) reflete o recorte; denominador (auditados) permanece o da população filtrada pelos demais critérios.

Implementação SQL em `services/deadline.py` (`apply_falhas_prazo_filter`), encadeada em `_filtered_falhas_base`.

## Cards de tipificação

Dois eixos distintos no TSV (não misturar):

| Campo | Exemplos | Papel |
|-------|----------|--------|
| `tipo_falha` / `tipo_conclusao` | Manual, Automático, Processual | Tipificação / modalidade da análise |
| `categoria_falha` | **Procedimento**, Falha Crítica, Não Crítica | Categoria da falha (ortogonal ao tipo) |

**Procedimento** é categoria: aparece majoritariamente em falhas `tipo_falha=Manual` e também em Automático/Processual — **não** é sinônimo de Processual.

Classificação canônica (`normalize_tipo_conclusao`): **apenas** Manual, Automático (aliases Mapeamento/Sistema), Processual.  
Qualquer residual (em branco, Biometria, Regra De Negócio, …) → **Manual**. **Não existe card Outros.**

| Card | Numerador (falhas) | Denominador (auditados) | `denominator_mode` | `group` |
|------|--------------------|-------------------------|--------------------|---------|
| Manual | `tipo_falha` normalizado Manual (inclui residual) | `tipo_conclusao` Manual (inclui residual) | `tipo_conclusao` | `tipificacao_pareada` |
| Automático | `tipo_falha=Automático` | `tipo_conclusao=Automático` | `tipo_conclusao` | `tipificacao_pareada` |
| Processual | `tipo_falha=Processual` | **total auditados do filtro** | `total` | `tipificacao_denom_total` |

Filtro Manual no detalhe: exclui Automático/Processual (e aliases); inclui residual no banco.  
`tipo_bucket=Outros` legado é tratado como filtro Manual.

### Campos auxiliares por card

- `share_pct` = `100 × auditados_card / total_auditados` (participação no **volume auditado**, não nas falhas).
- `formula` / `denominator_label` — texto para tooltip.
- `data_quality` — quando `falhas > 0` e `auditados == 0` (inconsistência / população sem denom.).

## Breakdown (`/breakdown/`)

Dimensões: `tipo_analise`, `etapa`, `tipo_falha`, `tipo_conclusao`, `id_cliente`, `id_workflow`.

Parâmetro `metric`:

| metric | Valor plotado | Denominador |
|--------|---------------|-------------|
| `quantidade` (default) | falhas absolutas | — |
| `participacao` | `100 × falhas_linha / total_falhas` | total falhas |
| `taxa` | `100 × falhas_linha / auditados_linha` | auditados da categoria; se 0 → alerta, sem taxa inventada |

## Insights / prioridades (Pacote A — cliente primeiro + Fase 1 executiva)

O endpoint `/insights/` devolve diagnóstico executivo:

- `resumo_executivo` — narrativa automática (resultado, tendência, concentrações, recomendação).
- `semaforo` — status vs meta (default 99,7%) e variação M-1 (`ok` / `atencao` / `proximo_limite` / `fora_meta`).
- `top_oportunidades` (máx. 3) — ranking por participação nas falhas, com `impacto` (Baixo→Muito alto) e ação sugerida.
- `diagnostico` — atenção principal, maior risco, maior oportunidade, ponto positivo.
- Alertas de **base pequena** quando auditados &lt; 30 no risco de EO.
- Labels de cliente/workflow **sem ID** na UI (só o nome).
- Ranking de operador permanece **apenas** na aba Agentes (`piores_agentes` vazio).

Breakdown em modo `quantidade` inclui `cum_share_pct` para Pareto. Dimensão `localidade` é só falhas (sem EO%).

## Δ M-1 (variação)

- Período anterior: `shift_period(start, end)` com semântica **civil M-1**:
  - mês civil completo → mês civil anterior completo (jul/31 → jun/1–30, **não** 31/05–30/06);
  - intervalo no mesmo mês (MTD/parcial) → mesmos dias-calendário no mês anterior (clamp);
  - intervalo que cruza meses → janela de mesma duração em dias (fallback).
- `delta_pp = EO_atual − EO_anterior` (pontos percentuais). **Não** confundir com variação relativa `%`.
- Payload `previous`: datas, EO, auditados, falhas, `delta_pp`, `comparable`, `interpretation`.
- Ao montar `prev_params`, preservar filtros multi-valor (`QueryDict.lists` / `params_with`).

## Contestações (card Resumo / Agentes)

Fonte: tabelas Qualidade (`qualidade_falha` / `qualidade_auditado`), **não** o módulo
`AuditoriaAtividadeProtocolo`.

Identificação (escopo operacional — exceto Contestação Compliance / Formalização):

- Auditados: `tipo_analise` contendo `Contest`, **exceto** `Compliance`
  (ex.: Contestação, Contestação Externa, Contestação Claro, Contestação Externa Legado).
- Falhas: mesmo recorte em `tipo_analise` **ou** `modulo` contendo `Contest`
  (na base, `modulo='Contestação'` alinha quase 1:1 com esses tipos).
- `Contestação Compliance` permanece na base para papéis dedicados, mas não entra
  em Indicadores EO / Quality Overview (paridade com exclusão do cliente 9999 Formalização).
- Cliente **GAQ** (`id_cliente=41`) também fica fora do escopo padrão (portfólio report +
  contestação operacional sem filtro explícito de cliente).

Métricas:

```
auditados_contestacao     = COUNT(auditados com tipo Contestação*)
protocolos_contestados    = COUNT(DISTINCT protocolo) nesses auditados
eventos_contestacao       = COUNT(falhas de contestação filtradas)
protocolos_com_falha_contestacao = COUNT(DISTINCT protocolo) nessas falhas
procedentes               = protocolos_com_falha_contestacao
taxa_procedencia_pct      = procedentes ÷ protocolos_contestados
```

- Contestados do card EO usam a população de **auditados** (todos os tipos
  Contestação*), não só as falhas.
- Procedentes no EO = **falhas** de contestação (protocolos distintos) —
  mesma base Qualidade, sem depender de `AuditoriaAtividadeProtocolo.situacao`.
- Mesmo `date_axis` e mesmos filtros dimensionais do EO (período, cliente,
  localidade, líder, agente, etc.).
- Quando houver mais de um registro para o mesmo caso, prevalece a maior
  `data_analise`; em empate, prevalece o maior `ID`. O `ID` é usado apenas
  internamente como desempate determinístico e não é exposto no payload.
- Comparação com período anterior: `shift_period` (mesma regra do EO).

## Aba Cliente (visão 360°)

Endpoint: `module=cliente` → `build_client_dashboard`.

**Cliente obrigatório** (`id_cliente`). Sem cliente → `{ requires_cliente: true }`
(sem agregados de carteira nesta aba).

Definições (mesma população filtrada / mesmo `date_axis`):

```
Protocolos auditados = COUNT(DISTINCT protocolo) em auditados
Protocolos com falha = COUNT(DISTINCT protocolo) em falhas
Taxa de falha = protocolos com falha ÷ protocolos auditados
Protocolos contestados = COUNT(DISTINCT protocolo) em falhas de contestação
Taxa de contestação sobre falhas = protocolos contestados ÷ protocolos com falha
Eventos de contestação = COUNT(falhas de contestação)
Taxa de procedência = contestações procedentes ÷ contestações finalizadas
```

- UI exibe **EO** / **Impacto** (não “ponderado”), sem mudar fórmulas internas.
- Índice de saúde: status `em_definicao` até regra oficial publicada (`score = null`).
- Benchmark: cliente × carteira (mesmo período, filtros sem `id_cliente`).
- Procedência: best-effort via `AuditoriaAtividadeProtocolo` quando disponível;
  caso contrário `available: false` e UI mostra “Sem dados”.

## Ranking / quartis (não alterados nesta sprint)

- Quartil: ordenação por EO desc; Q1 = melhor 25%.
- EO do resumo de quartil = **média** dos EO individuais (não EO agregado).
- Líder: denominador = soma de auditados das matrículas que aparecem nas falhas daquele líder.

## Métrica oficial (`official-v1`)

Parâmetro estável: `metric_mode=official|complete`.

| Modo | Comportamento |
|------|---------------|
| `official` (**default**; ausência ou valor inválido) | Aplica recorte histórico à população **antes** de qualquer cálculo |
| `complete` | População integral (paridade com o comportamento anterior ao toggle) |

### Versão e corte

- Versão: `official-v1`
- Cutover inclusivo: **2026-07-01**
- Campo intrínseco da regra: sempre `data` (`QualidadeAuditado.data` / `QualidadeFalha.data`)
- `date_axis` continua escolhendo o campo do **período visualizado** (`data` ou `data_analise`), sem alterar a decisão oficial

Exemplos:

- `30/06/2026` + classificação bloqueada → fora da métrica oficial
- `01/07/2026` + mesma classificação → entra
- Registro sem `data` → fora da métrica oficial (mesmo com `date_axis=analise`)

### Exclusões históricas (antes de 01/07/2026)

Comparação: igualdade após trim + caixa (sem substring).

**Auditados — `tipo_analise`:**

- AUDITORIA REDOC
- ANÁLISE DIRECIONADA
- ANÁLISE SF
- AUDITORIA DIRECIONADA

Deliberadamente **não** bloqueados: `(APP) Análise direcionada`, `ANÁLISE ESPECIAL - SF`, e qualquer valor que não seja exatamente um dos acima.

**Falhas — condição cumulativa** (basta uma dimensão bloqueada):

- `tipo_analise`: AUDITORIA REDOC, ANÁLISE DIRECIONADA, ANALISE DIRECIONADA, ANÁLISE SF, ANALISE SF, AUDITORIA DIRECIONADA
- `tipo_modulo_2`: ANÁLISE DIRECIONADA, ANALISE DIRECIONADA, ANÁLISES AVULSAS, ANALISES AVULSAS, CONTESTAÇÃO AF, CONTESTACAO AF
- `tipo_falha`: BIOMETRIA, MAPEAMENTO, NECESSIDADE DEVOLUTIVA, PROCESSUAL, SISTEMA

Valores vazios **não** são tratados como bloqueados.

### Granularidade

Preserva a regra **simétrica** atual:

- `grain=etapa`: contar linhas em auditados e falhas
- `grain=protocolo`: protocolos distintos em ambos; protocolo vazio excluído
- Não reproduz a assimetria do DAX (auditados distinct × falhas por linha)

### Alcance

Com Métrica oficial ativa, a mesma população alimenta EO real, EO ponderado, impacto,
séries, Δ M-1, tipificação, criticidade, breakdowns, Pareto, rankings, quartis, Resumo,
Agentes, detalhe, Cliente, contestações, prioridades, documentos/cenários, listas e
exportações que consomem `filtered_auditados` / `filtered_falhas`.

Fórmulas de EO **não** mudam — só a população.

### Ataque de fraude

O DAX original isenta restrição por `Data diferença`. O EO não filtra por
`data_diferenca` hoje. Se esse filtro for criado no futuro, **Ataque de fraude**
não deve ser restringido por ele. Isso não dispensa as demais regras de data nem
as exclusões históricas acima.

### Legados fora do indicador

`REGISTROS_PONTUAIS` e `AUDITADOS_CONTESTADOS` são legados sem vínculo com o
indicador atual — não modelados, não importados à parte.

### Datas futuras (Console Ops)

Diagnóstico somente leitura em `/planejamento/portal-ops`
(`GET /api/v1/portal-ops/data-quality/qualidade-dates/`).

Não entra na API pública de Qualidade, não bloqueia métrica/importação e status
com casos futuros = `attention`.

## Chave composta protocolo + matrícula (projeção Intranet)

Regra de negócio: **o mesmo protocolo não pode ser atribuído ao mesmo operador**
duas vezes na base de **falhas** do indicador.

- Chave: `case_key = normalize(protocolo) + "|" + normalize(matricula)` (paridade BRB `chave_caso`).
- Escopo: **somente falhas** (`qualidade_falha`). Auditados continuam independentes.
- Matrícula vazia (ex.: Processual / Automático sem operador) **não** entra na regra.
- Projeção Intranet (`intranet_source.sync_one`): ao detectar duplicata com registro **mais recente**,
  **ignora a falha incoming** (`sync_status=skipped`, motivo `duplicate_protocolo_matricula`).
  Se o incoming tiver **`data` (auditoria) mais antiga**, **substitui** a falha existente
  (`falhas_replaced` no relatório de sync).
- Import TSV mensal/retroativo de falhas: deduplica no arquivo e contra a base;
  mantém auditoria mais antiga (`data`); preview lista conflitos em `case_key_conflicts`.
- Comando retroativo: `python manage.py dedupe_qualidade_falhas --dry-run|--apply`.
- Console Ops: `GET /api/v1/portal-ops/data-quality/qualidade-case-key-dupes/`.
- Cadastro na auditoria: validação futura; reutiliza o mesmo helper `case_key`.

## Inconsistências conhecidas (documentadas, não “corrigidas” silenciosamente)

1. Datas distintas entre auditados e falhas.
2. Populações não joinadas por protocolo.
3. Processual com denominador total (decisão de negócio pendente).
4. Ranking por líder assimétrico.
