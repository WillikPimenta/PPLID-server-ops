# Ponto de retomada — homologação local blue-green

**Atualizado em 27/11/2026: rodada reduzida encerrada com aprovação parcial e bloqueio de capacidade. Produção não liberada.**

Os resultados atuais estão em [RELATORIO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md](RELATORIO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md) e o procedimento do servidor em [RUNBOOK_PILOTO_SERVIDOR_BLUE_GREEN.md](RUNBOOK_PILOTO_SERVIDOR_BLUE_GREEN.md). A ação inicial `build` que faltava foi implementada e passou nos nove testes direcionados. As duas releases foram construídas e tiveram 84 dependências conferidas cada; troca/rollback, sessão, navegação lazy e 135 assets antigos passaram.

A carga de 150 sessões falhou mesmo após corrigir o limite de conexões para 256: 220 requisições, 84 respostas 503 e 56 timeouts. O teste foi interrompido; a promoção tentada foi recusada antes de trocar. **Próximo passo: corrigir/qualificar o atendimento HTTP e a competição das sondas de saúde nesse perfil; depois repetir somente essa carga.** Não repetir os 1.375 testes frontend, 421 backend ou builds sem alteração que os invalide. O diário parado e o seed existente permitem retomada controlada com `--resume-stopped`; não apagar journals ou recibos para forçar execução.

Referências finais: R1 `1cb6444748473ced40eea6b51daf5f4162424890`, R2 `fac41fdd35bb07c0967c8599c4a38b8c419516e2`; código de operações `1b5b37fad07042337c2834d30725ef6b6ee0dea4`, com limite de conexões corrigido em `f0deceaacea35f43297c34d22b485f4c870ce255`. Worktrees preservadas e mudanças versionadas nas branches de revisão. Resumo estruturado: `.local/bluegreen-local-20261126-151916/summary.json`; laboratório final: `.local/bluegreen-readiness/build-b3b05159`. PostgreSQL/supervisor encerrados, journal `stopped`, zero slots/proxy e nenhum processo de laboratório identificado na auditoria.

---

## Registro histórico da pausa de 26/11

O conteúdo abaixo descreve aquela pausa e foi preservado como histórico. O estado e o próximo passo atuais são os indicados acima.

Produção não foi ativada e continua sem liberação. O trabalho foi preservado nas worktrees de revisão; existem alterações sem commit que devem ser mantidas. O próximo agente deve ler este arquivo e o checkpoint JSON antes de executar qualquer comando de implantação.

## Localização do trabalho salvo

| Item | Caminho / referência |
|---|---|
| Workspace | `C:\Users\c93123a\PPLID-server-ops` |
| Plano em execução | [PLANO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md](PLANO_HOMOLOGACAO_LOCAL_BLUE_GREEN.md) |
| Aplicação de revisão | `.local/bluegreen-review-app` — `feat/bluegreen-web-only-20261126` |
| HEAD da aplicação | `74b1cece` — SHA completo em `pause-checkpoint.json` |
| Operações de revisão | `.local/bluegreen-review-ops` — `feat/bluegreen-deploy-20261126` |
| HEAD de operações | `aa411fa` — SHA completo em `pause-checkpoint.json` |
| Evidências desta execução | `.local/bluegreen-local-20261126-151916` |
| Checkpoint estruturado | [.local/bluegreen-local-20261126-151916/pause-checkpoint.json](.local/bluegreen-local-20261126-151916/pause-checkpoint.json) |
| Cópia adicional do trabalho pendente | `.local/bluegreen-local-20261126-151916/pause-checkpoint/` |
| Auditoria de processos na parada | `.local/bluegreen-local-20261126-151916/pause-cleanup-proof.json` |

O snapshot contém patches das alterações rastreadas, status/HEADs e cópias selecionadas dos novos arquivos de código, testes e documentação. Não inclui venvs, node_modules, bancos, cookies ou arquivos de ambiente. As worktrees originais continuam sendo o local principal de trabalho. Não aplicar os patches por cima delas sem conferir o diff: são cópias para recuperação, não comandos de retomada.

Os checkouts anteriores do usuário já tinham alterações. Não usar reset, clean ou checkout forçado para “limpar” o workspace. A aplicação original é `C:\Users\c93123a\PPLID`; no servidor real, a instalação é `C:\PPLID`. A autorização anterior para MAIN e o pico de 150 usuários continuam registrados.

## Resultados confirmados até a parada

| Área | Resultado | Evidência relativa ao diretório de evidências |
|---|---|---|
| Frontend completo | **270 arquivos e 1.375 testes aprovados; zero falhas/pendentes** | `tests/components-vitest-after-reboot.json` |
| Tipos vue-tsc 3 | Aprovado após quatro correções pequenas; heap de 4.096 MiB | `tests/frontend-reboot-build-summary.json`, `tests/frontend-typecheck-fixes.json` |
| Build frontend | `npm.cmd run build:deploy` aprovado, cerca de 106 segundos | `tests/build-after-reboot-outside-sandbox.log` |
| Regressões de alias/Development | 16/16 aprovados | `tests/type-fixes-targeted-after-reboot.json` |
| Assert/tsx | 32 arquivos aprovados | `tests/frontend-assert-final.json` |
| Auditoria npm completa / produção | Zero entradas nas duas auditorias | `audits/npm-full-final.json`, `audits/npm-production-final.json` |
| Backend funcional consolidado | **421 testes aprovados**, incluindo permissões, autenticação, relatórios, workers e limites de upload | `functional/menu-migrations-01/report.json` e `commands.log` — última rodada: `Ran 421 tests … OK` |
| PostgreSQL dessa rodada | Parada confirmada pelo harness | `functional/menu-migrations-01/report.json`: `postgresStopped: true` |
| Segurança e gerador de carga | 29 aprovados | `tests/ops-resource-recovery-02.xml` |
| Segurança e transferência durante troca | 26 aprovados; o único ensaio de transferência antes falho passou | `tests/ops-resource-transfer-03.xml` |
| Pipeline, recuperação e guards de laboratório | 18 aprovados | `tests/ops-pipeline-transaction-short-path.xml` |
| Manutenção e configuração explícita | 9 aprovados | `tests/ops-maintenance-guards-02.xml` |
| Contenção de build e proteção da release do supervisor | 21 casos distintos aprovados em duas rodadas com sobreposição | `tests/build-process-after-reboot-final.xml`, `tests/build-pinned-release-final.xml` |
| Recuperação por sessão/dono Windows | 6 verificações aprovadas; há casos repetidos de rodadas anteriores | `tests/build-session-recovery-final.xml` |

Não somar as rodadas como casos distintos. A suíte anterior de operações teve 67/68 aprovados; a transferência restante passou depois. As mudanças posteriores de contenção/recibo receberam os testes direcionados acima; a regressão final necessária deve considerar o conteúdo que for congelado.

Os 421 testes do backend usaram MD5 **apenas para acelerar senhas das fixtures**, explicitamente registrado no relatório. Uma rodada anterior de 117 casos passou com o hasher real; os servidores HTTP do futuro ensaio de carga mantêm a configuração real. Isso não altera o hasher da aplicação.

A última rodada do backend começou quando o HEAD ainda era `72625cd1`, com mudanças de backend sem commit. O commit posterior `74b1cece` contém somente fixtures de frontend. O novo módulo de testes do executor de migrations foi criado depois da descoberta dos 421 casos e **não está incluído nesse resultado**.

## Mudanças já preservadas e trabalho sem commit

Commits da aplicação: `d76e27bc` (atores UUID no canal), `727af9c9` (dependências), `ba763fdc` (identidade de menu/preservação de RBAC), `fb68c4f3` (runner assert), `72625cd1` (tipos Vue) e `74b1cece` (fixtures do frontend).

Commits de operações: `ca198c4` (UUID no protocolo canônico), `34f4cb7` (RAM/commit/disco e configuração explícita), `67a4a30` (paths e manutenção) e `aa411fa` (árvore de build contida e recibo obrigatório).

O canal painel/supervisor usa JSON local vinculado à conta Windows por DPAPI, valida geração/identidade e conserva os controles de acesso. As cópias normalizadas continuam iguais:

- Drenagem: SHA-256 `18eee816f5d506b77551ceba5645ef25045e5adf21479e1bcd33f211c2d747cd`.
- Controle do supervisor: SHA-256 `dfce8e8116225925d60ca75047942c81ae081193ef0b940e0263ebaa60342d20`.

**Pendências sem commit que não devem ser descartadas:**

- Aplicação: correções de fixtures RBAC/rotas/reset diário, expansão dos testes reais de drenagem, `test_upload_limits.py` e helper/teste `produtividadeHistoricalImport` que corrige a seleção de pares incompletos. Essas mudanças aplicáveis já participaram das rodadas aprovadas.
- Aplicação: `backend/apps/access/tests/test_menu_identity_executor.py`, dois casos novos com comando/migration executor/post_migrate reais. Somente sintaxe verificada; execução pendente e deve ocorrer em rodada separada por usar `TransactionTestCase`/flush.
- Operações: patch de sessão Windows em `build_process.py` e `test_build_process.py`, aprovado em seis verificações, ainda sem commit.
- Operações: harnesses `application_load.py`, `load_seed.py`, `local_backend_checks.py`, `local_capacity.py`, `local_mixed_job.py`, `release_manifest.py`, alterações de `readiness_smoke.py` e testes de suporte. Parte dos harnesses teve somente revisão estática; o ensaio completo ainda não ocorreu.
- Rascunhos de relatório/runbook na raiz da worktree de operações. Eles ainda precisam refletir os 421 testes e os últimos hardenings; não são uma entrega final liberada.

O smoke foi ampliado para verificar assets antigos com hashes/cabeçalhos e navegação lazy no documento R1 após promoção. O ensaio misto proposto usa importação CSV real com 20 mil registros, três jobs de fila e download Parquet. **Essas ampliações ainda não foram executadas.**

## Próximo passo obrigatório ao retomar

**Resolver a interface do primeiro build antes de tentar ativar blue-green.** O novo `Manager.contract` exige recibo de build validado. O `pipeline` atual exige uma instância já ativa; chamar `bluegreen_prepare.ps1` diretamente não produz o recibo exigido. Falta uma ação CLI/PowerShell **`build` sem promoção**, que reutilize a contenção de processos e os recibos, funcione antes da primeira ativação e preserve todas as proteções de releases usadas por slots/supervisor.

Esse bloqueador foi identificado na revisão do runbook. **Nenhuma implementação ou teste da ação `build` foi iniciado antes da parada.** Não contornar criando recibo manual ou removendo o guard.

Depois de o usuário pedir para continuar:

1. Conferir este checkpoint, os diffs atuais e disco/RAM/margem de commit. Não reiniciar testes já aprovados sem mudança que justifique isso.
2. Implementar/testar a ação inicial `build`, atualizar o runbook e versionar o patch de sessão Windows já aprovado.
3. Executar separadamente `apps.access.tests.test_menu_identity_executor` no banco descartável existente; investigar qualquer falha sem desativar preservação ou sinais reais.
4. Consolidar commits limpos e escolher R1/R2 compatíveis. O par ainda não foi congelado. A diferença do helper de pares incompletos está separada, mas a adequação de R1 como retorno ainda precisa ser decidida e documentada. Não criar commit vazio para aparentar duas versões.
5. Sincronizar somente os arquivos operacionais revisados necessários ao workspace de execução e comparar hashes. Os helpers calculam a raiz por `__file__`; executá-los diretamente da worktree cria um laboratório em outro lugar. Não sobrescrever alterações alheias do checkout original.
6. Construir duas releases novas pelo pipeline real, venvs novas, manifests e constraints. Não promover os builds antigos como se validassem o contrato atual.
7. Executar R1 → R2 → R1 → R2, navegador/sessão/CSRF/assets, supervisor independente, recuperação e carga real: baseline equivalente, progressão 10/50/100/150, sustentar 150 por 30 minutos se os recursos permitirem e ensaio misto adicional.
8. Finalizar relatório, runbook, SHAs/hashes, checklist do plano e prova de limpeza. A liberação real continua dependente do servidor, rede/conta/tasks, integrações e requisitos do negócio.

O Caddy local verificado fica em `.local/bluegreen-tools/caddy-2.11.9/caddy.exe`. PostgreSQL: `C:\Program Files\PostgreSQL\14\bin`. Node usado: 22.14.0; o `engines` atual exige `^22.12.0 || >=24.0.0`. A conta é comum, sem administrador.

O cluster funcional reaproveitável está em `functional/menu-migrations-01/pgdata`, porta 25008, confirmado parado. O harness aceita `--resume` somente com parada comprovada. Interpretador de dependências usado nos testes: `.local/bluegreen-readiness/build-ca04e41c/deploy/MAIN/releases/6ab3f747/backend/.venv/Scripts/python.exe`, com código da worktree via PYTHONPATH. Essa utilização não torna a release antiga apta a promoção.

## Cuidados na próxima sessão

- Houve reinício informado por falta de espaço e, antes dele, pressão de memória/commit. Após a retomada havia aproximadamente 10,5 GiB livres; conferir os valores atuais no JSON da pausa. Não aumentar paginação nem encerrar processos corporativos.
- Manter builds, tipos e carga pesada em sequência. Os builds de laboratório agora verificam reservas antes de iniciar e interrompem sua própria árvore se atingirem o limite durante a preparação.
- Usar `npm.cmd`. Tipos precisaram de heap de 4.096 MiB; a tentativa de 1.536 MiB falhou por teto do Node.
- Usar diretórios temporários curtos, como `.local/bg-tmp/<id>`. Caminhos longos causaram falhas de MAX_PATH; não é necessário alterar o registro do Windows.
- O sandbox impediu subprocessos esbuild e alguns temporários pytest. As verificações necessárias passaram fora do sandbox, pela revisão automática de aprovação, com a mesma conta comum e sem UAC. Não alterar ACLs/política global para contornar isso.
- Recuperação de recibo de build incompleto de outra sessão Windows ou dono inacessível bloqueia. Não presumir que a ausência de um Job Object `Local` em outra sessão prova término.
- Nenhum reset, descarte ou limpeza de evidências foi feito para parar. A auditoria da pausa apenas observa processos dos laboratórios; processos da aplicação original ou de outras tarefas devem ser preservados.

Para retomar, pode pedir: **“Continue a partir de PONTO_DE_RETOMADA_BLUE_GREEN.md.”**
