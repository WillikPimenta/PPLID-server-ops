# Prompt para o agente no servidor — implantação blue-green

Copie o bloco abaixo para o agente que está executando **no servidor de produção**. Ele deve ter acesso aos dois repositórios e executar com a conta operacional habitual, sem administrador. Esta publicação disponibiliza código e instruções; a implantação ocorre quando este prompt for executado no servidor.

---

Você está no servidor do PPLID. Execute a implantação controlada de MAIN descrita abaixo, acompanhe o resultado e faça rollback se necessário. Não pare em um plano: descubra a instalação, prepare os artefatos e o retorno, execute quando os pré-requisitos técnicos estiverem satisfeitos e valide o serviço. Não solicite novamente autorizações rotineiras já dadas neste prompt. Se faltar informação indispensável que não possa descobrir na máquina, avance no que for independente e pergunte somente o necessário.

## Objetivo e limites autorizados

- A instalação de produção é `C:\PPLID`. Descubra os caminhos reais de operações, fontes, configuração, releases e Python; não confunda a raiz da instalação com a raiz de um repositório Git.
- Ambiente autorizado: MAIN. São **150 sessões ativas**, com **até 16 requisições de negócio simultâneas**, e fila para o excedente. Isso não significa 150 requisições simultâneas nem limite de 16 conexões TCP ociosas.
- Prioridade: implantação e recuperação rápidas. O usuário aceita perdas ocasionais de requisições HTTP durante a troca. Essa tolerância não autoriza perda de dados, repetição de mutações, duplicação de robôs ou encerramento forçado de jobs.
- Trabalhe com a conta comum existente. Preserve configurações corporativas, portas públicas, integrações, credenciais, permissões, sessões e arquivos persistentes. Não instale serviços, altere firewall/políticas ou exija administrador.
- Estão autorizados o download das branches abaixo, os ajustes locais necessários à instalação, build, preparação, migração compatível revisada, troca controlada, monitoramento e retorno. Não faça merge em `main`/`master`, force push ou atualizações funcionais adicionais.
- Se faltar permissão técnica, backup utilizável, compatibilidade de esquema ou procedimento de retorno comprovado, mantenha o serviço atual e explique o impedimento. Não contorne essas condições reduzindo guards.

## Repositórios e revisões

| Repositório | URL | Branch |
|---|---|---|
| Aplicação | `https://github.com/WillikPimenta/PPLID.git` | `release/blue-green-pilot-20261127` |
| Operações | `https://github.com/WillikPimenta/PPLID-server-ops.git` | `release/blue-green-pilot-20261127` |

Revisões de referência:

- **Candidata da aplicação:** `fac41fdd35bb07c0967c8599c4a38b8c419516e2`. A branch publicada deve resolver exatamente para esse commit; se divergir, investigue antes de executar código diferente.
- **Base revisada de operações:** `4934da903ae578367dba3999091dd7742f43fa8a`.
- **Correção de saúde e fila incluída nessa base:** `264ac4043a419e4c33230a212f9f04327d79678e`.
- O commit posterior de publicação em operações acrescenta este prompt. Confira que o código executável obtido continua igual à base revisada; registre o SHA completo do HEAD recebido. Não use uma atualização posterior da branch sem revisar o delta.
- **Controle R1 usado somente no laboratório:** `1cb6444748473ced40eea6b51daf5f4162424890`. Não suponha que seja a versão atualmente instalada nem o retorno correto do servidor.

A candidata deriva de uma base de integração que também contém alterações funcionais anteriores ao trabalho de deploy. Compare com o SHA realmente instalado, inclusive todas as migrations pendentes. Não descreva o delta de produção como sendo apenas uma troca de infraestrutura.

## 1. Descobrir e preservar antes de alterar

1. Identifique conta/SID, configuração autoritativa (`machine.config.json` e `envConfigPath`), repositório de operações usado por tarefas/console/watchers, release ativa e seu SHA completo. Confirme a identidade por metadata, apontamentos e resposta pública; não use apenas o HEAD de um checkout auxiliar.
2. Leia o estado dos dois repositórios e preserve alterações existentes. Não use `reset --hard`, `clean`, `checkout --force`, `pull` com merge implícito ou troca de branch sobre arquivos em uso. Crie checkout/worktree separado se houver alterações locais ou scripts ativos. Preserve patches e arquivos não rastreados relevantes em diretório privado, sem publicá-los.
3. Faça inventário de listeners, processos com PID/criação/executável/comando, tarefas e seus gatilhos, watchers, proxy, slots, supervisor, robôs e jobs em andamento. Registre se MAIN está em legado ou blue-green e se existe operação pendente. Porta ou nome de processo servem para descoberta, não como autorização para matar processos.
4. Crie uma pasta de evidências persistente e privada. Salve configuração e apontamentos necessários ao retorno, estado original das tarefas e comandos reais de start/stop. Não imprima nem envie `.env`, strings de conexão, cookies, tokens, dados pessoais ou segredos para Git/chat.

## 2. Baixar as branches sem afetar o serviço atual

Use os clones identificados ou novos clones isolados. Faça `fetch` das branches nos dois repositórios e verifique o retorno de cada comando nativo com `$LASTEXITCODE`. O padrão abaixo usa variáveis que você deve preencher com os caminhos reais; não execute placeholders literalmente:

```powershell
$PilotBranch = 'release/blue-green-pilot-20261127'
$CandidateFull = 'fac41fdd35bb07c0967c8599c4a38b8c419516e2'
$OpsValidated = '4934da903ae578367dba3999091dd7742f43fa8a'

git -C $OpsClone fetch origin "refs/heads/${PilotBranch}:refs/remotes/origin/${PilotBranch}"
if ($LASTEXITCODE -ne 0) { throw 'Fetch de operacoes falhou' }
git -C $AppClone fetch origin "refs/heads/${PilotBranch}:refs/remotes/origin/${PilotBranch}"
if ($LASTEXITCODE -ne 0) { throw 'Fetch da aplicacao falhou' }
```

Valide URLs dos remotos, SHA da aplicação e ancestralidade da base de operações. Compare o conteúdo de operações recebido contra `$OpsValidated`, permitindo apenas a inclusão deste Markdown na publicação. Use checkout isolado e fixado no commit conferido. Não atualize um mirror pelo remoto errado e não considere que o fetch de um clone atualizou automaticamente os demais.

Leia, na revisão baixada, `RUNBOOK_PILOTO_SERVIDOR_BLUE_GREEN.md`, `RELATORIO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md`, `deploy/BLUE_GREEN.md` se existir, `deploy/SUPERVISOR_CONTROL.md`, o entrypoint e os exemplos de configuração efetivamente presentes. Use o código recebido para conferir nomes e parâmetros; não invente comandos.

## 3. Preflight e retorno preparados com o serviço atual atendendo

- Confirme Python e dependências de operações, Node compatível com `^22.12.0 || >=24.0.0`, `npm.cmd`, PostgreSQL e permissão de execução. Não contorne restrições corporativas de execução.
- Valide Caddy **2.11.9**, SHA-256 `2622e239c79df700b0eb92cbd950d1edee365e155a7859f358f0e55f002df591`. Obtenha pelo canal permitido e confira o hash antes de usar.
- Meça disco, RAM e margem de memória comprometida. Reserve espaço para releases físicas, venvs, build/cache, assets antigos, logs e backup. Não copie venvs, `node_modules`, `dist`, banco ou arquivos DPAPI do laboratório. Não apague releases/arquivos corporativos para liberar espaço.
- Confira portas A/B/admin sem colisões entre ambientes, inclusive os desabilitados. Internas e administração ficam em loopback. Preserve URLs públicas, hosts/CSRF, segredo de sessão, conexão de banco, media e configuração de build reais.
- Mantenha **16 threads de negócio**, **duas threads reservadas de saúde no backend**, frontend com quatro threads e capacidade de 256 canais. Preserve a classificação por requisição do dispatcher; não remova a correção para simplificar a instalação.
- Confira conta operacional, permissões sobre junctions/Job Objects, diretórios, tarefas e políticas. DPAPI é vinculado à conta/máquina; o proxy, os slots e o supervisor precisam do contexto operacional correto.
- Confira a forma efetiva de selecionar o checkout novo de operações para CLI, console, tarefas e watchers. O download em pasta paralela não atualiza sozinho os entrypoints instalados. Planeje a troca desses apontamentos e sua reversão; impeça atuação concorrente das revisões antiga e nova sem parar genericamente toda a instalação.
- Confirme backup consistente do banco/configuração e evidência de restauração utilizável. Não restaure banco de produção automaticamente por causa de erro HTTP.
- **Antes da primeira interrupção**, produza um registro de retorno com comandos concretos e caminhos reais. Se o destino de retorno for legado, prepare também um script/procedimento `Retornar-Legado.ps1`, com identidades e pré-condições, baseado nos comandos existentes na instalação. Não basta escrever “reiniciar a versão antiga”.

Se precisar ajustar código operacional, faça mudança pequena em branch local, registre o diff e rode apenas a regressão afetada em área isolada. Não transforme produção em laboratório nem remova proteção de ownership, migração, recibo ou recursos para conseguir avançar.

## 4. Build real e revisão do banco

1. Confira e atualize o mirror correto de MAIN (`<baseDir>\deploy\MAIN\mirror`) pelo remoto da aplicação, preservando seu estado. Confirme que a candidata existe ali como commit completo. Use um identificador de pasta de release inequívoco que corresponda ao SHA do manifesto.
2. Construa a candidata com configuração real de produção, mantendo a atual atendendo. Use o comando público **build**; `prepare` não faz build. Não escreva recibos ou `meta.built` manualmente. Confira fonte limpa, identidade do artefato, build, `pip check`, constraints, contratos web-only/drenagem e frontend com URL real de API.
3. Se o legado ainda exigir estratégia `legacy`, prepare uma cópia explícita da configuração para construção em paralelo ou outro procedimento suportado pelo código. Não mude a configuração usada por tarefas ativas apenas para satisfazer o parser. Garanta que os subprocessos recebam a configuração selecionada via `OPS_MACHINE_CONFIG`.
4. Consulte o plano completo com `migrate_preserving_rbac --plan` na release física e no banco MAIN confirmado. Revise **todas** as migrations pendentes, duração/locks e compatibilidade com a candidata e a versão real de retorno. Conflitos ou mudanças incompatíveis impedem a troca online.
5. A migration `access.0046_split_planning_performance_menu_key` preserva PK/permissões e separa a identidade de Planejamento. Confira conflitos antes de aplicar e RBAC depois. Não apague configuração, amplie grants ou desative a preservação para contornar falha.
6. Aplique somente o plano compatível revisado, explicitamente, com limites de espera apropriados ao servidor e ambiente correto. Confira ausência de pendências. Startup/promoção não aplicam migrations. **Rollback de código não desfaz esquema nem dados**; se a versão anterior não funcionar com o esquema resultante, ainda não há retorno seguro e a mudança deve aguardar solução.

O entrypoint disponível é `deploy\bluegreen.ps1`. Use-o como processo filho, por exemplo `powershell.exe -NoProfile -File ...`, e confira o exit code: o script contém `exit`. Preencha `$PilotOps`, `$PilotMachine`, `$CandidateRelease` e `$PilotRunId` com valores reais. Antes de mutações, confirme no código recebido se cada ação é válida no estado encontrado.

```powershell
$env:OPS_MACHINE_CONFIG = $PilotMachine
$BgEntry = Join-Path $PilotOps 'deploy\bluegreen.ps1'
powershell.exe -NoProfile -File $BgEntry -Action build -Environment MAIN -Sha $CandidateRelease -RunId $PilotRunId -MachineConfig $PilotMachine
if ($LASTEXITCODE -ne 0) { throw 'Build recusado; manter versao atual' }
```

## 5. Implantar conforme o estado encontrado

### MAIN já usa blue-green

Confirme estado consistente, versão ativa saudável, slot anterior compatível e supervisor identificado. Preserve a release física anterior, seus assets e recibos. Registre o destino exato de rollback antes de promover. Com artefato já validado, use `promote -Sha`; se ainda houver build a fazer, `pipeline -Sha` mantém o mutex durante build/validação/promoção. Não execute os dois para a mesma tentativa nem repita uma mutação de resultado incerto.

Mantenha o supervisor independente na geração atual quando seus contratos forem compatíveis. Não reinicie robôs como efeito de deploy web. Se o supervisor exigir atualização, trate sua drenagem e substituição como etapa separada, com recibo e comprovação de término dos descendentes.

### MAIN ainda usa legado: primeira ativação

Prepare tudo antes de liberar as portas públicas. Uma versão legada sem contrato web-only não pode ser passada a `prepare` como se fosse um slot compatível. O controle R1 do laboratório não substitui a versão instalada.

Se a candidata for a primeira versão compatível, prepare-a como primeiro slot **somente depois de validar o retorno ao legado real**. Nesse caso não existe rollback aquecido para o legado: a primeira transferência pode ter uma breve interrupção e seu fallback é o procedimento concreto preparado na etapa 3.

Coordene exclusivamente os gatilhos e processos inventariados. Drene os donos antigos de jobs e comprove a transferência antes de marcar `workersOwnershipConfirmed: true` ou iniciar novo supervisor. Não reinicie schedulers enquanto a geração anterior puder estar trabalhando.

Use `prepare` para o slot interno, confirme readiness e controles, prepare/inicie o supervisor apropriado após comprovar exclusividade e só então libere os listeners web antigos pelo mecanismo real conhecido. Preserve os jobs independentes. Faça `activate` imediatamente após a liberação das portas e valide por acesso público. Não use `stop_all`, kill por porta, `taskkill /IM` ou término da árvore que contenha trabalhos em execução.

Não provoque uma falha em produção apenas para demonstrar rollback. Não promova uma segunda versão arbitrária só para criar um slot anterior. Comprove prontidão do retorno e execute-o se houver falha real.

## 6. Acompanhar ativamente e decidir sobre recuperação

Antes da troca, registre um baseline curto com o tráfego existente. Inicie um coletor independente que continue durante a mudança, registrando horário, status, latência e identidade/versão. Preserve todas as amostras, inclusive falhas; não use retries para escondê-las.

Depois da troca, acompanhe ativamente por **cinco minutos**. Esse é um período de observação do piloto, não um ensaio de capacidade nem garantia de operação futura. Não encerre o turno dizendo que ficará acompanhando sem um processo real e evidência verificável.

Verifique:

- Readiness e identidade nos destinos corretos, portas públicas e acesso pela URL corporativa. Faça sondas leves aproximadamente uma vez por segundo, com timeout curto, sem saturar a fila.
- Sessão existente, login/CSRF/RBAC, navegação e assets de abas antigas; controles do painel apontando à geração correta do supervisor. Use contas/sessões autorizadas disponíveis sem expor credenciais.
- Uma consulta habitual e evidências de upload/download/integrações que possam ser verificadas sem produzir mutações de negócio artificiais. Não dispare importações, robôs ou trabalhos reais só para testar.
- Jobs/filas e efeitos já em andamento, ausência de donos duplicados, logs de slots/proxy/supervisor, CPU, memória física/commit, disco e conexões PostgreSQL.
- Espera em fila, latência HTTP e confirmação da troca em campos separados. `trafficSwitchMs` não é medição direta de tempo offline; o aquecimento da candidata pode durar mais enquanto a versão anterior atende.

Conte erros ocasionais de HTTP, mas **não faça rollback por um timeout isolado de relatório ou por qualquer perda transitória já aceita pelo usuário**. Para este piloto, trate três falhas consecutivas de readiness/identidade pública da candidata, sem recuperação nas sondas, como motivo de retorno ao destino comprovado. Também retorne diante de quebra de autenticação/permissões, erro funcional novo impeditivo, indisponibilidade persistente ou regressão clara de recursos atribuível à candidata. Esses são gatilhos operacionais de recuperação, não um SLA de negócio.

Se houver possível duplicação/perda de dados ou jobs, suspenda novas admissões pelo mecanismo existente, preserve evidências e reconcilie os efeitos. Não presuma que trocar código, reiniciar ou repetir a operação corrige efeitos já persistidos.

## 7. Rollback e resultados incertos

**Antes de executar rollback, consulte o estado.** O orquestrador pode já ter retornado automaticamente à origem após falha. Confira SHA/saúde do destino público. Se a versão anterior já está ativa e saudável, confirme a recuperação sem executar outro rollback que possa reativar a candidata ruim.

Se a tentativa ainda estiver em curso, solicite `cancel` cooperativo com o RunId correto e acompanhe a finalização; não mate a árvore de build ou dispute o mutex. Em falha de comunicação, journal pendente ou reboot, consulte logs e use `recover` quando indicado pelo estado, antes de decidir a próxima mutação. Não apague journals, leases, PIDs ou recibos para destravar.

### Com slot anterior compatível confirmado

```powershell
powershell.exe -NoProfile -File $BgEntry -Action status -Environment MAIN -MachineConfig $PilotMachine
# Conferir manualmente pelo estado lido: candidata ativa e previous = retorno esperado.
powershell.exe -NoProfile -File $BgEntry -Action rollback -Environment MAIN -MachineConfig $PilotMachine
if ($LASTEXITCODE -ne 0) { throw 'Rollback nao confirmado; reconciliar estado, sem repetir cegamente' }
powershell.exe -NoProfile -File $BgEntry -Action status -Environment MAIN -MachineConfig $PilotMachine
```

Valide SHA público, sessão, permissões, dados/jobs e geração do supervisor após o retorno. Acompanhe mais cinco minutos. Não reverta migrations, restaure banco ou troque o supervisor como efeito implícito desse rollback. Não faça nova promoção automática da candidata após a recuperação.

### Primeira ativação sem slot anterior: retorno ao legado

Execute o procedimento concreto preparado **antes** da troca: impeça gatilhos concorrentes, feche admissões e drene qualquer geração nova do supervisor, confira recibo/descendentes e só então reative starters legados. Encerre a camada web blue-green via `shutdown`, que por si só não para o supervisor independente. Confirme liberação das portas e restaure apontamentos, configuração e tarefas reais preservados, iniciando os componentes antigos uma única vez.

Os guards legados verificam o estado blue-green. Se a instalação exigir arquivá-lo para retornar ao legado, faça isso somente após comprovar parada de todos os componentes pertinentes, pelo procedimento revisado; preserve cópia íntegra. Não renomeie o runtime enquanto houver processos/jobs ativos.

Confirme compatibilidade do esquema mantido, versão realmente recuperada, URL corporativa, sessões, permissões e jobs. Se não puder comprovar recuperação segura, mantenha as evidências e informe exatamente o bloqueio; não anuncie sucesso nem use encerramento forçado como atalho.

## 8. Encerramento e evidências

Salve um relatório e um ponto de retomada no servidor com: caminhos reais, conta, SHAs anterior/candidato/operacional, diffs locais, configurações alteradas sem segredos, backup, migrations, tarefas/ownership, comandos e exit codes, RunId, métricas/probes, erros e situação final de cada componente.

Informe claramente um dos resultados: implantado e observado; rollback executado e validado; ou bloqueado antes da troca com versão atual preservada. Registre o tempo efetivamente acompanhado e se o acesso foi validado pela rede corporativa ou apenas pela própria máquina. Deixe documentado o comando de rollback aplicável ao estado final e não remova a versão de retorno durante este piloto.

Reaproveite as suítes locais já concluídas: **1.375 testes frontend, 421 backend, builds e regressões de segurança/drenagem**. Não as rode contra produção. Os helpers `readiness_build`, `readiness_smoke` e `local_capacity` são exclusivos de laboratório descartável e não devem ser apontados para `C:\PPLID`.

Resultado local conhecido: promoção/rollback confirmados em 420,48/613,59 ms; cenário pesado sintético com 98 HTTP 200, 20 timeouts e zero 503 em 118 requisições, com 80 dos 150 usuários enviando requisições antes do aborto estrito. A mistura não representa tráfego corporativo medido. Preserve essa limitação no relatório, sem transformá-la em homologação integral nem em bloqueio por um SLA numérico que o usuário não exigiu.

---

Fim do prompt.
