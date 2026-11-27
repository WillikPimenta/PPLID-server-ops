# Plano de homologação local do deploy blue-green

## Estado da execução em 27/11/2026 e escopo acelerado

Na retomada, o usuário priorizou agilidade e autorizou dispensar testes e repetições desnecessários. Os resultados já concluídos serão reaproveitados: 421 casos selecionados de backend, 1.375 casos Vitest em 270 arquivos, tipos/build e auditorias npm sem entradas, além dos ensaios de segurança documentados nas evidências. Repetir somente os checks afetados por mudança posterior ou falha concreta.

Para esta execução local, a etapa G prevê **somente um ensaio curto de 120 segundos com 150 sessões autenticadas, até 16 requisições HTTP de trabalho concorrentes, fila para as demais e trocas de versão**. Esse limite não significa 16 conexões TCP totais ou 150 requisições simultâneas. O usuário esclareceu que falhas ocasionais de requisições são aceitáveis se o retorno for rápido; medir sua frequência/duração sem inventar SLA ou orçamento de erros. A rampa longa, a comparação prolongada com baseline legado, a sustentação de 30 minutos e o cenário misto de 300 segundos ficam **adiados e não contam como aprovados**. O ensaio curto não comprova capacidade sustentada ou comparação de p95 sem baseline.

**Estado final local: trabalho encerrado com limitações; produção não ativada nem liberada.** Builds, `pip check`, 84 pins por release, manifestos, migração, navegador e assets antigos passaram. A sobrecarga anterior sem limite e a primeira carga limitada falha permanecem históricas. Depois de reservar saúde fora da fila, promoção e rollback concluíram com até 16 requisições concorrentes. O teste estrito ficou `ok=false`: 118 requisições, 98 HTTP 200, 20 timeouts e zero HTTP 503; somente 80 dos 150 usuários receberam atendimento. Houve uma falha em 146 sondas; execução de 96,83 dos 120 segundos planejados, sem executar a retirada de slot. Não marcar G como aprovação integral de 150 sessões ou SLA, nem chamar automaticamente 20 falhas de perdas ocasionais aceitas. PostgreSQL/supervisor parados e deploy recuperado. Não realizar mais carga local nesta entrega: o próximo passo é o piloto controlado no servidor `C:\PPLID`, confirmando perfil, aceitação, conta/tarefas, rede e ownership reais. Reutilizar suítes e artefatos válidos.

O corpo original abaixo foi preservado como histórico e referência; esta atualização prevalece onde reduz a duração e as repetições do ensaio local. Consultar os resultados efetivamente obtidos em [RELATORIO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md](RELATORIO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md) e o procedimento posterior em [RUNBOOK_PILOTO_SERVIDOR_BLUE_GREEN.md](RUNBOOK_PILOTO_SERVIDOR_BLUE_GREEN.md).

## 1. Objetivo e resultado esperado

Concluir nesta máquina as correções, os testes funcionais, os ensaios de workers e a preparação das releases que podem anteceder a validação no servidor `C:\PPLID`.

O resultado esperado é um pacote identificado por commits, com duas releases compatíveis, evidências reproduzíveis e procedimento de implantação/retorno. A conclusão deste plano significa **pronto para validação no servidor**, e não liberação automática para produção.

Este documento é um plano de execução futura. As caixas abaixo começam pendentes; resultados anteriores são referência, não aprovação antecipada dos novos testes.

## 2. Ponto de partida

| Item | Referência |
|---|---|
| Repositório de operações | `C:\Users\c93123a\PPLID-server-ops` |
| Aplicação original | `C:\Users\c93123a\PPLID` — preservar alterações de trabalho existentes |
| Aplicação de revisão | `.local/bluegreen-review-app`, branch `feat/bluegreen-web-only-20261126` |
| Candidata validada anteriormente | `6ab3f7479a2daceaf9d0abd5486811e99e2e3bec` |
| Operações de revisão | `.local/bluegreen-review-ops`, branch `feat/bluegreen-deploy-20261126`, último commit documental conhecido `f2cb72d` |
| Implementação do protocolo | Operações `f196754` e `12cb2b2` |
| Pico informado | 150 usuários simultâneos |
| Evidências anteriores | [Etapa 1](deploy/ETAPA_1_WORKERS_DEPENDENCIAS.md) |

Já foram comprovados localmente: build limpo da candidata, `pip check` sem conflitos, 84 versões Python conferidas, auditoria npm de produção sem entradas, 100 casos distintos de operações e testes selecionados de aplicação. Permanecem 47 falhas Vitest, sete arquivos assert/tsx com falhas e sete entradas na auditoria de desenvolvimento, conforme os relatórios anteriores. Reproduzir o estado atual antes de corrigir; os números podem mudar.

## 3. Escopo e isolamento

- Executar com a conta comum desta máquina, sem depender de administrador.
- Trabalhar nas branches/worktrees de revisão. Conferir seu estado antes de editar e preservar alterações de outras tarefas. Não resetar os checkouts originais.
- Criar um diretório novo de evidências, por exemplo `.local/bluegreen-local-AAAAMMDD-HHMM`, com configuração, logs e dados sintéticos próprios.
- Usar PostgreSQL descartável, portas de loopback verificadas e perfil temporário para AppData, OneDrive, arquivos e sessões de navegador.
- Fornecer configurações explícitas de laboratório ao processo e aos filhos. Não herdar `.env`, banco, filas ou credenciais corporativas. Bloquear saídas externas nos ensaios sintéticos.
- Usar simulações locais para integrações externas. Marcar separadamente o que depende de um destino de homologação e não puder ser comprovado aqui.
- Antes de qualquer encerramento de processo de teste, conferir sua identidade e vínculo ao laboratório. Nunca encerrar por nome genérico ou porta.
- Este plano não inclui ativação em produção, alteração de tarefas corporativas, firewall ou serviços do servidor. A autorização anterior para usar MAIN permanece registrada; a implantação real fica na etapa posterior, com os requisitos técnicos satisfeitos.

## 4. Ordem de execução

Executar A → B → C → D → E → F → G → H. Uma falha em uma área não impede investigação independente de outra, mas a consolidação final exige os critérios de saída aplicáveis. Não repetir toda a matriz a cada edição: executar primeiro os casos afetados e consolidar a regressão ao estabilizar a candidata.

### A — Preparar o laboratório e registrar a linha de base

- [ ] Ler este plano, o relatório da etapa 1, o inventário de workers e as instruções locais aplicáveis.
- [ ] Registrar branch, SHA, alterações locais, versões de Python/Node/npm/PostgreSQL/Caddy e condição de conta sem administrador.
- [ ] Conferir memória/disco disponíveis e reservar portas sem conflito; registrar a faixa dinâmica da máquina.
- [ ] Criar configuração sintética explícita e testar que uma configuração ausente/inválida falha sem recorrer a recursos corporativos.
- [ ] Conferir igualdade normalizada do protocolo canônico de operações com a cópia da aplicação.
- [ ] Registrar as suites/comandos existentes e os resultados anteriores, sem executar robôs reais durante descoberta/importação.

**Saída:** `baseline.json`, inventário de configuração sem segredos e laboratório identificável. Se as worktrees tiverem evoluído, registrar o novo ponto de partida; não sobrescrever mudanças para recuperar os SHAs antigos.

### B — Corrigir a suíte do frontend

- [ ] Executar `npm ci` com o lockfile da candidata.
- [ ] Executar as suites Vitest e assert/tsx separadamente, mesmo se o script agregado interromper na primeira falha. Consultar `package.json` e os runners para usar os comandos efetivos da revisão.
- [ ] Exportar resultados estruturados com nome do arquivo, nome do caso, mensagem e duração.
- [ ] Classificar cada falha: defeito de produto, contrato esperado desatualizado, fixture incorreta ou problema do runner/ambiente.
- [ ] Corrigir o acesso a variáveis de ambiente no runner quando esse for o motivo real; preservar os testes que verificam comportamento de produção.
- [ ] Resolver IDs duplicados de menus e divergências entre Vue Router e catálogo RBAC conforme a fonte autoritativa. Conferir que a correção não amplia permissões nem oculta funcionalidades válidas.
- [ ] Investigar o caso de `heavyRequest` e os demais grupos Vitest pelo erro reproduzido, sem assumir que todos têm a mesma causa.
- [ ] Adicionar teste de regressão quando houver defeito funcional; atualizar expectativa apenas com justificativa do contrato correto.
- [ ] Executar suites completas ao estabilizar as correções, mais o comando de tipos existente e `npm run build:deploy`.

**Critério de saída:** suites aplicáveis verdes, compilação e verificação de tipos aprovadas. Não excluir testes, usar `skip` ou relaxar asserções apenas para obter aprovação. Se uma expectativa exigir decisão de produto indisponível, documentar a dúvida e manter o item pendente; não declarar homologação concluída.

**Evidências:** resultados antes/depois, classificação das falhas, commits e justificativa das mudanças de contrato.

### C — Resolver as dependências de desenvolvimento

- [ ] Gerar auditorias atualizadas de produção e completas em JSON, preservando código de saída.
- [ ] Conferir as cadeias de `vue-tsc`, `@vue/language-core`, `minimatch`, `brace-expansion`, `glob`, `editorconfig` e `js-beautify` no lockfile efetivo.
- [ ] Consultar advisories e documentação dos mantenedores no momento da execução para escolher versões corrigidas e compatíveis.
- [ ] Revisar o override de `brace-expansion`; atualizar a cadeia completa quando necessário, sem impor uma versão incompatível só para silenciar o relatório.
- [ ] Se for necessária mudança major de `vue-tsc`, aplicar em commit separado e validar compatibilidade com Vue, TypeScript, configuração e scripts atuais.
- [ ] Repetir instalação limpa, tipos, testes afetados e build; preservar a compatibilidade de XLSX/XLS/CSV.
- [ ] Confirmar que a auditoria de produção continua sem entradas e que as 84 constraints Python continuam coerentes, ou registrar e testar as versões que precisarem mudar.

**Critério de saída:** nenhuma entrada alta/crítica sem resolução ou avaliação explícita. Buscar auditoria completa zerada. Se não houver correção viável, registrar advisory, cadeia, exposição, motivo, mitigação e decisão pendente do responsável; a aceitação de risco não é automática nem equivale a teste aprovado. Não executar `npm audit fix --force` indiscriminadamente.

### D — Homologar aplicação e integração painel/supervisor

Criar banco sintético com usuários e perfis distintos, registros suficientes para filtros/paginação, arquivos válidos e inválidos e estados de fila. Executar migrations somente nesse banco, em preparação explícita; o start de um slot deve continuar recusando esquema pendente.

| Fluxo | Testes obrigatórios | Resultado esperado |
|---|---|---|
| Autenticação | Login, logout, sessão expirada, CSRF e promoção/rollback com aba aberta | Sessão válida preservada; acessos indevidos recusados |
| Permissões | Perfis de leitura/operação/admin da aplicação; acesso direto a rotas e APIs | Mesmas permissões antes/depois da troca, sem depender apenas do menu |
| Navegação | Rotas principais, `/falhas/`, links diretos, carregamento lazy e refresh | Páginas carregam e APIs retornam contratos esperados |
| Consultas | Filtros, ordenação, paginação e exportação nos módulos usados | Dados e resultados conferidos contra fixtures |
| Arquivos | Upload/download, nome especial, arquivo vazio/inválido e limite configurado | Validação previsível, integridade preservada e rejeição controlada |
| Planilhas | XLSX/XLS/CSV com zeros à esquerda, datas, números, fórmulas e múltiplas abas | Leitura/exportação preservam os valores definidos pelo produto |
| Abas antigas | Manter aba de R1 aberta durante R2; chamar APIs e carregar chunks antigos | Compatibilidade demonstrada ou incompatibilidade bloqueada antes do deploy |
| Painel de robôs/SLA | Start, status, pausa/parada e configuração durante operação com dois slots | Painel acompanha e comanda o executor correto, sem duplicação |

- [ ] Descobrir os limites atuais de upload e usá-los em testes de fronteira: abaixo, igual e acima. O requisito máximo de negócio ainda não informado deve continuar pendente.
- [ ] Verificar onde vivem estado, credenciais de sessão e comandos de robot manager/SLA. Um singleton em memória no slot não representa o supervisor independente.
- [ ] Se o painel não alcançar o executor real, implementar um canal de controle/estado adequado, autenticado e restrito à conta/ambiente, com identidade da geração, timeout, concorrência e erros visíveis. Definir esse contrato antes de codificar.
- [ ] Testar painel, supervisor e slots como processos separados; testes com mocks no mesmo processo não comprovam essa integração.
- [ ] Testar respostas ao tentar comandar executor ocupado, em drenagem, indisponível ou de geração anterior.
- [ ] Registrar separadamente os fluxos que usam simulação e os que usam um destino de homologação autorizado. Não chamar sistemas corporativos reais apenas para fechar cobertura.

**Critério de saída:** fluxos críticos aprovados, nenhum erro funcional sem triagem, isolamento de permissões preservado e comandos/estado do painel comprovados entre processos. Dependência externa indisponível permanece identificada como não homologada.

### E — Homologar ciclos reais de workers com trabalho controlado

Usar os entrypoints reais com persistência de teste e adaptadores externos controlados. Cobrir cada classe do [inventário](deploy/INVENTARIO_WORKERS_BLUEGREEN.md): BI/painéis, robôs, SLA, relatórios, férias, escala, suporte, eventos e purga de métricas. Catalogar os modos efetivamente habilitados; não presumir que um teste do supervisor cobre todos.

Para cada classe, registrar: dono do processo, unidade de trabalho, estado persistido, prazo normal, efeitos esperados e forma de retomada.

| Cenário | Verificação |
|---|---|
| Ocioso → drenagem | Acorda da espera e não inicia novo ciclo |
| Trabalho longo → drenagem | Trabalho aceito termina e executa sua limpeza; nenhum cancelamento de deploy é injetado |
| Fila pendente | Itens não admitidos permanecem persistidos para próxima execução |
| Timeout de parada | Executor permanece vivo; recibo ausente; substituição bloqueada |
| Parada repetida | Confirma término após conclusão sem duplicar efeitos |
| Falha no recibo | Escrita recuperada permite conclusão da geração já drenada |
| Queda do supervisor/dono | Filhos preservados; nova admissão fechada; registros incertos bloqueiam substituição |
| Pai termina antes do neto | Família continua contabilizada até o último descendente sair |
| Processo aceita trabalho antes da drenagem | Inicialização tardia do filho não perde a admissão já aceita |
| Duas tentativas de start | Apenas um dono por serviço/modo/lane/slot protegido |
| Promoção/rollback web | Supervisor independente mantém identidade e trabalho atual |
| Retirada de slot | Grupo desse slot drena; grupo ativo e supervisor permanecem disponíveis |
| Reinício/retomada | Estado persistido reconciliado; não presumir retomada de sessão de navegador ou execução exatamente uma vez |
| Modo legado | Sem variável de controle, fluxos legados continuam com seu contrato |

- [ ] Conferir quantidade de efeitos persistidos e IDs dos jobs, não apenas ausência de exceptions.
- [ ] Ensaiar recuperação de registros órfãos com evidência de término e estado de negócio; não apagar leases para forçar start.
- [ ] Documentar casos que exigem reconexão manual de sessão/credenciais e provar que o painel informa essa condição.
- [ ] Executar a suite do protocolo/supervisor e as suites afetadas da aplicação após as correções.

**Critério de saída:** cada classe possui evidência de execução, drenagem e recuperação; nenhum job perdido ou duplicado nos cenários definidos; nenhuma substituição quando o término é incerto. Limitações de destino externo ficam explícitas.

### F — Preparar duas releases e ensaiar troca/rollback

- [ ] Estabilizar e registrar commits limpos da aplicação e de operações. Conferir que o conteúdo testado está versionado, inclusive protocolo, manifesto e dependências.
- [ ] Escolher R1 como versão funcional de retorno e R2 como candidata, ambas com o mesmo protocolo compatível. Não reutilizar automaticamente os antigos SHAs sem contrato e não criar um commit vazio apenas para aparentar teste entre versões.
- [ ] Se R1 precisar receber o contrato atual, aplicar a adaptação em branch isolada e testar seus fluxos. Registrar quais alterações a diferenciam da versão de produção.
- [ ] Construir ambas pelo pipeline real em laboratório novo, com venvs novas e configuração sintética. Conferir `pip check`, constraints, lockfile, manifesto e hashes.
- [ ] Preparar o esquema descartável compatível com ambas. Mudança incompatível de banco bloqueia esse par; não testar rollback de código ignorando o esquema.
- [ ] Executar R1 → R2 → R1 → R2 com tráfego, sessão, CSRF, upload em andamento, assets antigos e supervisor independente.
- [ ] Testar candidata quebrada, falha de readiness, falha de observação pública, interrupção antes/depois da troca, resposta administrativa perdida e falha de reversão.
- [ ] Repetir recovery para comprovar resultado estável e diagnóstico quando houver `recovery-required`.
- [ ] Cobrir build interrompido e subprocessos pip/npm de laboratório; conferir que não há publicação parcial nem limpeza de release em uso.
- [ ] Ensaiar concorrência entre comandos de manutenção, promoção, rollback, cancelamento e prune, preservando releases dos slots e do supervisor.
- [ ] Exercitar gatilhos de watcher/console em configuração de laboratório e conferir que apontam para as releases corretas.
- [ ] Ensaiar retorno ao legado somente numa instalação descartável, incluindo transferência de ownership e reativação dos gatilhos; registrar o que ainda dependerá das tasks reais do servidor.

Ferramentas existentes, a adaptar aos SHAs e caminhos efetivos após leitura do código e de `--help`:

```powershell
python -m deploy.bluegreen.readiness_build --source .local/bluegreen-review-app --pg-bin "C:/Program Files/PostgreSQL/14/bin"
python -m deploy.bluegreen.readiness_smoke --help
python -m pytest tests/bluegreen/test_worker_drain.py tests/bluegreen/test_background.py -q
```

`readiness_smoke` exige duas releases distintas com relatórios de build aprovados e journal de runtime novo. Não apagar um journal existente para contornar a proteção. Usar laboratório apropriado e encerrar seus processos antes de qualquer recuperação de arquivos.

**Critério de saída:** par R1/R2 homologado com esquema compatível, troca/rollback comprovados, sessões e dados preservados, erros diagnosticáveis e limpeza final confirmada.

### G — Carga representativa nesta máquina

O teste anterior de 150 clientes foi sintético e curto. Este ensaio deve exercitar a aplicação e o banco de teste. Seu resultado mede esta máquina e não dimensiona automaticamente o servidor.

- [ ] Definir mistura de jornadas com base nos módulos usados: navegação autenticada, consultas, filtros, exportações e arquivos. Registrar proporções e tempos de interação; não tratar 150 usuários como 150 requisições por segundo.
- [ ] Criar volume de dados sintéticos representativo e registrar suas dimensões.
- [ ] Medir baseline legado isolado e blue-green com os mesmos dados/jornadas; não comparar máquinas ou perfis de carga diferentes.
- [ ] Executar progressão sugerida: 10, 50, 100 e 150 usuários, com aquecimento e cinco minutos por patamar; sustentar 150 por pelo menos 30 minutos se os recursos locais permitirem.
- [ ] Durante a sustentação, realizar promoção, rollback e retirada de slot com sondas contínuas sem retry nas portas públicas.
- [ ] Repetir o patamar relevante com jobs controlados e exportação/upload concorrentes para observar competição por CPU, memória, disco e banco.
- [ ] Registrar falhas do gerador e saturação da própria máquina separadamente de falhas do sistema.

**Métricas:** erros por endpoint, p50/p95/p99, maior intervalo sem resposta, duração de promoção/rollback/drenagem, CPU, memória disponível e por árvore de processos, I/O, espaço livre, conexões/locks do PostgreSQL e filas de trabalho.

**Critérios locais propostos:** nenhuma perda/duplicação de dados ou erro atribuível à troca; nenhuma indisponibilidade observada pelas sondas na frequência registrada; p95 sem piora superior a 20% frente ao baseline equivalente; ausência de crescimento contínuo de memória/fila durante o patamar estável. Tratar 20% e as durações como metas de engenharia deste plano, não como SLA já acordado. Se a máquina saturar, interromper o aumento, registrar o limite e manter o teste de 150 pendente para ambiente com capacidade suficiente.

O tamanho máximo real de upload e os tempos aceitáveis para primeira ativação, retorno e recuperação ainda devem ser definidos com o negócio. Não inventar aprovação desses requisitos usando somente os números do laboratório.

### H — Consolidar a entrega para o servidor

- [ ] Rodar a regressão final necessária sobre os commits finais. Uma correção posterior invalida apenas as evidências afetadas, que devem ser repetidas.
- [ ] Registrar SHAs completos de operações, R1 e R2; hashes dos artefatos; versões das ferramentas; resultados de auditoria e comandos de reprodução.
- [ ] Conferir branches de revisão limpas e preservar as mudanças originais de outros trabalhos. Não misturar os commits de homologação com alterações de negócio não revisadas.
- [ ] Produzir `RELATORIO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md` com matriz aprovado/falhou/não executado, limitações e links para evidências locais.
- [ ] Produzir `RUNBOOK_PILOTO_SERVIDOR_BLUE_GREEN.md` com preflight somente leitura, configuração requerida, ownership, construção dos SHAs, ativação, observação, critérios de abortar, rollback e retorno ao legado.
- [ ] Incluir no runbook a conferência de conta/tarefa e Job Objects, portas, paths, recursos, rede, logon/reinício, dados e binários reais do servidor.
- [ ] Identificar quais valores de configuração pertencem ao servidor. Não entregar `.env` de laboratório nem seu `dist` como pacote pronto para promoção; reconstruir com a configuração correta e validar o artefato resultante.
- [ ] Encerrar somente processos pertencentes ao laboratório e registrar zero processos/bancos de teste ativos; preservar logs e artefatos necessários à revisão.
- [ ] Atualizar os relatórios existentes com o novo resultado, mantendo o histórico e sem marcar produção como liberada.

## 5. Organização das evidências

```text
.local/bluegreen-local-AAAAMMDD-HHMM/
  baseline.json
  tests/                 # XML/JSON e logs, inclusive falhas iniciais
  audits/                # auditorias completas e de produção
  functional/            # matriz de fluxos, fixtures e capturas sem segredos
  workers/               # jobs, efeitos, identidades e recibos
  releases/              # referências de build e manifestos por SHA
  load/                  # jornadas, métricas brutas e comparação de baseline
  cleanup-proof.json
  summary.json
```

Os diretórios `.local` são evidências locais e podem não estar versionados. Versionar os relatórios legíveis e os harnesses reutilizáveis adequados; indicar como preservar/transportar os artefatos sem credenciais, cookies, dados pessoais ou segredos. Não contar reexecuções como novos casos distintos.

## 6. Decisão de conclusão

Marcar **PRONTO PARA VALIDAÇÃO NO SERVIDOR** somente quando:

- [ ] Testes aplicáveis e builds finais aprovados; pendências funcionais críticas resolvidas.
- [ ] Dependências corrigidas ou riscos residuais explicitamente avaliados pelo responsável, sem aprovação presumida.
- [ ] Painel e supervisor comprovados como processos independentes, inclusive SLA.
- [ ] Matriz de workers aprovada para as classes locais testáveis; lacunas externas claramente relacionadas.
- [ ] Duas releases compatíveis e rollback/recuperação ensaiados com estado e dados conferidos.
- [ ] Carga local medida, com limites da máquina e requisitos ainda não definidos registrados.
- [ ] Relatório, SHAs, evidências e runbook prontos e laboratórios encerrados.

Se um requisito não puder ser demonstrado localmente, registrar **PENDENTE DE AMBIENTE/INFORMAÇÃO** com a dependência concreta. Concluir os demais itens possíveis sem apresentar o requisito pendente como aprovado.

## 7. O que continuará exclusivo da etapa no servidor

Permissões e conta da tarefa real; portas e acesso pela rede corporativa; memória/disco e carga do host; caminhos e dados reais; comportamento após logon/reboot; identificação dos listeners e jobs legados; transferência efetiva de ownership; configuração de build de produção; primeira ativação acompanhada e retorno real ao legado.

A execução local prepara e reduz as incertezas dessa etapa. A decisão de produção depende também dessas evidências do servidor.
