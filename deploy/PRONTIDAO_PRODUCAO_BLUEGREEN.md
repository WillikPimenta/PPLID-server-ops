> Atualização de 27/11/2026: builds e verificações funcionais locais aprovados, mas a carga de 150 sessões falhou. Produção continua bloqueada. O resultado atual está em [RELATORIO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md](../RELATORIO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md); os números abaixo são históricos.

# Prontidão para produção — execução de 26/11/2026

**Decisão atual: NÃO LIBERADO.** As correções de infraestrutura avançaram e os testes locais foram ampliados. A execução do [plano](../PLANO_PRONTIDAO_PRODUCAO_BLUE_GREEN.md) não deve ser confundida com aprovação dos requisitos que ainda dependem do produto e do servidor.

**Atualização posterior — etapa 1:** foi implementada drenagem cooperativa com confirmação, identificação de subprocessos e preservação em timeout/queda, removendo `stop_all()` do encerramento de deploy. A candidata da aplicação é `6ab3f747`; a auditoria de dependências de produção caiu de seis entradas altas para zero. A consolidação passou a **100 testes distintos de operações aprovados**, além dos testes selecionados da aplicação. Há falhas preexistentes na suíte do frontend e sete alertas em ferramentas de desenvolvimento. Consultar [entrega, evidências e limites da etapa 1](ETAPA_1_WORKERS_DEPENDENCIAS.md). Os resultados anteriores abaixo permanecem como histórico; ainda faltam workloads/integração painel-supervisor, carga representativa e piloto no servidor.

## Escopo autorizado e ambientes

O usuário autorizou usar MAIN e informou pico de 150 usuários. Aplicação nesta máquina: `C:\Users\c93123a\PPLID`; instalação no servidor: `C:\PPLID`. Na inspeção local não havia os arquivos padrão `machine.config.json` nem listeners nas portas 8000, 5173 e 5190. Um checkout da aplicação não comprova uma instalação operacional equivalente à do servidor.

Testes de falha, builds, bancos e carga foram executados em `.local/bluegreen-*`, com portas de loopback e dados sintéticos. MAIN permanece legado na configuração versionada; não houve promoção, parada de serviço, alteração de task ou ativação corporativa.

## Correções realizadas

- Promoção que falha após persistir a troca volta à versão anterior saudável. A decisão considera o estado efetivo, incluindo transação interrompida; não depende de `switched=False` local.
- Falha da própria reversão preserva os dois diagnósticos e marca `recovery-required`. O SHA bom e o histórico são reconciliados quando a reversão é confirmada.
- A intenção de troca é mantida até verificar o atendimento público. Ativação e rollback também registram falhas recuperáveis.
- Retirada de um slot limpa suas referências de `previous`/`candidate` de forma durável; recovery também reconcilia a identidade da candidata.
- Supervisor usa diretório e readiness exclusivos por instância, verifica SHA/instance ID/processo e reconhece o interpretador filho do launcher Python do Windows.
- Lançamento do supervisor registra intenção antes de criar o processo; perda da gravação do PID pode ser reconciliada. Start após parada confirmada cria nova instância; saída inesperada bloqueia substituição.
- Gravação do status compartilhado usa `Global\PPLID-Deploy-Status`, o mesmo mutex do legado. Teste com três processos preservou MAIN, DEV e HOM.
- Logger PowerShell respeita `OPS_MACHINE_CONFIG` e `PPLID_OPS_STORE_PATH`, sem recorrer ao caminho padrão quando uma configuração explícita é inválida.
- Relatório de migrações usa os marcadores de `showmigrations --plan`. O código anterior procurava `[ ]` na saída de `migrate --plan` e podia informar falsamente zero pendências. O bloqueio do slot continua usando `MigrationExecutor`.
- Administração do Caddy reconcilia leitura/resposta perdida após `/load` verificando o hash real da configuração, sem repetir o POST. Rejeição HTTP explícita continua sendo erro.

Na rodada anterior, código e testes estavam na branch `feat/bluegreen-deploy-20261126`, commit `566a0bb`, worktree `.local/bluegreen-review-ops`, e a aplicação de revisão estava em `b683f027`. A versão anterior de teste recebeu os mesmos guards em `3232216c`, branch `test/bluegreen-r1-20261126`, baseada no código funcional `8cda7a82`. Os commits atuais estão no relatório da etapa 1.

## Situação por etapa do plano

| Etapa | Situação ao encerrar esta execução |
|---|---|
| A — Rollback | Concluída nos casos definidos e cobertos. |
| B — Interrupções/concorrência | Parcial; cenários adicionais aprovados, matriz completa de build/manutenção ainda aberta. |
| C — Releases limpas | Builds anteriores e troca real aprovados; candidata final `6ab3f747` construída em venv nova com constraints e `pip check` aprovado. Falta validar fluxos/gatilhos sobre a candidata final na instalação de destino. |
| D — Workers | Drenagem implementada e validada localmente na etapa 1; workloads reais e integração painel/supervisor ainda pendentes. |
| E — Aplicação | Autenticação, sessão, CSRF e assets aprovados entre versões; matriz funcional completa pendente. |
| F — Capacidade | Ensaio curto de transporte com 150 clientes aprovado; baseline/carga representativa pendentes. |
| G — Piloto corporativo | Não executado; a inspeção desta máquina não equivale ao servidor em `C:\PPLID`. |
| H — Liberação | Relatórios e commits preparados para revisão; pacote de produção ainda não aprovado. |

## Testes automatizados

Suíte principal: **78 aprovados**, em 589,30 s, incluindo regressão do console, transferências de 6 MiB, supervisor com jobs sintéticos e teste de 150 clientes. Houve um aviso de depreciação na instalação global de `requests/urllib3` usada pelo harness; os ambientes virtuais limpos tiveram verificação de dependências própria.

Os ensaios adicionais incluem contagem real de migrações pendentes, interrupção antes da gravação de PID e antes/depois da troca, recovery repetido, falha de rollback e leitura administrativa transitória. Consultar os XMLs para os resultados finais; não somar reexecuções como casos novos.

**Total consolidado da rodada anterior: 85 casos distintos aprovados** — 78 na suíte principal, um de contagem real de migrations, três pontos de interrupção e três casos do controle administrativo. Reexecuções direcionadas de casos já contados não aumentam esse total. Após o ajuste da fase do supervisor naquela rodada, sete casos pertinentes foram reexecutados e passaram. A etapa 1 ampliou o conjunto para 100 casos distintos.

Histórico de falhas preservado:

- A primeira regressão de rollback falhou como esperado antes da correção.
- A primeira suíte ampliada teve 73 aprovações e três falhas no novo harness PowerShell, por faltar o modo de execução já usado pelos scripts do repositório. O harness foi corrigido; os três casos e a suíte principal seguinte passaram.
- Um ensaio adicional falhou na leitura administrativa do Caddy após ativação, antes de chegar ao ponto de interrupção solicitado. Isso motivou a reconciliação descrita acima. A matriz foi repetida após a correção.

Evidências: `.local/bluegreen-readiness/run-20261126/pytest-final.xml`, `pytest-boundaries.xml`, `pytest-control-recovery.xml`, `pytest-admin.xml`, `pytest-schema.xml`, `pytest-concurrency.xml` e `pytest-latest.xml`, com logs ao lado.

## Ensaio de 150 clientes

| Medida | Resultado |
|---|---:|
| Clientes simultâneos | 150 |
| Duração observada | 39,39 s |
| Requisições | 11.151 |
| Erros observados | 0 |
| p50 | 9,55 ms |
| p95 | 38,43 ms |
| Máxima | 276,97 ms |

Foram usadas sessões persistentes com retries desabilitados, mistura de HTML e API sintética, promoção e três reversões entre slots. Isso testa o transporte sob concorrência. Não mede dashboards, consultas, dados e robôs corporativos de 150 usuários; não comprova a meta de p95 contra baseline legado nem os ensaios prolongados do plano.

Fonte: `.local/bluegreen-readiness-full-02/test_150_synthetic_clients_dur0/load-150-summary.json`.

## Builds e releases reais

O primeiro build limpo completo de `b683f027` passou pelo entrypoint real `deploy/bluegreen_prepare.ps1`: venv nova sem pacotes globais, instalação declarada, `manage.py check`, `npm ci`, Vite e validação de staging, em PostgreSQL descartável. `pip check` retornou zero. O pip gerou alterações apenas nos metadados versionados de `sistematest.egg-info`; isso foi registrado separadamente dos fontes limpos.

Foi criada a ferramenta `deploy.bluegreen.readiness_build` para reproduzir o build real em banco descartável, preservar logs e versões resolvidas, fornecer perfil temporário aos subprocessos e retomar um laboratório parado para construir outro commit. `readiness_smoke` valida duas releases construídas, migra apenas o banco descartável, verifica autenticação/sessão/CSRF, assets e navegador opcional.

Os dois builds limpos, `3232216c` e `b683f027`, passaram pelo pipeline real; ambos tiveram `pip check` sem conflitos. A validação da versão anterior registrou corretamente 603 migrações pendentes no banco novo, sem executá-las durante o build. O ensaio entre versões prepara o esquema separadamente nesse banco descartável.

Esses SHAs são versões de ensaio. A release final de MAIN precisa incorporar as correções na branch aprovada da aplicação e repetir os ensaios afetados. O snapshot de versões Python resolvidas ficou nos artefatos; não foi introduzido um lock de dependências com hashes no repositório da aplicação.

**Ensaio de tráfego entre versões: aprovado no laboratório.** Foram aplicadas 603 migrações somente no banco descartável. A versão `3232216c` iniciou, autenticou no navegador e foi substituída por `b683f027`; sessão e CSRF permaneceram válidos após promoção e rollback. O navegador ficou restrito às duas portas locais do laboratório. Os chunks exclusivos da versão antiga foram buscados e conferidos por hash; havia 270 nomes de arquivos JavaScript distintos entre os conjuntos das duas versões. A imagem final foi inspecionada e mostrou a tela Vue de login após logout.

As duas árvores de processos web somaram cerca de 562 MiB num snapshot aquecido sem carga de negócio. Isso não representa pico, memória do PostgreSQL/navegador/build ou dimensionamento para 150 usuários reais.

Os resultados estão nos arquivos `report-<sha>.json`, `smoke-report.json`, `artifact-manifest-<sha>.json`, `pip-freeze-<sha>.txt` e nas imagens `clean-browser-before.png` / `clean-browser-after.png` do laboratório `.local/bluegreen-readiness/build-5433c6a8`. O consolidado legível por máquina é `.local/bluegreen-readiness/run-20261126/validation-summary.json`. A inspeção final confirmou zero processos web/proxy de laboratório restantes e PostgreSQL encerrado.

Na primeira tentativa de build, o pip recusou o certificado TLS e o logger tentou o caminho padrão de operações, falhando por tabela ausente. O logger foi corrigido e as tentativas seguintes usaram SQLite de logs explícito no laboratório. Não houve ação em listener ou banco de negócio. O pip usou certificados confiáveis do Windows com `PIP_USE_FEATURE=truststore`, mantendo a verificação HTTPS, conforme a [documentação oficial](https://pip.pypa.io/en/stable/topics/https-certificates/).

## Requisitos ainda abertos para liberação

| Requisito | O que falta |
|---|---|
| Parada/atualização dos robôs | Quiescência, drenagem e identidade implementadas; falta homologar jobs reais, retomada e interação painel/supervisor. Ver [inventário](INVENTARIO_WORKERS_BLUEGREEN.md). |
| Dependências e regressão da aplicação | Auditoria de produção zerada na etapa 1; revisar sete entradas de desenvolvimento e falhas preexistentes de testes. Ver [relatório](PENDENCIAS_DEPENDENCIAS_BLUEGREEN.md). |
| Capacidade representativa | Baseline e carga prolongada com telas/consultas/dados/jobs reais, 150 usuários e memória/disco do servidor. |
| Aplicação completa | Matriz funcional de `/falhas/`, arquivos nos limites reais, contratos das APIs usadas por abas antigas e navegação lazy real. |
| Recuperação abrangente | Interrupção de build e subprocessos pip/npm, todas as combinações concorrentes de manutenção e recuperação após queda de jobs de negócio; gatilhos completos watcher/console com nova release em instalação equivalente à do servidor. |
| Instalação corporativa | Conferir configuração, conta, binários, portas/rede e recursos no servidor; ensaiar tasks, logon/reboot e console sob carga. |
| Primeira entrada e retorno | Identificar listeners legados e donos dos jobs; ensaiar transferência, limites de tempo e retorno completo ao legado. |
| Retenção | Definir e testar retenção de assets/logs/releases sem quebrar clientes antigos nem remover fonte do supervisor. |

O limite máximo real de upload e os tempos aceitáveis de primeira ativação, rollback e recuperação ainda precisam ser definidos. Os testes locais não fornecem esses requisitos do negócio.

## Próxima sequência necessária

Concluir a homologação de workloads e aplicação sobre a candidata da etapa 1, tratar a triagem residual e executar o piloto autorizado em MAIN com os limites e procedimentos concretos do servidor. A autorização de uso de MAIN já foi dada; o impedimento atual é técnico e de evidência operacional.
