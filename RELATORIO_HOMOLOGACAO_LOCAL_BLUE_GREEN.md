# Relatório de homologação local blue-green

## Decisão atual

**APROVAÇÃO LOCAL PARCIAL — NO_GO PARA PRODUÇÃO COM 150 USUÁRIOS.** As regressões selecionadas, tipos, builds, manifestos e a mecânica de troca/rollback com navegador passaram. O ensaio HTTP de 150 usuários falhou e foi interrompido antes dos 120 segundos previstos. A capacidade de atendimento exigida não foi demonstrada; os 1.375 testes de frontend e 421 de backend não substituem essa verificação. Este documento não representa execução do piloto em `C:\PPLID`.

O plano de origem é [PLANO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md](PLANO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md). Na retomada de 27/11/2026, o usuário priorizou agilidade e autorizou dispensar repetições desnecessárias: a etapa G local foi reduzida ao ensaio curto descrito neste relatório. A autorização anterior para MAIN está registrada; a dependência remanescente é técnica e de ambiente. Pico informado: 150 usuários simultâneos.

## Revisões e isolamento

| Item | Estado persistido |
|---|---|
| Aplicação de revisão | `.local/bluegreen-review-app`, branch `feat/bluegreen-web-only-20261126` |
| R1 de laboratório congelada | `1cb6444748473ced40eea6b51daf5f4162424890` |
| Operações de revisão | `.local/bluegreen-review-ops`, branch `feat/bluegreen-deploy-20261126` |
| Código de operações congelado para o ensaio | `1b5b37fad07042337c2834d30725ef6b6ee0dea4`; documentos finais ainda em atualização |
| Conteúdo testado | Regressão aprovada inclui a correção funcional que distingue R2 de R1; não atribuir o resultado integral a R1 |
| R2 de laboratório congelada | `fac41fdd35bb07c0967c8599c4a38b8c419516e2`; corrige o filtro de pares incompletos |
| Builds R1/R2 | APROVADOS em `.local/bluegreen-readiness/build-b3b05159`; ambos com pipeline 0, `pip check` 0, fonte limpa e PostgreSQL parado ao concluir o build |
| Manifestos das releases | R1: 5.891 artefatos; R2: 5.892 artefatos; 84 pins Python conferidos em cada venv nova |
| Materiais de operações em runtime | 52 arquivos comparados com o código da revisão, igualdade normalizada confirmada em `resume-20261127/ops-materials.json` |
| Evidências | `.local/bluegreen-local-20261126-151916/` no workspace de operações |
| Referência anterior | [Etapa 1](deploy/ETAPA_1_WORKERS_DEPENDENCIAS.md), sem substituir a regressão atual |

Checkouts originais contêm alterações preexistentes e devem permanecer preservados. Os ensaios usam conta comum, perfil e dados sintéticos, PostgreSQL descartável, portas de loopback e bloqueio de saídas externas nos processos de teste. Não foram alteradas tasks corporativas ou ativado MAIN no servidor por esta homologação.

## Matriz de resultados preservados

Os caminhos de evidência na tabela são relativos ao diretório indicado acima. Rodadas repetidas não contam como casos novos.

| Área | Resultado observado | Evidência / pendência |
|---|---|---|
| Baseline e conta | Registrados SHAs, versões, recursos e condição sem administrador | `baseline.json`, `account-tools-proof.json` |
| Frontend, baseline Vitest | 1.369 casos: 1.323 aprovados e 46 falhas | `tests/components-vitest-before.json`; um caso QR já corrigido durante a rodada foi reproduzido separadamente no HEAD original |
| Frontend, correções direcionadas | 110/110 aprovados; JSON completo com `success=true` | `tests/components-targeted-after-2.json`; não equivale à regressão completa |
| Assert/tsx | 32/32 aprovados | `tests/frontend-assert-final.json` e `.log` |
| Tipos, vue-tsc 3.3.14 | APROVADO após quatro correções de compilação; saída 0 | `tests/frontend-typecheck-fixes.json`, `frontend-reboot-build-summary.json`; heap Node de 4.096 MiB |
| Build de frontend | APROVADO, saída 0, 105,714 segundos | `tests/build-after-reboot-outside-sandbox.log`, `frontend-reboot-build-summary.json`; configuração de laboratório, não artefato de produção |
| Regressões das correções de tipos | 16/16 aprovadas | `tests/type-fixes-targeted-after-reboot.json`; dois arquivos afetados |
| Vitest completo | 1.375/1.375 casos aprovados, 270 arquivos, zero falhas/pendências, saída 0 | `tests/components-vitest-after-reboot.json`, `frontend-reboot-vitest-summary.json`, `pause-checkpoint.json`; conteúdo corresponde à candidata com a correção planejada para R2 |
| npm produção | Zero entradas em todas as severidades, código de saída 0 | `audits/npm-production-final.json`, `final-exit-codes.json` |
| npm completo | Zero entradas em todas as severidades, código de saída 0 | `audits/npm-full-final.json`, `final-exit-codes.json` |
| Dependências de produção | 61 entradas de lock comparadas, sem alteração; XLSX preservado | `audits/production-lock-comparison.json` |
| Backend funcional inicial | 380 casos, 10 falhas e 52 erros; rodada não aprovada | `functional/application-01/commands.log`; falhas triadas entre novo defeito UUID, fixtures/contratos e ambiente |
| Backend direcionado anterior | 117 aprovados com PostgreSQL e hasher real de senha | `functional/menu-migrations-01/commands.log`, `report-history-486fcf1b.json`; preservado como evidência complementar |
| Backend, regressão selecionada consolidada | 421 aprovados, zero falhas; PostgreSQL encerrado | `functional/menu-migrations-01/commands.log` e `pause-checkpoint.json`; inclui contas, access/RBAC, menus, falhas/relatórios, eventos, escala, suporte, configuração/SLA, drenagem e limites de upload |
| Aceleração de fixtures backend | A rodada de 421 usa MD5 somente em fixtures; produção não alterada | `pause-checkpoint.json` registra o override; os 117 anteriores incluem PBKDF2 real. Não declarar que a rodada acelerada mede custo de autenticação em produção |
| Drenagem nos entrypoints reais | 10/10 aprovados, incluindo seis ciclos controlados adicionais | `functional/worker-entrypoints-01/commands.log`; efeitos persistidos e nenhuma nova admissão após drenagem |
| Canal supervisor | 15 casos aprovados, mais rodada posterior de 2 casos UUID/middleware aprovada | `tests/supervisor-control/unittest-03.log`, `unittest-04.log`; não somar reexecuções como novos casos |
| Segurança de operações com Caddy | 20 aprovados | `tests/ops-safety-caddy.xml` |
| Regressão de operações anterior | 67 aprovados, 1 falha, zero skips, 68 casos | `tests/ops-final-01.xml` e `.log`; falha repetida com sucesso na rodada abaixo |
| Recursos, segurança e gerador de carga | 29/29 aprovados, zero skips | `tests/ops-resource-recovery-02.xml`; inclui checks afetados pelo guard de commit e pelo encaminhamento de configuração |
| Promoção, recursos e transferência durante troca | 26/26 aprovados, zero skips; inclui o único caso falho da rodada anterior | `tests/ops-resource-transfer-03.xml`; recusa por recursos ocorre antes de retirar o slot aquecido |
| Entrypoints, transações, recusa de build e guards mistos | 18/18 aprovados, zero skips | `tests/ops-pipeline-transaction-short-path.xml` |
| Contenção/interrupção de build e recibo obrigatório | 19/19 aprovados, zero skips | `tests/build-process-after-reboot-final.xml`; cenários de árvore de build controlada |
| Recuperação de build por sessão | 6/6 aprovados, zero skips | `tests/build-session-recovery-final.xml` |
| Proteção da release fixada pelo supervisor | 5/5 aprovados, zero skips | `tests/build-pinned-release-final.xml` |
| Guards de manutenção | 9/9 aprovados, zero skips | `tests/ops-maintenance-guards-02.xml`; são rodadas parcialmente sobrepostas, sem somar como casos distintos |
| Novo comando build-only antes da primeira ativação | 9/9 casos focais aprovados | `tests/initial-build-only-final.xml`; implementação `d022285`, complementada pela recusa entre sessões em `7e013a9`; sem promover ou iniciar slot |
| Executor real da migration de menus | 2/2 casos aprovados em aproximadamente 45 segundos; PostgreSQL parado | `functional/menu-migrations-01/commands.log` e `report.json`; complemento à regressão de 421, sem somar reexecuções |
| Duas releases limpas finais | APROVADAS: pipeline e `pip check` com saída 0 em ambas | `report-1cb64447.json` e `report-fac41fdd.json` no laboratório `build-b3b05159`; `sourceIncludesWorkingChanges=false` |
| Integridade e versões instaladas | R1: 5.891 artefatos; R2: 5.892; 84/84 constraints conferidas em cada uma | `releases/artifact-manifest-1cb64447.json`, `artifact-manifest-fac41fdd.json`; hashes de fontes/dist, constraints, lockfile e protocolos |
| Restauração de esquema sintético | Snapshot real de laboratório anterior restaurado em banco de destino vazio; migration `access.0046` aplicada no primeiro smoke | Origem `.local/bluegreen-readiness/build-5433c6a8`; não usa dados corporativos nem equivale à restauração de backup de produção |
| Smoke de troca/rollback e navegador | Verificações parciais aprovadas: R1 → R2 → R1 → R2, sessão, documento original, navegação lazy e 135 assets antigos com hashes preservados | `smoke-report-before-resume-7767b40e.json` no laboratório; relatório integrado ficou `ok=false` por erro posterior do harness antes da carga |
| Ensaio HTTP curto, 150 usuários | FALHOU: 120 segundos planejados, aborto em 31,66 segundos | 220 requisições: 80 HTTP 200, 84 HTTP 503 e 56 timeouts de leitura; p95 de 30.016 ms |
| Sondas e transição sob carga | 202 sondas, 99 falhas; uma tentativa de promoção para R1 aos 10,03 segundos recusada por `HTTPError` antes de trocar | Não houve promoção concluída nessa rodada; não declarar ausência de tentativa ou disponibilidade contínua |
| Encerramento da última tentativa | PostgreSQL e supervisor parados conforme relatórios | `smoke-report.json`: `ok=false`, `postgresStopped=true`, `extended.supervisorStopped=true` no laboratório `build-b3b05159`; auditoria final consolidada de processos a registrar |
| Carga prolongada e comparação legado/blue-green | ADIADAS, não aprovadas; fora do escopo local acelerado | Sem rampa 10/50/100/150, baseline de uma hora, sustentação de 30 minutos ou cenário misto de 300 segundos; qualificação adicional no servidor conforme necessidade de operação |
| Piloto/rede/tasks/integrações reais | NÃO EXECUTADO | Depende do servidor e de destinos reais de homologação |

## Correções e contratos em revisão

O painel web passou a controlar o executor independente por canal JSON em loopback, vinculado à conta Windows por DPAPI. Ambiente, geração e identidade de processo são conferidos; métodos são explicitamente permitidos e comandos possuem identidade de retry. O transporte não repete mutações automaticamente. Um defeito de serialização de UUID do usuário real foi encontrado na regressão funcional e corrigido nas duas cópias do protocolo, nos commits de aplicação `d76e27bc` e operações `ca198c4`. Ambos precisam integrar os SHAs congelados das releases. Ver [SUPERVISOR_CONTROL.md](deploy/SUPERVISOR_CONTROL.md).

As falhas de frontend foram classificadas por contrato/fixture, runner e comportamento funcional. A correção de identidade duplicada dos menus separa Planejamento (`planejamento-performance-desenvolvimento`) de Operações (`performance-desenvolvimento`). A migration `access.0046_split_planning_performance_menu_key` conserva PK e campos existentes, transforma apenas referências da seção Planejamento e aborta atomicamente em conflito. `migrate_preserving_rbac` permite somente essa transformação revisada dentro da preservação de configuração; não amplia grants. Existem casos de regressão para preservação, conflito, reversão e rollback transacional.

No frontend, o runner assert/tsx recebeu o ambiente necessário e fixtures foram alinhadas às regras verificadas na implementação. Há correção funcional do helper de importação histórica para respeitar a opção de ignorar pares incompletos; ela deve ficar identificável no par de releases e no relatório final. Alterações de expectativas de testes não substituem a validação de comportamento e permissões.

A cadeia de desenvolvimento foi corrigida com `vue-tsc` 3.3.14 e overrides restritos de `js-beautify` para `editorconfig` 4.0.1 e `glob` 14.0.2, preservando as entradas de produção comparadas. Os commits separados são `727af9c9` (dependências), `ba763fdc` (identidade de menu/RBAC) e `fb68c4f3` (runner e fixtures assert/tsx). A versão mínima de Node precisa acompanhar o `engines` efetivo: `^22.12.0 || >=24.0.0`. Auditorias são uma fotografia da execução, não garantia sobre advisories futuros. Referências oficiais e decisões constam em `audits/dependency-review.json`.

O compilador atualizado encontrou quatro erros efetivos de tipos, corrigidos em `72625cd1`: exportação do alias de componente Vue com seu tipo público preservado, remoção de escrita em computed readonly do painel Jira, remoção de ref não utilizada em Produtividade Fiscal e conversão explícita do índice numérico em Development. Tipos, build, os 16 casos direcionados adicionais e a rodada completa Vitest de 1.375 casos foram aprovados. Node 22.14.0 foi a versão executada; o requisito declarado permanece `^22.12.0 || >=24.0.0`.

O par R1/R2 é exclusivo do laboratório e serve para comprovar a mecânica entre duas versões controladas; R1 não é a release instalada em produção. R1 (`1cb6444748473ced40eea6b51daf5f4162424890`) conserva um comportamento preexistente: com a opção de ignorar pares incompletos marcada, o frontend ainda envia órfãos, que o backend ignora. R2 corrige esse filtro e contém seu teste correspondente. A rodada verde de 1.375 casos incluiu a correção; não se atribui esse resultado integral a R1. O retorno R2 → R1 demonstrará também esse comportamento antigo conhecido. O piloto real continua exigindo conferir/testar o SHA efetivamente instalado no servidor e seu candidato compatível.

Os dois builds usaram venvs novas. O cache de pacotes pip foi reutilizado para reduzir downloads; não houve clonagem de venv a partir de `current`/`previous`. Cada relatório registra fonte limpa, build/validação real e `pip check` aprovado. Os manifestos conferem as 84 versões exatas instaladas sem importar Django ou inicializar a aplicação. Dois gitlinks temporários preexistentes em `.tmp-local` foram explicitamente excluídos e identificados; não são submódulos de runtime. Metadados gerados pelo pip continuam representados pelos hashes no manifesto, sem serem confundidos com alteração funcional dos fontes. A correção dessa política ficou no helper de manifesto (`ecab81d4`), sem modificar o motor de deploy.

O primeiro smoke restaurou um snapshot PostgreSQL real de laboratório anterior em banco vazio e aplicou `access.0046`. A tentativa anterior sobre uma origem vazia foi NOOP e não criou dump; ela não é a evidência de restauração. As verificações concluídas depois da restauração incluem as quatro versões do percurso, login/montagem com administrador sintético, manutenção do documento do navegador, rota carregada lazy e integridade dos 135 assets antigos. Isso não constitui matriz de todos os perfis/rotas corporativos.

Essa primeira execução integrada falhou antes de iniciar a carga: o harness esperava `results` na API de casos, cujo contrato usa `items`. A correção é do helper de laboratório; não alterou produto ou builds. O erro e o relatório parcial foram preservados. PostgreSQL e supervisor foram parados nessa tentativa e o journal ficou `stopped`. A retomada verifica slots vazios, recibo de parada e zero processos nativos pertencentes à geração, reaproveita as verificações anteriores por referência/hash e não apaga o journal nem repete os 135 assets/navegador. As verificações anteriores de navegador, sessão, quatro transições e assets são evidências aprovadas reutilizadas; **o resultado integrado final é falho por atendimento HTTP sob carga**. Não alegar que a última execução concluiu o trecho final de CSRF/logout após a carga abortada.

## Resultado final da carga curta

O perfil usado inicia 150 usuários em uma rajada de zero a dois segundos, com jornadas a cada cinco segundos. Depois de corrigir o limite de conexões para 256 (`f0deceaacea35f43297c34d22b485f4c870ce255`), um teste focal com 150 conexões keep-alive, 153 conexões totais e health check de um segundo passou. Esse resultado específico não aprovou o perfil de aplicação: sua repetição voltou a falhar.

Na última rodada, houve aborto em 31,66 segundos dos 120 planejados: 220 requisições, sendo 80 HTTP 200, 84 HTTP 503 e 56 timeouts de leitura; p95 de 30.016 ms. Das 202 sondas, 99 falharam. Uma promoção para R1 foi tentada aos 10,03 segundos e recusada por `HTTPError` antes da troca. A primeira carga falha, anterior à correção de conexões, teve 230 requisições (95 HTTP 200, 103 HTTP 503, 32 timeouts) e nenhuma tentativa de transição; ficou arquivada em `resume-20261127/load-before-connection-fix` e `first-150-load-failure.json`. Não misturar os números ou eventos dessas duas rodadas.

Os dados observados apontam limitação de atendimento HTTP/pool/sondas sob esse perfil: Waitress com 16 threads chegou a fila de 83, e a sonda do Caddy com prazo de um segundo compete na mesma fila. Não foi demonstrada saturação do sistema operacional: as observações anteriores indicavam CPU de 18–45%, cerca de 10 GB de RAM física e 14 GB de margem de commit disponíveis. Não houve baseline legado equivalente; portanto não se atribui a falha exclusivamente ao blue-green, a consultas PostgreSQL ou à capacidade física do host. Também não se comprovou capacidade sustentada nem SLA.

**Próximo passo técnico:** investigar e ajustar a capacidade HTTP e o comportamento conjunto do pool, fila e sondas com jornadas representativas, depois repetir o ensaio afetado com comparação controlada. Essa investigação não foi executada como um novo projeto nesta entrega. O piloto real e a validação de conta/tarefas, rede, recursos, integrações e ownership do servidor continuam obrigatórios; não liberar produção apenas pelas suítes verdes.

Em operações, `34f4cb7` adicionou medição nativa da margem de commit no Windows, conferida junto de RAM e disco. `minimumCommitMb` pode ser configurado explicitamente; na ausência, usa `minimumAvailableMb`. O guard recusa build/start/promoção sob recursos insuficientes, e a promoção confere recursos antes de retirar o slot aquecido. O mesmo commit faz `Config.load` reter o path de máquina selecionado e o pipeline encaminhá-lo a subprocessos por `OPS_MACHINE_CONFIG`, evitando recorrer a outra instalação quando se usa configuração explícita. São travas preventivas; não reservam recursos contra outros processos do host.

## Interrupção, recursos e retomada

Durante execução concorrente de verificações anteriores, houve falhas de alocação em Node/PowerShell/Python e PostgreSQL, inclusive mensagens de falta de memória e falha de criação de processo com código Windows 1455. A rodada RBAC registrou erro de conexão/SSL e resposta 500 nesse contexto; a outra falha veio de fixture que não alcançava a regra de prazo pretendida. **Não classificar todos esses resultados como falha de ambiente sem repetição confirmatória.** A falha de junction em operações foi repetida com sucesso; a causa da primeira ocorrência permanece sem comprovação.

O usuário informou reinício por falta de espaço. Na retomada foram medidos aproximadamente 10,49 GiB de disco livre, 12.098 MiB de RAM física disponível e 20.227 MiB de margem de commit. São valores instantâneos, não uma reserva nem aprovação de capacidade. O teste de tipos interrompido deixou log vazio; o JSON de 110 casos direcionados já estava completo e pode ser preservado.

Na retomada, limitar o heap de Node a 1.536 MiB foi insuficiente para tipos; a rodada concluída usou `NODE_OPTIONS=--max-old-space-size=4096`. Esse limite específico de processo é distinto da falta de margem de commit do sistema observada antes do reboot. O primeiro build após o reinício foi impedido pelo sandbox ao iniciar esbuild; a execução autorizada com a mesma conta fora dessa restrição concluiu em 105,714 segundos. Uma rodada de transações também falhou em quatro casos por paths de teste longos, antes de criar o contrato; o mesmo conjunto em paths curtos passou, com a tentativa anterior preservada em `tests/ops-pipeline-transaction-after-reboot.xml`.

Retomar pelo conteúdo persistido, verificar PostgreSQL/processos pelos registros e identidades e executar ferramentas pesadas sequencialmente. Antes de novas venvs/releases ou carga, conferir tendência de disco, RAM e commit. Não alterar paginação, encerrar processos corporativos nem remover artefatos/evidências indiscriminadamente para concluir a etapa. Provar a parada de laboratórios antes de limpar seu conteúdo.

## Pendências para concluir a homologação local

1. Preservar as regressões aprovadas (421 backend e 1.375 Vitest), tipos, build e auditorias. Repetir apenas checks afetados por nova alteração ou falha concreta; não excluir testes ou relaxar asserções apenas para aprovar.
2. Preservar os checks focais aprovados de build-only (nove casos) e executor de migration de menus (dois casos). Os cenários de contenção de build, recuperação por sessão e release fixada também já possuem rodadas aprovadas.
3. Confirmar o isolamento e a limpeza do ensaio HTTP curto. Limites de upload estão incluídos na regressão backend selecionada; fluxos com destino externo/credenciais continuam separados e não homologados por simulação.
4. Preservar os builds e manifestos aprovados de R1/R2, seus SHAs e a diferença funcional real; reconstruir somente se uma alteração posterior invalidar o artefato correspondente.
5. Preservar as verificações aprovadas de R1 → R2 → R1 → R2, navegador, sessão e assets, separadas da execução integrada falha sob carga. A recuperação das condições necessárias ao encerramento bem-sucedido do ensaio permanece pendente.
6. Resolver a limitação de atendimento HTTP observada e repetir o ensaio de 150 usuários antes de afirmar atendimento desse requisito. O ensaio curto foi executado e falhou; rampa, baseline longo, sustentação de 30 minutos e cenário misto de 300 segundos continuam adiados, sem aprovação presumida.
7. Preencher matriz final, SHAs/hashes, comparação de carga e prova de limpeza. Atualizar os relatórios anteriores preservando seu histórico.

O ensaio foi interrompido por falhas de atendimento, sem prova de saturação física do host. **Não comprova capacidade sustentada de 150 usuários, não estabelece SLA e não sustenta comparação de p95 com baseline legado que não foi medido.** A qualificação prolongada fica fora deste escopo local acelerado e pode ser realizada adicionalmente no servidor, conforme a necessidade real. A mistura sintética não representa automaticamente as jornadas corporativas. Os tempos aceitáveis de parada/retorno e o upload máximo real precisam ser definidos com o negócio.

O checkpoint `pause-checkpoint.json` preserva 1.375 frontend e 421 backend aprovados e zero processos de laboratório identificados no momento da pausa. Isso comprova aquela parada, não substitui a prova final após os novos builds/ensaios de 27/11.

## Saída para o servidor

O [RUNBOOK_PILOTO_SERVIDOR_BLUE_GREEN.md](RUNBOOK_PILOTO_SERVIDOR_BLUE_GREEN.md) descreve preflight, construção com configuração real, migrations explícitas, transferência de ownership, primeira ativação, observação, rollback e retorno ao legado. Seus campos de SHAs e critérios de entrada continuam pendentes até a conclusão das etapas acima.

Permanecem exclusivas da instalação real: conta/task e políticas de execução, rede corporativa, paths e dados, capacidade do host, listeners/jobs legados, destino de integrações, logon/reboot e transferência efetiva de ownership. A primeira ativação pode exigir parada para liberar as portas legadas; rollback web aquecido e retorno completo ao legado são operações distintas.

Artefatos `.local` são evidências locais e podem não estar versionados. Preservar logs/JSON/XML/manifestações necessários sem incluir `.env`, cookies, tokens, credenciais ou dados pessoais no pacote transportado. Prova final de zero processos/bancos de laboratório ativos: **PENDENTE da conclusão das rodadas retomadas**.
