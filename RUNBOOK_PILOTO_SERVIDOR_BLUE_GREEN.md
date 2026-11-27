# Piloto blue-green no servidor

## Estado e condições de entrada

**APROVAÇÃO LOCAL PARCIAL — NO_GO PARA PRODUÇÃO COM 150 USUÁRIOS.** O ensaio HTTP falhou; este runbook é um procedimento preparado para etapa posterior, não uma ordem de ativação pronta. A autorização anterior para usar MAIN permanece válida, mas não elimina o bloqueio técnico de capacidade. O destino real é `C:\PPLID`; `C:\Users\c93123a\PPLID` identifica somente a instalação desta máquina.

Executar com a conta operacional comum, sem exigir administrador. A mesma conta deve ser dona do proxy, dos slots e do supervisor: o canal de controle usa DPAPI CurrentUser. Não copiar descoberta, tokens ou runtime de outra conta/máquina.

As regressões locais preservadas no checkpoint passaram: 421 casos selecionados de backend, 1.375 casos Vitest em 270 arquivos, tipos/build e auditorias npm sem entradas. O ensaio local acelerado de 150 usuários foi planejado para 120 segundos e abortou em 31,66 segundos: 220 requisições, 80 HTTP 200, 84 HTTP 503, 56 timeouts e p95 de 30.016 ms. Houve 99 falhas em 202 sondas. Uma promoção para R1 aos 10,03 segundos foi recusada antes de trocar. Não se comprovou capacidade sustentada nem SLA; a rampa, o baseline prolongado, a sustentação de 30 minutos e o cenário misto de 300 segundos ficam adiados e não contam como aprovados nesta execução acelerada.

Antes de usar as etapas de ativação, investigar a capacidade HTTP/pool/filas/sondas e repetir o ensaio afetado. O limite de conexões 256 passou em teste focal de keep-alive/health, mas a aplicação continuou falhando sob a mesma carga. Waitress chegou a fila de 83 com 16 threads e a sonda de um segundo compartilha essa fila. Não foi demonstrada saturação física do host ou causa exclusiva no PostgreSQL, e não houve baseline legado equivalente. Suítes verdes não removem esse bloqueio; o piloto real no servidor permanece obrigatório.

Antes da primeira ativação, preencher e conferir o registro abaixo com o [relatório local](RELATORIO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md). Campos pendentes impedem tratar este runbook como ordem de execução pronta.

| Campo | Valor a registrar |
|---|---|
| Código de operações congelado para o ensaio local | `1b5b37fad07042337c2834d30725ef6b6ee0dea4`; registrar a revisão efetivamente publicada no servidor |
| R1: commit funcional de retorno no servidor | PENDENTE: conferir o SHA real instalado; não substituir automaticamente pelo controle de laboratório |
| R2: commit candidato no servidor | PENDENTE de construção com configuração real e homologação compatível |
| Diferença funcional R1/R2 | PENDENTE; ambas precisam dos mesmos contratos web, drenagem e controle |
| Configuração MAIN e repositório de operações | Caminhos reais lidos de `machine.config.json` e da instalação |
| Conta operacional e tarefa que a inicia | SID, nome da tarefa, usuário, ação, diretório e política de logon/reinício |
| Janela e tempo máximo aceitável de retorno | Definir com a operação antes do piloto |
| Backup e restauração testados | Referência do backup e evidência de restauração em banco isolado |
| Upload máximo e jornadas críticas | Confirmar com o negócio; não inferir do teste sintético |
| Recursos mínimos | Fixar por medição com dois slots, supervisor, PostgreSQL e build concorrente |

## 1. Preflight somente leitura

1. Confirmar os SHAs completos e a procedência dos dois repositórios. Preservar alterações existentes; não executar reset/clean para fabricar um checkout limpo.
2. Ler somente as chaves necessárias de `C:\PPLID\machine.config.json` e de `envConfigPath`: `baseDir`, MAIN, portas, estratégia e paths de ferramentas. Não imprimir `.env`, senhas, strings de conexão ou a configuração completa em chamados/logs.
3. Conferir `whoami /user`, versão PowerShell, Python, `node.exe --version`, `npm.cmd --version` e PostgreSQL. Node 22.14.0 foi testado; o frontend desta candidata exige `^22.12.0 || >=24.0.0`, portanto Node 18 não atende. Node é necessário ao build; o frontend de produção é servido por Python/WhiteNoise.
4. Conferir leitura/execução das ferramentas e escrita/criação/rename nas pastas de releases, runtime, logs, assets e media. O build contido usa criação suspensa com atribuição atômica `JOB_LIST`, cujo preflight exige Windows 10/Windows Server 2016 ou posterior e políticas que permitam a operação. Verificar criação de junction e Job Objects com a conta comum e a política reais em pasta descartável. O ensaio local não comprova a política do servidor. Não testar sobrescrevendo `current` ativo.
5. Conferir recursos e sua tendência com a carga existente. A máquina local foi reiniciada pelo usuário por falta de espaço durante a homologação. Antes disso houve falhas de alocação/processos e erro Windows 1455 em logs; RAM física livre isoladamente não bastou para avaliar a margem.

O exemplo abaixo apenas mede disco, RAM física e margem de memória comprometida no Windows. Usar o Python previamente conferido da instalação; conservar resultado sem segredos junto às evidências do piloto.

```powershell
$PilotPython = 'C:\caminho-confirmado\python.exe'
@'
import ctypes, json, shutil
class Memory(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
        (name, ctypes.c_ulonglong) for name in (
            'totalPhys', 'availPhys', 'totalPage', 'availPage',
            'totalVirtual', 'availVirtual', 'availExtended')]
m = Memory()
m.length = ctypes.sizeof(m)
if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
    raise ctypes.WinError()
disk = shutil.disk_usage('C:/PPLID')
print(json.dumps({
    'diskFreeMiB': disk.free // 2**20,
    'physicalAvailableMiB': m.availPhys // 2**20,
    'commitAvailableMiB': m.availPage // 2**20,
    'commitLimitMiB': m.totalPage // 2**20,
}, indent=2))
'@ | & $PilotPython -
```

Reservar disco para as duas releases físicas, venvs, dependências/cache do build, static/media, acervo de assets antigos, logs e PostgreSQL, com folga medida. Configurar `minimumDiskMb`, `minimumAvailableMb` e `minimumCommitMb` a partir dessa medição. `minimumCommitMb` usa `minimumAvailableMb` como fallback se ausente e deve ser inteiro de pelo menos 128 MiB; o valor mínimo aceito pelo parser não é uma recomendação de capacidade. As travas conferem disco, RAM e margem de commit antes de iniciar processos/build e antes de retirar o slot aquecido na promoção. Não reservam recursos contra consumo de outros processos nem substituem monitoramento. Não iniciar carga/build quando a folga estiver caindo. Não alterar paginação ou encerrar aplicações corporativas para aprovar o piloto. Retenção de assets exige acompanhamento: não há coleta automática desse acervo.

6. Inventariar listeners públicos e suas identidades (PID, criação, executável e linha de comando), tarefas, watchers, console e jobs legados. `Get-NetTCPConnection` ou `netstat -ano` são descoberta; uma porta sozinha não autoriza encerrar o processo.
7. Conferir as duas portas públicas preservadas, quatro portas internas A/B e porta administrativa local, incluindo colisões com DEV/HOM desabilitados e faixa dinâmica do Windows. Internas e administração permanecem em loopback. Testar acesso público de uma máquina cliente corporativa; não alterar firewall neste procedimento.
8. Conferir Caddy 2.11.9 e SHA-256 do executável `2622e239c79df700b0eb92cbd950d1edee365e155a7859f358f0e55f002df591` com `Get-FileHash -Algorithm SHA256`. A conta deve poder executá-lo sob as políticas corporativas. A ferramenta não instala serviço Windows.
9. Validar bindings, hosts/CSRF, cookie/sessão, URLs de API de build, media persistente, acesso PostgreSQL e integrações reais. O `dist` do laboratório contém configuração sintética e não é artefato de produção.
10. Capturar estado/policies/permissões antes da mudança e conferir o conflito de menus abaixo por conexão de leitura autorizada ao banco correto. Não exportar dados pessoais ou credenciais.

```sql
SELECT id, menu_key, section, label, parent_menu_key, route_names, is_active
FROM access_portalmenuoption
WHERE menu_key IN (
    'performance-desenvolvimento', 'planejamento-performance-desenvolvimento'
)
OR parent_menu_key IN (
    'performance-desenvolvimento', 'planejamento-performance-desenvolvimento'
)
ORDER BY section, id;
```

A migration `access.0046_split_planning_performance_menu_key` altera somente a identidade antiga pertencente à seção `planejamento` para `planejamento-performance-desenvolvimento` e as referências de pai dessa seção. Mantém PK e os demais campos; a identidade de Operações continua `performance-desenvolvimento`. Se as duas chaves existirem e a origem for Planejamento, ela aborta atomicamente. Revisar os registros e resolver com o responsável; não apagar a configuração nem mesclar permissões automaticamente. Se a origem for Operações, a migration não a renomeia.

**Bloquear avanço** por identidade/processo incerto, task desconhecida, conflito de menu, perda de margem de recursos, paths/configuração divergentes, backup sem restauração ensaiada ou par R1/R2 sem homologação compatível.

## 2. Publicar e construir sem trocar o tráfego

Publicar os commits revisados pelo fluxo normal da equipe, preservando o legado até a janela de migração. Construir R1 e R2 a partir dos SHAs congelados usando configuração real de MAIN e o pipeline da revisão. Verificar lockfile, constraints Python, `pip check`, manifesto, contratos e hashes. Os helpers `readiness_build`, `readiness_smoke` e `local_capacity` aceitam laboratório descartável; não são comandos de implantação em `C:\PPLID`.

O controle R1 local é `1cb6444748473ced40eea6b51daf5f4162424890`; a R2 local é `fac41fdd35bb07c0967c8599c4a38b8c419516e2`, com a correção do filtro de pares incompletos. Esse par não representa automaticamente o código instalado no servidor. R1 conserva o envio de órfãos, ignorados pelo backend, mesmo com a opção de ignorá-los marcada. Registrar essa limitação conhecida no ensaio de retorno; não afirmar que R1 passou os 1.375 casos que incluíam a correção de R2.

Os dois builds locais passaram no laboratório `build-b3b05159`: pipeline 0, `pip check` 0 e fonte limpa. Foram criadas venvs novas com reutilização do cache pip; não clonadas de `current`/`previous`. Os manifestos registram 5.891/5.892 artefatos e 84 pins conferidos em cada release. O smoke verificou troca/rollback R1 → R2 → R1 → R2, sessão, documento e navegação lazy no navegador e 135 assets antigos; essas evidências foram preservadas e reaproveitadas. O ensaio integrado final de carga falhou. O último relatório confirma PostgreSQL e supervisor parados; conservar a prova final de identidades/processos. A restauração de snapshot sintético de outro laboratório não atende, por si, ao requisito de backup/restauração dos dados reais do servidor.

Na janela controlada, depois de inventariar/suspender os gatilhos concorrentes e registrar a configuração anterior, configurar MAIN como `blueGreen` com os valores conferidos, mantendo os processos legados existentes enquanto se constrói. Alterar a flag não os encerra nem transfere ownership. A configuração deve conter o mirror com os commits disponíveis, os paths corretos e recursos para o build; o comando não busca um SHA que não esteja nesse mirror.

Construir as duas releases com a nova ação `build`, que funciona antes de haver slot ativo e não executa `prepare`, promoção, ativação ou start de workers. Ela mantém o mutex e contém somente a árvore de build em Job Object; só aceita êxito com validação e recibo de árvore vazia. Se a release já estiver fixada por slot/supervisor, valida e reutiliza o artefato em vez de reconstruí-lo. A ação possui nove casos focais aprovados, conforme o relatório; o build real e a homologação dos artefatos continuam sendo requisitos separados.

```powershell
$PilotOps = 'C:\caminho-real-do-repositorio-de-operacoes'
$PilotMachine = 'C:\PPLID\machine.config.json'
$env:OPS_MACHINE_CONFIG = $PilotMachine
$R1Release = '<pasta-sha-da-R1-real-validada>'
$R2Release = '<pasta-sha-da-R2-real-validada>'
Set-Location $PilotOps
.\deploy\bluegreen.ps1 -Action build -Environment MAIN -Sha $R1Release -RunId '<id-unico-build-r1>' -MachineConfig $PilotMachine
if ($LASTEXITCODE -ne 0) { throw 'Build R1 nao aprovado; preservar listeners legados' }
.\deploy\bluegreen.ps1 -Action build -Environment MAIN -Sha $R2Release -RunId '<id-unico-build-r2>' -MachineConfig $PilotMachine
if ($LASTEXITCODE -ne 0) { throw 'Build R2 nao aprovado; preservar listeners legados' }
```

Conferir o JSON retornado (`built`, `reused`, `shaFull`, `release`, `metadata`), os logs e `bluegreen/builds/<sha>.json`. Não fabricar recibo, marcar `meta.built` manualmente ou executar o entrypoint privado como atalho. `deploy/bluegreen_prepare.ps1` é chamado pelo orquestrador para build/validação; não executa migrations nem muda `current`. A ação `prepare` abaixo exige a release já construída e validada; não constrói fontes.

Antes do piloto, registrar a forma já usada pela instalação para carregar o ambiente MAIN e executar `manage.py`. Não depender do `.env` do checkout errado. Em uma release física verificada, conferir primeiro o plano com:

```powershell
# $ReleasePython e $ReleaseBackend devem apontar à release real verificada.
Push-Location $ReleaseBackend
try {
    & $ReleasePython manage.py migrate_preserving_rbac --plan
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao consultar plano de migrations' }
} finally { Pop-Location }
```

Migrations são uma etapa explícita e revisada, fora do start de slots. Antes de aplicar, avaliar cada migration pendente, compatibilidade com R1 e R2, locks, duração, backup e limite de espera. Depois, executar `migrate_preserving_rbac --noinput` com o ambiente MAIN confirmado e os limites de banco definidos para a janela. Esse comando preserva a configuração existente de RBAC, rotas, menus e grants, permitindo apenas a transformação revisada da identidade de Planejamento. Ele não comprova compatibilidade semântica ou ausência de bloqueio SQL. Sua proteção transacional não torna uma migration incompatível segura para execução online.

Conferir novamente menus/PKs e permissões por perfis reais após a aplicação. `migrate --check` deve terminar sem pendências. Start, promoção, rollback e recovery recusam esquema pendente; não aplicam migrations automaticamente. **Rollback de código não reverte migration nem dados.** Reversão de `0046` pode ser recusada se a chave de destino estiver ocupada e exige análise própria.

## 3. Transferir ownership e fazer a primeira ativação

Este trecho depende do inventário real de listeners, tarefas e jobs. Não executar enquanto os campos da seção inicial e os critérios locais do relatório estiverem pendentes.

1. Manter controlados, na janela já autorizada, somente os gatilhos de deploy/start concorrentes inventariados na construção. Conservar o estado original de cada task/watcher/console para restaurá-lo. Não desabilitar tarefas genéricas por nome parecido.
2. Esperar/reconciliar os jobs legados e transferir ownership de cada serviço/modo/lane conforme o inventário. Sessão de navegador em memória não é transportada entre processos. Quando for necessária reconexão manual, confirmar no painel o estado e a ação antes de permitir novos jobs.
3. Conferir a configuração autoritativa de MAIN já preparada para `deployStrategy: blueGreen`, com paths, portas e limites medidos. A mera troca da flag não transfere os processos já iniciados. Guards legados impedem ações conflitantes.
4. Preparar a mesma versão funcional compatível já publicada (R1), evitando somar a migração da entrada HTTP a uma mudança de negócio. `prepare` inicia o slot interno e administração local do proxy; ainda não toma as portas públicas.

```powershell
# Reutilizar os paths e SHAs conferidos na etapa de build.
Set-Location $PilotOps
.\deploy\bluegreen.ps1 -Action prepare -Environment MAIN -Sha $R1Release -MachineConfig $PilotMachine
if ($LASTEXITCODE -ne 0) { throw 'Preparacao recusada; preservar listeners legados' }
```

`OPS_MACHINE_CONFIG` deve alcançar também os subprocessos PowerShell do build, que resolvem paths pela configuração da máquina. A correção de operações `34f4cb7` faz o pipeline encaminhar o path selecionado em `Config.load`; manter o valor explícito acima também torna a sessão operacional inequívoca. Não definir uma variável global do usuário para contornar configuração divergente.

5. Confirmar ausência de donos legados concorrentes antes de definir `workersOwnershipConfirmed: true`. Essa flag registra a conclusão do inventário; não substitui a prova. Subir o supervisor independente fixado em R1 (ou outra release previamente homologada com contrato idêntico) e conferir readiness e cada classe habilitada.

```powershell
.\deploy\bluegreen.ps1 -Action workers-start -Environment MAIN -Sha $R1Release -MachineConfig $PilotMachine
if ($LASTEXITCODE -ne 0) { throw 'Supervisor nao confirmado; nao ativar' }
```

6. Conferir painel → executor entre processos: status, configuração e um comando controlado autorizado por modo. Verificar autenticação/CSRF/RBAC, geração e o estado efetivo. Uma configuração salva com aplicação pendente não significa execução confirmada.
7. Liberar somente os listeners web legados com o procedimento da instalação já ensaiado e identidades conferidas. Não usar `stop_all`, kill por porta ou encerramento da árvore que possua jobs independentes. Se não houver procedimento comprovado, manter a ativação bloqueada.
8. Ativar imediatamente o proxy nas portas públicas preservadas:

```powershell
.\deploy\bluegreen.ps1 -Action activate -Environment MAIN -MachineConfig $PilotMachine
if ($LASTEXITCODE -ne 0) { throw 'Ativacao falhou; diagnosticar estado e executar retorno ensaiado' }
.\deploy\bluegreen.ps1 -Action status -Environment MAIN -MachineConfig $PilotMachine
```

9. Validar a partir de clientes: login/logout, sessão existente, CSRF, `/falhas/`, rotas diretas e refresh, permissões, consultas, upload/download e painel/SLA. Conferir jobs e efeitos persistidos. Identidade do supervisor deve ser preservada pelos deploys web seguintes.
10. Restaurar os gatilhos inventariados somente após a instalação estar consistente. Registrar a duração observada da primeira ativação; ela pode incluir parada ao transferir as portas legadas.

## 4. Deploy seguinte, observação e rollback aquecido

Com MAIN já ativo em blue-green, usar o pipeline operacional, que mantém o mutex compartilhado durante build, validação e promoção:

```powershell
.\deploy\bluegreen.ps1 -Action pipeline -Environment MAIN -Sha $R2Release -RunId '<id-unico-do-piloto>' -MachineConfig $PilotMachine
if ($LASTEXITCODE -ne 0) { throw 'Pipeline falhou; consultar estado e logs antes de nova acao' }
```

Para uma release física já construída e validada, a ação disponível é `promote -Sha $R2Release`. O nome da pasta de release e o SHA completo do manifesto precisam concordar. O slot anterior permanece aquecido; não executar prune manual de diretórios registrados por slots/supervisor.

Observar as duas portas públicas com sondas sem retry (registrar frequência), sessão/CSRF, abas antigas/assets, p50/p95/p99 por jornada, erros, jobs, filas, CPU, RAM, margem de commit, disco/IO e conexões/locks PostgreSQL. Não presumir disponibilidade porque apenas o proxy aceita conexão. Sem erro na amostra não se prova ausência de pausas menores que o intervalo de sondagem.

Abandonar a candidata por perda/duplicação de dados, quebra de permissões, erro relacionado à troca, crescimento de fila/memória, queda de margem de recursos, supervisor inconsistente ou resultado incerto de jobs. A meta local de p95 até 20% do baseline equivalente é proposta de engenharia; os tempos e o SLA do negócio continuam a ser definidos.

```powershell
.\deploy\bluegreen.ps1 -Action rollback -Environment MAIN -MachineConfig $PilotMachine
if ($LASTEXITCODE -ne 0) { throw 'Rollback nao confirmado; preservar processos e diagnosticar recovery' }
.\deploy\bluegreen.ps1 -Action status -Environment MAIN -MachineConfig $PilotMachine
```

Conferir versão pública, sessão, CSRF, dados, jobs e identidade do supervisor após o rollback. A operação retorna o código web ao slot anterior compatível; não desfaz esquema nem troca o supervisor independente.

## 5. Operação interrompida, drenagem e resultado incerto

Depois de reboot, perda de resposta administrativa ou falha no pipeline, consultar `status`, `events.jsonl`, journal e logs antes de repetir comandos. Preservar o estado em `deploy/MAIN/bluegreen`; não editar PID, apagar lease/journal ou forçar uma release em uso.

```powershell
.\deploy\bluegreen.ps1 -Action recover -Environment MAIN -MachineConfig $PilotMachine
.\deploy\bluegreen.ps1 -Action status -Environment MAIN -MachineConfig $PilotMachine
```

`recover` reconcilia a intenção registrada com tráfego e processos; erro ou `recovery-required` exige diagnóstico. Conferir identidade completa dos processos e efeitos de negócio. Um PID ausente não comprova que todos os descendentes/jobs terminaram. A recuperação de um supervisor morto não significa retomada automática de sessão em memória ou execução exatamente uma vez.

`cancel` solicita cancelamento cooperativo do deploy; não encerra a árvore pip/npm à força. `workers-stop` fecha novas admissões e aguarda ciclos/descendentes, exigindo recibo e identidades coerentes. Timeout mantém a geração em drenagem e bloqueia substituição; aguardar o trabalho e repetir a confirmação após diagnóstico. Atualização do supervisor é uma operação própria, separada do deploy web.

No canal painel/executor, 409 indica conflito/ocupado; 503 pode indicar drenagem, indisponibilidade ou resultado incerto. Consultar estado. Repetir mutação incerta somente com o mesmo `X-Pplid-Command-Id` e `X-Pplid-Executor-Generation`; não criar outra identidade para contornar o erro. Se `configuration_saved=true` e `executor_apply=pending_or_unknown`, reconciliar configuração desejada e estado efetivo. Consultar [contrato do canal](deploy/SUPERVISOR_CONTROL.md).

## 6. Retorno ao legado

É uma manutenção distinta do rollback aquecido e pode exigir parada. Executar somente pelo procedimento real já ensaiado:

1. Suspender os gatilhos concorrentes identificados e registrar seu estado.
2. Solicitar `workers-stop`, confirmar recibo, zero trabalho/descendentes e término da geração; não reativar starters legados se houver incerteza.
3. Executar `shutdown` da camada web e conferir os processos registrados e as portas públicas. `shutdown` preserva supervisor independente; por isso a etapa anterior é necessária ao retorno completo.
4. Arquivar estado e logs somente depois da parada comprovada. Os guards legados também consideram estado blue-green: preservá-lo em arquivo de auditoria fora do path ativo, pelo procedimento revisado, sem simplesmente apagar leases.
5. Restaurar `deployStrategy: legacy` e o apontamento/configuração da release de retorno compatível, usando o procedimento legado da instalação. Não reverter migration de banco como efeito implícito.
6. Reativar listeners e starters legados uma única vez. Conferir sessões, dados, integrações, filas e ownership, depois restaurar os gatilhos.

## 7. Registro de saída do piloto

Guardar SHAs, hashes, ferramentas, configuração sem segredos, conta/tarefas, inventário de processos, migrations aplicadas, tempos, probes, validações por perfil, jobs/efeitos e prova de limpeza de componentes de teste. Não transportar `.env`, dados pessoais, cookies, arquivo de sessões do laboratório ou tokens DPAPI.

Somente encerrar o piloto como aprovado após as validações a partir da rede corporativa, comportamento da conta/tarefa após logon/reboot, carga e integrações reais. A conclusão local prepara essa etapa; não a substitui.
