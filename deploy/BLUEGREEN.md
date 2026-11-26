# Deploy blue-green — operação e limites

## Estado da entrega

Implementação opt-in validada em laboratório local. A configuração versionada de produção continua sem `deployStrategy`; portanto usa `legacy`. MAIN não foi ativado, DEV/HOM não foram habilitados e nenhuma task corporativa foi alterada.

Consulte [VALIDACAO_BLUEGREEN.md](VALIDACAO_BLUEGREEN.md) para os resultados efetivamente observados e as verificações ainda pendentes. O plano original permanece em [PLANO_DEPLOY_BLUE_GREEN.md](../PLANO_DEPLOY_BLUE_GREEN.md).

## Arquitetura implementada

```text
Portas públicas atuais
    Caddy 2.11.9, processo independente do console
        pools fixos A/B, sem reload durante deploys comuns
            slot A: Waitress backend + Waitress/WhiteNoise frontend
            slot B: Waitress backend + Waitress/WhiteNoise frontend
                mesma base PostgreSQL do ambiente
                media persistente

Supervisor de tarefas separado, fixado em uma release própria
```

Cada processo web executa do caminho físico da release. O frontend compilado é servido por WhiteNoise, dependência já existente da aplicação; não é necessário iniciar Vite/Node no runtime. As rotas `/api`, `/media`, `/static`, `/falhas` e `/admin` na porta do frontend seguem para o backend. Os acessos diretos à porta pública do backend continuam disponíveis.

O proxy mantém os dois destinos cadastrados. Cada slot responde a `/__pplid/traffic` com base em um arquivo de controle atômico. Antes de retirar o destino antigo, o orquestrador permite ambos por 1,1 segundo para aquecer as sondas do Caddy. Em seguida, permite somente o destino escolhido e verifica a convergência pelas duas portas públicas. Nenhuma requisição de cliente é repetida para esconder falhas.

Frontend e backend convergem por sondas independentes. Existe uma curta janela em que um cliente pode receber frontend de uma versão e API de outra; a compatibilidade entre versões é obrigatória. A troca não é uma transação distribuída instantânea.

A prova inicial com reloads completos do Caddy interrompeu duas conexões. Essa estratégia foi descartada. A troca por pools fixos passou no laboratório sem os erros observados naquela estratégia.

## Configuração

Use [env.config.bluegreen.example.json](../config/env.config.bluegreen.example.json) como referência, sem substituir cegamente a configuração existente. O exemplo também mantém `legacy` como padrão.

Por ambiente, configurar explicitamente:

- `deployStrategy: blueGreen` somente na migração autorizada.
- Binário Caddy e SHA-256 do executável.
- Python com `psutil`, usado pelo orquestrador; o backend usa o Python da própria release.
- Portas internas A/B distintas e livres, além da porta administrativa local do proxy.
- Memória e disco mínimos, dimensionados a partir do consumo medido.
- Timeouts de inicialização, observação, drenagem, encerramento e preparação.

O Caddy fixado é o executável oficial Windows amd64 2.11.9. A origem e os hashes estão em [caddy-version.json](bluegreen/caddy-version.json). O instalador confere o SHA-512 publicado para o ZIP e registra o SHA-256 do executável extraído. Não instala serviço Windows nem altera firewall.

```powershell
python deploy/bluegreen/install_caddy.py C:\PPLID\tools\caddy-2.11.9
```

O binário deve ser provisionado conforme as políticas reais da máquina. A execução local no laboratório não comprova autorização corporativa em outro servidor.

## Contrato com a aplicação

O repositório PPLID precisa incluir `scripts/deploy/bluegreen-contract.json` e os guards `PPLID_WEB_ONLY` nos pontos que iniciam tarefas. A ferramenta [application_patch.py](bluegreen/application_patch.py) instala esses guards usando âncoras exatas e recusa arquivos divergentes.

No checkout local examinado, foram protegidos:

- Scheduler BI em `common`.
- Autostart dos robôs em `automacoes`.
- Agendamentos de falhas, férias, suporte e escala.
- Purga periódica de métricas, preservando o flush de métricas por processo web.
- Scripts legados de start/stop/deploy, que passam a recusar ações em ambientes com blue-green configurado ou estado registrado.

Mudanças nos guards não iniciam ou encerram processos já em execução. O modo legado conserva o comportamento anterior quando `PPLID_WEB_ONLY` não está ativo e não existe configuração/estado blue-green.

Cada slot recebe um snapshot de ambiente fora da release, com os overrides web-only. Isso impede que `bootstrap_environ`, ao recarregar o `.env`, reative tarefas acidentalmente. Não expor esse snapshot por HTTP nem incluí-lo em downloads de logs.

## Estado, locks e recuperação

O estado autoritativo fica em `deploy/<ENV>/bluegreen/state.json`. Inclui slots, identidade dos processos, release, portas, fase e intenção de troca. `traffic.json` controla quais instâncias participam do pool. `events.jsonl` registra eventos.

Python e PowerShell usam o mesmo mutex `Global\PPLID-Deploy-<ENV>`. O processo Python mantém a posse durante build, validação e promoção. Prune/cleanup também respeitam esse mutex.

Uma intenção é persistida antes de mudar o tráfego. A recuperação reconcilia o arquivo de controle, o processo do proxy e as instâncias registradas. Estado inválido bloqueia operações em vez de assumir defaults vazios. PIDs são conferidos com horário de criação, executável e argumentos.

`current` é apenas um apontamento de compatibilidade. É reconciliado após a troca; processos continuam usando o diretório físico.

O slot anterior permanece aquecido até o próximo deploy precisar reutilizá-lo. Isso mantém rollback rápido, mas significa que o consumo normal pode incluir **duas versões completas**. Antes de reutilizar o slot, o orquestrador aguarda drenagem e solicita parada graciosa. Um timeout preserva o processo e requer diagnóstico; não há kill genérico por porta.

## Banco e static

Esta entrega adota uma política conservadora: **migração pendente bloqueia a candidata**. O worker consulta o plano pelo Django e não executa migrações durante start, rollback ou recuperação. A preparação de static ocorre antes de construir o WSGI da candidata e somente fora de uma release registrada em outro slot.

Migrações compatíveis devem ser tratadas separadamente, sob procedimento revisado de expansão/contração e com limites de locks. A automatização de migrações online, classificação semântica e testes de bloqueio SQL não está homologada por esta entrega. `migrate --plan` não prova segurança. Rollback não desfaz esquema nem dados.

Os assets do frontend são copiados para um acervo compartilhado que só aceita o mesmo conteúdo para o mesmo nome. Colisões bloqueiam o deploy. Abas antigas continuam encontrando os chunks antigos. A limpeza automática desse acervo não foi implementada; acompanhar espaço em disco e definir sua retenção antes de operação prolongada.

## Comandos operacionais

Sempre usar um `machine.config.json` explícito quando não estiver na instalação padrão. Os exemplos abaixo são procedimentos, não foram executados contra MAIN.

```powershell
# Estado (somente leitura)
.\deploy\bluegreen.ps1 -Action status -Environment DEV -MachineConfig C:\PPLID\machine.config.json

# Preparação inicial: sobe A e administração local do proxy, sem ocupar portas públicas
.\deploy\bluegreen.ps1 -Action prepare -Environment DEV -Sha <pasta-sha-da-release> -MachineConfig C:\PPLID\machine.config.json

# Primeira ativação: exige portas públicas livres e candidata saudável
.\deploy\bluegreen.ps1 -Action activate -Environment DEV -MachineConfig C:\PPLID\machine.config.json

# Release já construída
.\deploy\bluegreen.ps1 -Action promote -Environment DEV -Sha <pasta-sha-da-release> -MachineConfig C:\PPLID\machine.config.json

# Pipeline com build e validação: também usado pelo watcher e console
.\deploy\run_pipeline_locked.ps1 -Environment DEV -TargetSha <sha-curto> -TargetShaFull <sha-completo>

# Rollback para o slot anterior aquecido
.\deploy\bluegreen.ps1 -Action rollback -Environment DEV -MachineConfig C:\PPLID\machine.config.json

# Cancelamento cooperativo, sem taskkill da árvore do pipeline
.\deploy\bluegreen.ps1 -Action cancel -Environment DEV -MachineConfig C:\PPLID\machine.config.json

# Recuperar uma operação interrompida; não aplica migrations
.\deploy\bluegreen.ps1 -Action recover -Environment DEV -MachineConfig C:\PPLID\machine.config.json

# Parada explícita da camada web; preserva os workers independentes
.\deploy\bluegreen.ps1 -Action shutdown -Environment DEV -MachineConfig C:\PPLID\machine.config.json
```

Cancelamento é cooperativo. Builds em andamento precisam alcançar um ponto de verificação; não se promete cancelamento instantâneo de pip/npm. Depois de uma operação cancelada, recovery limpa o marcador após reconciliar o ambiente.

Reiniciar pelo console substitui o slot web de forma coerente. Alterar portas, habilitar/desabilitar e `stop_all` legados ficam bloqueados em blue-green; usar o procedimento de manutenção. O painel informa a fase e as versões ativa/candidata sem tratar apenas a porta do proxy como prova de saúde do backend.

## Migração dos workers

Antes de ativar a camada web, inventariar e transferir os robôs/agendamentos do processo legado para supervisão independente, sem duas execuções simultâneas. Verificar sessões de navegador, estado do robot manager e tarefas externas.

Há um entrypoint independente para os starters existentes. Seu start é **explícito**, separado do deploy, exige `workersOwnershipConfirmed: true` e um preflight de esquema. O mutex de background é adquirido antes do `django.setup()` e dos efeitos de inicialização. O processo é fixado na release escolhida e sua release fica protegida contra retenção.

A release deve declarar `scripts/deploy/bluegreen-workers-contract.json` e conter a cópia compatível do protocolo de admissão. `workers-stop` fecha novos despachos e espera ciclos, subprocessos e descendentes terminarem; não chama o encerramento forçado do robot manager. Timeout mantém a geração em `draining` e bloqueia substituição. A confirmação exige recibo de parada, identidade da instância e nenhuma unidade pendente; um PID morto sozinho não basta. Consultar [contrato, testes e condições de recuperação](ETAPA_1_WORKERS_DEPENDENCIAS.md). A retirada de um slot web fecha apenas o grupo dessa instância, preservando o supervisor independente.

Preparar duas releases com o protocolo compatível antes de transferir os workers. O start confere todos os slots registrados; depois de registrar o supervisor, releases web sem o novo contrato são recusadas também no rollback. Um supervisor legado não é convertido automaticamente.

```powershell
.\deploy\bluegreen.ps1 -Action workers-start -Environment DEV -Sha <release-homologada> -MachineConfig C:\PPLID\machine.config.json
.\deploy\bluegreen.ps1 -Action workers-stop -Environment DEV -MachineConfig C:\PPLID\machine.config.json
```

Não marcar a confirmação de ownership como forma de contornar uma verificação pendente. A execução dos starters de negócio, sua parada e a atualização do supervisor precisam de homologação específica com os responsáveis pelas automações. Os testes genéricos de slots não comprovam a execução correta dos robôs corporativos. O deploy web não os reinicia automaticamente.

## Primeira ativação autorizada

O inventário e a limitação concreta de parada dos robôs estão em [INVENTARIO_WORKERS_BLUEGREEN.md](INVENTARIO_WORKERS_BLUEGREEN.md). O supervisor usa um diretório exclusivo por instância e readiness vinculado a SHA, instance ID e processo proprietário. Um novo start após parada confirmada cria outra instância; saída inesperada bloqueia substituição automática para evitar jobs duplicados. O readiness comprova conclusão dos hooks de Django, não saúde individual dos robôs.

1. Homologar a aplicação, banco, sessões, rotas, carga e workers em ambiente isolado.
2. Publicar as mudanças compatíveis dos dois repositórios conforme o fluxo normal da equipe; ainda em modo legado.
3. Provisionar Caddy e configuração local, confirmar permissões e portas.
4. Confirmar recursos para duas instâncias e revisar limites das sondas sob carga.
5. Na janela autorizada, controlar os gatilhos de sync/bootstrap para evitar concorrência.
6. Preparar a **mesma versão já publicada**, com o contrato web-only, em A. Não introduzir uma mudança funcional junto com a migração da entrada HTTP.
7. Transferir os workers para supervisão independente conforme ensaio prévio.
8. Liberar somente os listeners web legados, identificando seus processos. Não usar scripts que encerram indiscriminadamente árvores ou portas.
9. Ativar o proxy nas portas públicas preservadas.
10. Validar pelas máquinas clientes e confirmar sessões, escrita, uploads, `/falhas/` e robôs.
11. Restaurar os gatilhos. Só então ensaiar o primeiro deploy por slots.

Se a etapa de liberação dos listeners legados ainda não tiver um procedimento comprovado para a instalação real, a ativação não está pronta.

## Retorno ao legado

É manutenção com potencial parada, diferente do rollback aquecido.

1. Controlar os gatilhos automáticos na janela autorizada.
2. Parar e conferir os workers independentes antes de reativar starters legados.
3. Executar `shutdown` da camada web e conferir que os processos registrados terminaram e as portas públicas foram liberadas.
4. Preservar os arquivos de estado e logs em arquivo de auditoria. Arquivar `bluegreen/state.json` somente depois de comprovar a parada; não editar PIDs ou apagar estado para forçar operações.
5. Voltar `deployStrategy` para `legacy` na configuração autoritativa.
6. Iniciar a release compatível pelo procedimento legado e validar banco/sessões.
7. Restaurar os gatilhos.

## Reprodução dos testes

A revisão de prontidão e os bloqueios atuais estão em [PRONTIDAO_PRODUCAO_BLUEGREEN.md](PRONTIDAO_PRODUCAO_BLUEGREEN.md). Há dois harnesses adicionais, executados no laboratório: `python -m deploy.bluegreen.readiness_build --help` para build real de commit limpo com PostgreSQL descartável, e `python -m deploy.bluegreen.readiness_smoke --help` para promoção/rollback entre duas releases construídas, incluindo navegador opcional. Eles aceitam somente seus diretórios de laboratório, não a instalação em `C:\PPLID`. A retenção dos diretórios preserva evidências e consome disco; não confundir o esquema sintético preparado pelo smoke com migração online de produção.

```powershell
python deploy/bluegreen/install_caddy.py .local/bluegreen-tools/caddy-2.11.9
python -m pytest tests/bluegreen -v --basetemp=.local/bluegreen-test-novo -p no:cacheprovider
$env:PYTHONPATH = Join-Path (Get-Location) 'ops-console'
python -m pytest ops-console/tests/test_bluegreen_safety.py ops-console/tests/test_infra_ports.py ops-console/tests/test_console_restart.py ops-console/tests/test_server_ops_deploy.py ops-console/tests/test_orphan_bot_cleanup.py ops-console/tests/test_server_headless.py ops-console/tests/test_env_git_worktree.py ops-console/tests/test_overview_actions.py -q --basetemp=.local/bluegreen-console-novo -p no:cacheprovider
```

Para o código real em cópia descartável e PostgreSQL local:

```powershell
python -m deploy.bluegreen.full_app_lab --application C:\caminho\PPLID --pg-bin "C:\Program Files\PostgreSQL\14\bin" --caddy .local\bluegreen-tools\caddy-2.11.9\caddy.exe
```

Adicionar `--browser` executa o teste com Edge headless, bloqueando requisições fora das portas locais do laboratório. Isso verifica montagem do Vue e sessão/CSRF na mesma página durante troca e rollback.

A ferramenta copia fontes versionadas e uma lista explícita de quatro módulos Python locais necessários ao checkout examinado. Não copia `.env`, dados de usuário nem bancos. Usa portas aleatórias e encerra o PostgreSQL em `finally`. A cópia conserva mudanças locais dos fontes copiados; não equivale à homologação de um commit limpo de produção.

Coletor independente de disponibilidade, sem retries:

```powershell
python -m deploy.bluegreen.probe http://127.0.0.1:<porta-lab>/__pplid/ready --seconds 120 --hz 5 --output .local/bluegreen-probe.jsonl
```

Zero erros observados na amostra não comprova inexistência de interrupções menores que o intervalo de sondagem. Rede corporativa, carga representativa, logoff/reboot reais e workloads de negócio continuam exigindo os ensaios listados no relatório.

## Referências oficiais consultadas

- [Caddy API: carregamento e validação de configuração](https://caddyserver.com/docs/api).
- [Caddy reverse_proxy: pools, health checks, retries e streams](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).
- [Caddy: grace period e shutdown delay](https://caddyserver.com/docs/caddyfile/options).
- [Release oficial fixada](https://github.com/caddyserver/caddy/releases/tag/v2.11.9).
