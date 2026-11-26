# Plano para liberar o deploy blue-green em produção

Data: 26/11/2026. Destinatário: Sol ou responsável pela implementação.

**Situação inicial: NÃO LIBERADO para produção.** Este plano complementa o [plano original](PLANO_DEPLOY_BLUE_GREEN.md), o [runbook](deploy/BLUEGREEN.md) e as [evidências existentes](deploy/VALIDACAO_BLUEGREEN.md). Não substitui os registros anteriores nem considera pendências como aprovadas.

**Execução em andamento:** o usuário autorizou usar MAIN e informou pico de 150 usuários. Aplicação local: `C:\Users\c93123a\PPLID`; instalação no servidor: `C:\PPLID`. Isso não dispensa os critérios técnicos abaixo. Nesta máquina não foram encontrados os arquivos padrão de configuração de instalação nem listeners nas portas 8000, 5173 e 5190 durante a inspeção. A autorização já concedida deve ser preservada nas próximas etapas; confirmar somente informações ainda ausentes sobre o servidor e a janela necessária.

## 1. Objetivo e limites

Concluir as correções, produzir releases reproduzíveis e demonstrar troca, rollback, recuperação e continuidade dos robôs no Windows corporativo, com conta sem administrador. Preservar as portas públicas e reduzir a interrupção dos deploys posteriores à instalação do proxy.

A instalação inicial do proxy exige uma janela para liberar os listeners legados. Blue-green no mesmo host não fornece alta disponibilidade contra queda do servidor, banco ou sessão do usuário.

Executar o desenvolvimento e os testes locais sem novas confirmações rotineiras. Este documento não autoriza ativar MAIN, habilitar DEV/HOM, executar robôs de negócio, alterar tasks corporativas ou interromper processos existentes. Essas etapas dependem de autorização explícita para o ambiente e a janela; preparar antes os artefatos e procedimentos necessários à decisão.

Restrições permanentes:

- Preservar mudanças preexistentes dos dois repositórios. Não copiar indiscriminadamente arquivos não versionados, `.env`, bancos ou dados corporativos.
- Usar banco descartável, dados sintéticos e portas de loopback no laboratório. Robôs devem usar serviços simulados ou destinos de teste autorizados.
- Não instalar serviços Windows, mudar firewall nem exigir administrador.
- Não encerrar processos por nome/porta; conferir identidade e propriedade.
- Não publicar, fazer merge ou ativar produção como consequência automática de um teste aprovado.
- Não declarar zero downtime garantido. Medir erros e duração das interrupções, sem retries ocultando falhas.

## 2. Base de trabalho e evidências

Confirmar os caminhos, branches e commits antes de executar; são referências da revisão, não pressupostos sobre o checkout atual:

| Repositório | Worktree de revisão | Branch / commit observado |
|---|---|---|
| Operações | `.local/bluegreen-review-ops` | `feat/bluegreen-deploy-20261126` / `5ced2f1` |
| Aplicação | `.local/bluegreen-review-app` | `feat/bluegreen-web-only-20261126` / `b683f027` |

Existem 65 casos distintos anteriormente aprovados. Não somar reexecuções como novos casos. O ensaio com aplicação real usou fontes com mudanças locais, frontend previamente compilado e duas instâncias da mesma release; não valida um artefato limpo nem compatibilidade entre versões diferentes.

Criar uma pasta de evidências por execução em `.local/bluegreen-readiness/<id>/`, contendo versões, SHAs, configuração sanitizada, comandos, resultados, métricas, logs e resumo. Nunca registrar credenciais, cookies ou snapshots completos de ambiente. Publicar no repositório apenas relatórios sanitizados e testes reproduzíveis.

## 3. Etapa A — Corrigir a falha confirmada de rollback

Arquivo principal: `deploy/bluegreen/manager.py`, métodos `reload` e `promote`.

**Falha confirmada em reprodução isolada:** `reload` pode persistir a candidata como ativa e limpar `intent` antes de concluir a verificação pública. Se essa verificação lançar uma exceção, `promote` ainda está com `switched=False`; como a intenção já foi limpa, o tratamento de erro pode deixar a candidata recebendo tráfego e apenas registrar falha.

Trabalho:

1. Adicionar primeiro uma regressão que reproduza o erro, falhando na implementação atual.
2. Decidir a reversão pelo estado efetivo da transição, conciliando journal, gate e proxy, sem depender exclusivamente da variável local `switched`.
3. Preservar a identidade da release anterior até concluir a operação. Só voltar para ela após verificar sua saúde.
4. Definir estado recuperável quando o próprio rollback falhar. Conservar os processos e registrar erro original e erro da recuperação, sem declarar sucesso.
5. Revisar também `activate`, `rollback`, `reconcile_intent` e a sincronização dos apontamentos legados, para falhas equivalentes após persistência parcial.

Testes obrigatórios:

| ID | Falha induzida | Resultado exigido |
|---|---|---|
| A01 | Candidata falha antes da troca | Ativa original atende; nenhuma publicação da candidata. |
| A02 | Readiness público falha após persistir a troca | Retorno automático à anterior saudável; estado e tráfego concordam. |
| A03 | Apenas frontend ou backend público diverge | Não declarar sucesso; reverter e verificar os dois listeners. |
| A04 | Rollback automático também falha | Erros preservados; estado recuperável; nenhum kill de processo alheio. |
| A05 | Falha ao atualizar `current` depois da troca | Diagnóstico explícito; recovery converge sem derrubar a versão saudável. |
| A06 | Cancelamento durante a observação | Reversão comprovada nas duas portas e sessão preservada. |

Executar A02 primeiro com falha determinística na fronteira da verificação pública e depois com proxy/processos reais. Teste simulado sozinho não fecha a etapa.

**Saída:** correção revisável e regressões aprovadas. Não avançar à liberação com A02 pendente.

## 4. Etapa B — Auditar interrupções e recuperação

Revisar todas as operações que persistem estado antes/depois de criar, retirar ou publicar processos. Os pontos abaixo são hipóteses de risco a investigar, além da falha já confirmada na etapa A.

Injetar interrupção do orquestrador em cada fronteira:

- Antes/depois do build e da validação.
- Após registrar o slot e antes/depois de criar seu processo e salvar o PID.
- Durante aquecimento do gate, após retirada do antigo e antes/depois da observação.
- Durante retirada do slot inativo: conferir referências `previous`/`candidate` quando o slot já não existe.
- Antes/depois de criar o proxy e salvar sua identidade.
- Antes/depois de criar o supervisor de workers e registrar sua identidade.
- Durante atualização dos marcadores legados e da junction.

Testar recovery repetido, estado corrompido, PID reutilizado, listener alheio, candidata morta, ativa morta e proxy morto. Não fabricar identidades para autorizar encerramentos. Uma ambiguidade deve produzir diagnóstico e preservar processos.

Testar concorrência entre watcher, deploy manual, restart do console, rollback, cancelamento, recovery e prune. Conferir o mutex comum Python/PowerShell e ausência de perda de atualizações em arquivos compartilhados por ambientes diferentes.

**Saída:** matriz de pontos de interrupção com resultado esperado/observado; recovery idempotente nos casos suportados; documentação explícita dos casos que exigem intervenção.

## 5. Etapa C — Produzir e testar releases limpas

1. Registrar os SHAs exatos dos dois repositórios e verificar árvore limpa nas worktrees de revisão.
2. Construir duas releases reais, R1 e R2, de commits diferentes, com uma mudança pequena, observável e compatível, incluindo um asset compilado diferente. Não usar apenas metadados diferentes sobre o mesmo código.
3. Construir pelo pipeline usado em operação, com ambiente virtual novo, dependências declaradas e instalação do frontend respeitando o lockfile. Não reutilizar o `dist` do checkout original nem depender de pacotes globais.
4. Confirmar o contrato web-only, os guards e todas as dependências no conteúdo versionado. Se a árvore limpa não construir, corrigir a causa; não importar arquivos locais sem revisão.
5. Preparar esquema somente no banco descartável, com migrações compatíveis com as duas releases. Manter a regra atual de bloquear migrações pendentes no deploy online.
6. Executar preparação, ativação de laboratório, R1 → R2 → R1 e uma terceira publicação que reutilize o slot inativo.
7. Exercitar watcher, ponte PowerShell e console em configuração isolada; comprovar que publicam o SHA correto e reportam falhas corretamente.
8. Verificar que o build usa as URLs/configurações do ambiente de destino. Não promover um `dist` compilado para outro ambiente.

**Saída:** dois artefatos identificados por SHA e hashes, comandos reproduzíveis, dependências registradas, build completo aprovado e troca/rollback reais. Repetir a validação afetada se qualquer artefato mudar.

## 6. Etapa D — Homologar workers independentes

Inventariar cada robô/agendamento, seu starter, frequência, processo atual, estado persistido, dependências externas e comportamento de parada. Incluir scheduler de eventos, BI, robot manager e starters dos apps protegidos por `PPLID_WEB_ONLY`.

Implementar ou concluir um procedimento explícito para iniciar, parar, recuperar e atualizar o supervisor. Hoje a presença de `background.json` impede uma nova inicialização quando o processo registrado morreu; não resolver operacionalmente apagando estado sem verificar propriedade e conclusão dos jobs.

Testes obrigatórios em destinos simulados ou homologação autorizada:

- Dois slots web ativos não iniciam jobs; um supervisor inicia somente os jobs esperados.
- Duas tentativas simultâneas de supervisor não duplicam execução.
- Readiness comprova o estado necessário dos starters; arquivo antigo não pode representar uma execução nova.
- Falha entre criação do processo e persistência do PID é reconciliada sem duplicação.
- Parada durante job longo respeita sua política documentada; timeout não produz um segundo executor.
- Queda, reinício, logon e atualização da release do supervisor preservam execução única e estado.
- Promoção/rollback web não reiniciam workers; prune protege sua release.
- Transferência do legado e retorno ao legado não deixam jobs sem dono nem em duplicidade.

**Saída:** inventário aprovado pelos responsáveis, testes de cada classe de job e procedimento de transferência ensaiado. Manter `workersOwnershipConfirmed=false` até comprovar os requisitos; a flag não é um teste.

## 7. Etapa E — Validar comportamento da aplicação

Usar as releases limpas da etapa C, banco sintético representativo e navegador real. Manter uma aba de R1 aberta durante a promoção para R2.

| Área | Ensaio e critério |
|---|---|
| Autenticação | Login, sessão, CSRF, expiração e logout atravessam troca e rollback. |
| Cliente antigo | Navegação/lazy loading na aba antiga carrega chunks antigos; contratos de API permanecem compatíveis. |
| Escritas | POST/PUT/PATCH/DELETE não são repetidos pelo proxy; conferir quantidade de efeitos no banco. |
| Arquivos | Upload/download nos tamanhos mínimo, habitual e máximo permitido; conferir hash, tamanho e persistência em media. |
| Conexões longas | Streaming/exportação e keep-alive atravessam troca e retirada; nenhum truncamento. |
| Rotas | Validar API direta e via frontend, `/falhas/`, `/media/`, `/static/`, `/admin/` e SPA. |
| Cabeçalhos | Conferir host, esquema, cookies, redirects e origem; cabeçalhos forjados não ganham confiança indevida. |
| Banco | Migração pendente bloqueia a candidata sem modificar esquema nem interromper a ativa. |
| Protocolos | Confirmar uso ou ausência de WebSocket/EventSource; se necessários, homologar suporte antes de liberar. |

Migrações online não fazem parte da liberação inicial. Uma release que exija migrações deve seguir procedimento separado e revisado de compatibilidade, backup e restauração; rollback de código não desfaz dados.

## 8. Etapa F — Medir capacidade e disponibilidade

Obter perfil representativo: usuários simultâneos, volume e mistura de requisições, jobs concorrentes, uploads, exportações e picos. Medir baseline legado e blue-green com a mesma carga e dados.

Proposta mínima de ensaio, a ajustar antes da execução com base no uso real:

1. Baseline estável por 30 minutos após aquecimento.
2. Blue-green sob a mesma carga por 60 minutos, com pelo menos 10 promoções/rollbacks.
3. Pico representativo por 15 minutos, incluindo job longo e exportação.
4. Ensaio de recursos insuficientes isolado: candidata recusada, ativa preservada.

Coletar requisições e erros por endpoint, p50/p95/p99, maior intervalo sem resposta, duração de troca/rollback, CPU, memória disponível/comprometida, uso de cada processo, disco e conexões PostgreSQL. Manter sondas independentes nas duas portas públicas, sem retries, além das métricas da carga.

Critérios de aprovação:

- Zero falhas ou truncamentos atribuíveis às trocas nos ensaios; analisar separadamente falhas deliberadamente injetadas.
- p95 no máximo 20% acima do baseline equivalente, além de respeitar o limite de resposta do negócio.
- Memória suficiente para duas instâncias aquecidas, workers, banco e build, com margem definida a partir dos picos observados. Não aprovar apenas pelo mínimo configurado.
- Sem alternância indevida de saúde por saturação. Ajustar capacidade e sondas a partir das medições, sem mascarar falhas.
- Definir e registrar, antes do piloto, os limites em segundos para rollback, recuperação e janela inicial. Campo sem valor ou sem aprovação impede a liberação.

Definir também retenção e monitoramento do acervo de assets, logs, instâncias e releases. A limpeza de assets precisa preservar clientes antigos durante a janela de suporte estabelecida; não apagar apenas com base no SHA ativo.

## 9. Etapa G — Piloto no ambiente corporativo autorizado

Preparar configuração e comandos concretos antes de solicitar a janela. Não habilitar HOM automaticamente.

Verificações no host de destino:

- Conta padrão consegue executar os binários aprovados, criar junctions e adquirir os mutexes necessários.
- Caddy corresponde à versão/hash aprovados; listeners internos e administração usam loopback.
- Configuração autoritativa, caminhos, portas, permissões, credenciais e dependências estão conferidos sem expor segredos nos logs.
- Portas públicas funcionam a partir de máquinas clientes sem mudanças de firewall.
- Tasks existentes usam caminhos/configuração corretos; recuperação funciona após logon/reboot conforme o modelo operacional permitido. Documentar dependência de sessão/logon e seu tempo real de retorno.
- Console pode reiniciar sem derrubar proxy, slots ou jobs.
- Configuração de autenticação do console foi revisada; credenciais bootstrap padrão não podem permanecer como acesso efetivo no ambiente publicado.

Executar no piloto as releases homologadas, transferência de workers, deploy, rollback, interrupção controlada e recuperação. Ensaiar a liberação dos listeners legados com identificação exata dos processos. Ensaiar retorno completo ao legado e restauração dos gatilhos automáticos.

**Saída:** evidências de clientes da rede, recursos do host, tasks/logon, workers e tempos de recuperação. Se reboot não for autorizado, registrar o bloqueio; não marcar como aprovado por testes locais.

## 10. Etapa H — Preparar e aprovar a entrada em MAIN

Entregar um pacote de liberação contendo:

- SHAs e hashes dos artefatos aprovados, versões de ferramentas e diff dos dois repositórios.
- Configuração final revisada, com segredos fora do pacote de revisão.
- Relatório consolidado: testes executados, falhas corrigidas, limitações e itens não aplicáveis com justificativa.
- Responsável pela execução, responsável por interromper/reverter e contatos operacionais.
- Janela com duração máxima e critérios mensuráveis para abortar.
- Comandos completos de ativação e retorno ao legado, com caminhos reais e identidade dos processos; sem placeholders no procedimento final.
- Backup necessário e evidência de restauração em ambiente isolado quando houver mudança de dados/configuração que o exija.

Sequência de implantação, somente após autorização:

1. Publicar os commits aprovados mantendo o modo legado e validar a release compatível com web-only.
2. Controlar gatilhos automáticos, registrar estado inicial e preservar configurações.
3. Preparar o slot com a mesma versão funcional já em uso; não combinar mudança funcional com a primeira entrada do proxy.
4. Transferir workers conforme o procedimento ensaiado.
5. Liberar somente os listeners web legados e ativar o proxy nas portas preservadas.
6. Validar tráfego de clientes, sessão, escritas, arquivos e robôs; iniciar observação.
7. Restaurar os gatilhos e acompanhar métricas durante a janela acordada.
8. Se exceder os limites ou falhar um requisito crítico, executar o retorno ensaiado. Não improvisar remoção de estado ou encerramento por porta.

## 11. Testes de regressão e rastreabilidade

Executar `tests/bluegreen` integralmente após as correções, incluindo os novos casos A01–A06 e os testes acrescentados nas etapas B–F. Executar a regressão do console nos módulos:

`test_bluegreen_safety`, `test_infra_ports`, `test_console_restart`, `test_server_ops_deploy`, `test_orphan_bot_cleanup`, `test_server_headless`, `test_env_git_worktree` e `test_overview_actions`.

Conferir parser dos scripts PowerShell alterados, compilação Python, sintaxe JavaScript e `git diff --check`. Usar diretórios temporários novos por execução e confirmar cleanup dos processos/bancos de laboratório. Não ampliar testes sem relação com mudanças ou riscos ainda abertos.

Atualizar `deploy/VALIDACAO_BLUEGREEN.md`, preservando o histórico, e reclassificar T01–T36 apenas com evidência correspondente. Para cada resultado registrar:

| Campo | Conteúdo obrigatório |
|---|---|
| Identificação | Caso, etapa, data, executor, SHAs de ops/aplicação. |
| Ambiente | SO, ferramentas, recursos, configuração sanitizada. |
| Execução | Comando, precondições, falha induzida quando houver. |
| Resultado | Esperado, observado, status e localização das evidências. |
| Encerramento | Processos/portas/banco de teste encerrados ou motivo de preservação. |

## 12. Checklist de liberação

- [x] A — Falha confirmada de rollback corrigida; regressão real aprovada. Ver relatório de execução.
- [ ] B — Interrupções, concorrência e recovery validados.
- [ ] C — Builds limpos e duas releases distintas homologados pelo pipeline real.
- [ ] D — Workers: execução única, parada, atualização e recuperação comprovadas.
- [ ] E — Sessões, cliente antigo, API, rotas e arquivos aprovados.
- [ ] F — Capacidade, p95, disponibilidade e limites operacionais aprovados.
- [ ] G — Piloto corporativo, rede, tasks e retorno ao legado aprovados.
- [ ] H — Pacote de liberação revisado e janela de MAIN autorizada.
- [ ] Relatório final e matriz T01–T36 atualizados sem pendências críticas.

Estado detalhado e evidências por etapa: [PRONTIDAO_PRODUCAO_BLUEGREEN.md](deploy/PRONTIDAO_PRODUCAO_BLUEGREEN.md). Builds limpos e testes locais não fecham os requisitos de robôs, dependências, carga real e instalação no servidor.

Atualização posterior: a [etapa 1 de workers e dependências](deploy/ETAPA_1_WORKERS_DEPENDENCIAS.md) implementou admissão/drenagem com confirmação, corrigiu as seis entradas de dependências de produção e preparou a candidata `6ab3f747`. Homologação de negócio, triagem residual, carga representativa e piloto corporativo permanecem pendentes.

**Decisão:** liberar somente com todos os itens obrigatórios aprovados. Item não aplicável exige justificativa explícita; item bloqueado ou não executado não equivale a aprovação. Até lá, manter MAIN no modo legado.
