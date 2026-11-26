# Validação do deploy blue-green

Data: 26/11/2026. Ambiente: Windows, PowerShell 5.1.26100.9711, Python 3.12.4, conta sem administrador (`IsAdministrator=False`), Caddy oficial 2.11.9 Windows amd64.

## Resultado e escopo

Implementação opt-in criada nos repositórios de operações e da aplicação. O modo legado permanece como padrão na configuração versionada. Nenhuma porta ou banco corporativo foi usado nos testes; os serviços e clusters de teste ficaram em diretórios `.local/bluegreen-*`, com portas aleatórias de loopback.

Os ensaios locais aprovados demonstram troca e rollback entre slots, continuidade de requisição longa, preservação de autenticação, proteção de processos e recuperação dos cenários cobertos. **A entrega ainda não representa homologação nem ativação em MAIN.**

## Testes automatizados

| Suíte | Resultado observado |
|---|---|
| `tests/bluegreen` | 29 testes aprovados na execução completa, em 213,49 s. |
| Teste adicional do coletor independente | Aprovado durante promoção e rollback; última execução: 598 amostras, zero erros, em 30 s de coleta. |
| Revalidação direcionada final | 24 testes aprovados, em 39,70 s, após as últimas proteções. |
| Oito módulos selecionados de regressão do console | 35 testes aprovados, em 38,33 s. |
| Parser do PowerShell, compilação Python e sintaxe JavaScript | Aprovados; 21 scripts PowerShell conferidos, incluindo os cinco scripts alterados da aplicação. |

Os testes novos cobrem configuração, default legado, ambientes desabilitados, colisões de porta, corrupção de estado, paths, PID reutilizado, locks reais entre Python/PowerShell, publicação de assets, recursos insuficientes, contratos, ponte PowerShell, preparação no ambiente de destino, journaling de sucesso/falha e os cenários reais de processos descritos abaixo.

São **65 casos distintos aprovados**: 29 na suíte inicial do runtime, um teste adicional do coletor e 35 de regressão do console. A revalidação direcionada de 24 casos repete parte dessa cobertura e não foi somada novamente.

O coletor usa `perf_counter`, de alta resolução no Windows. No ensaio sintético a 10 Hz por endpoint, mediu p50/p95 de 3,416/23,458 ms no backend público e 3,723/23,365 ms no frontend público. São latências do laboratório sob duas sondas concorrentes; não demonstram a meta relativa de 20% contra um baseline de carga corporativa.

Na regressão ampliada foi encontrado e corrigido um `NameError` anterior em `_resolve_origin_branch_sha`: o retorno de `git fetch` era consultado como `fetch_result` sem ser atribuído. O teste existente de fetch malsucedido passou após a correção.

## Tráfego e processos reais

O laboratório usa Caddy, Waitress, Django e SQLite reais, sem substituir as conexões HTTP por mocks.

- Última execução completa: **1.655 sondas, 11 alternâncias e zero erros observados** durante o teste de troca/rollback.
- Execuções anteriores da mesma estratégia também passaram: 1.576 e 1.763 sondas, sem erros.
- Requisição HTTP em streaming iniciada antes da troca concluiu sem truncamento.
- Falha de inicialização e migrações pendentes na candidata preservaram o atendimento da versão ativa.
- Cancelamento antes da troca preservou A; cancelamento durante observação voltou para A.
- Recuperação de intenção persistida e de queda do proxy passou.
- A revisão final também comprovou recuperação com proxy encerrado durante uma intenção de troca e com o registro de PID da candidata ausente, preservando corretamente o slot anterior para rollback.
- Terceira release reutilizou somente o slot inativo; processo antigo foi encerrado e seu asset continuou disponível.
- A configuração inválida do Caddy foi recusada e a anterior preservada.
- Cookies, corpo de POST e método HTTP foram preservados; header de encaminhamento forjado não chegou como endereço confiável ao backend.
- Parada controlada da camada web foi ensaiada no laboratório.

Na prova inicial com reloads completos ocorreram duas conexões fechadas. Esse resultado levou à adoção dos pools fixos com troca pelo endpoint de participação. Não se está usando retries de clientes para transformar falhas em sucesso.

Zero erros na amostra não prova ausência de interrupções menores que a amostragem nem garante o resultado sob outra carga. A comparação formal de p95 contra carga representativa ainda está pendente.

## Código real do PPLID e PostgreSQL

Foi criada uma cópia descartável do checkout local, identificado pelo HEAD `ab539ff9dbcf01c135dc2d21d2a2135be531fdef`, incluindo mudanças locais dos fontes copiados. Não é uma homologação de um commit limpo de produção.

Foram copiados fontes versionados e quatro dependências Python locais explicitamente listadas em `full_app_lab.py`. Não foram copiados `.env` de origem, credenciais, dumps ou diretórios de dados de usuário. O banco e o usuário foram criados exclusivamente no cluster local descartável.

Resultados aprovados:

1. `manage.py check` no código real, sem erros.
2. Aplicação de **605 migrações** no PostgreSQL 14 isolado.
3. Preparação e publicação da aplicação real pelo proxy.
4. Substituição por uma segunda instância da mesma release física e rollback, com identidade de instância diferente.
5. Login real com usuário criado somente no banco de laboratório.
6. Sessão e CSRF preservados após troca e rollback; logout com o token existente aprovado.
7. Timeout de lock de 250 ms comprovado em uma tabela criada somente para o ensaio.
8. Vue real montado no Edge headless, com requisições externas ao laboratório bloqueadas.
9. Login no navegador e sessão/CSRF mantidos na mesma página após troca e rollback.
10. Encerramento da camada web, navegador e PostgreSQL ao final, sem erro de cleanup no relatório aprovado.

O ensaio adicional de navegador também foi aprovado. As imagens `browser-before.png` e `browser-after.png` foram geradas; a imagem final foi inspecionada e mostrou a tela de login Vue renderizada corretamente após o logout de teste.

Evidências locais, preservadas fora do versionamento:

- `.local/bluegreen-full-app-a8148352/report.json`
- `.local/bluegreen-full-app-a8148352/commands.log`
- `.local/bluegreen-full-app-a8148352/postgres.log`
- `.local/bluegreen-full-app-a8148352/deploy/MAIN/bluegreen/events.jsonl`
- `.local/bluegreen-final-02/` — fixtures e logs da execução completa da suíte.
- `.local/bluegreen-probe-latest.summary.json` — amostras/latências do coletor independente.
- `.local/bluegreen-full-app-a8148352/browser-before.png` e `browser-after.png` — Vue real no Edge.

Essas 605 migrações ocorreram em banco novo e isolado. Não comprovam compatibilidade entre duas versões da aplicação diante de uma migração real em produção. O deploy online implementado bloqueia migrações pendentes.

## Matriz do plano original

“Parcial” identifica cobertura útil já executada e o que ainda falta; não significa aprovação integral do cenário.

| ID | Situação | Evidência ou pendência |
|---|---|---|
| T01 | Aprovado nos testes de compatibilidade | Default `legacy` e regressão selecionada do console. |
| T02 | Aprovado | Configuração desabilitada recusada. |
| T03 | Aprovado | Colisão detectada; listener alheio preservado. |
| T04 | Aprovado | Slots reais A/B com releases e portas distintas. |
| T05 | Aprovado no laboratório | Retirada de slot inativo preserva o ativo. |
| T06 | Aprovado | Identidade de PID divergente não autoriza encerramento. |
| T07 | Parcial | Falha de preparação coberta por fronteira de subprocesso; candidata quebrada testada de fato. Falta build completo de nova versão em ambiente de homologação. |
| T08 | Aprovado nos casos cobertos | Readiness/identidade e falha de startup bloqueiam publicação. |
| T09 | Aprovado por contrato/verificação | SHA e instance ID conferidos antes/depois da troca. |
| T10 | Parcial | 11 alternâncias sem falha de sonda; faltam carga representativa e meta formal de p95. |
| T11 | Aprovado | Streaming iniciado antes da troca concluiu. |
| T12 | Parcial | POST, corpo, cookies e streaming cobertos; falta matriz de upload/download volumosos e keep-alive prolongado. |
| T13 | Pendente/avaliar aplicabilidade | Não foi localizado uso de WebSocket/EventSource no código pesquisado; exports HTTP em streaming existem. Não foi homologado WebSocket em Waitress/WSGI. |
| T14 | Aprovado nos ensaios HTTP e navegador | Login, sessão, CSRF, troca, rollback e logout no PPLID copiado e no Edge headless. |
| T15 | Parcial | Chunk antigo preservado após terceira release; falta concluir o ensaio de navegação/lazy loading real. |
| T16 | Pendente | Cliente antigo com contrato de API de release nova funcionalmente diferente. |
| T17 | Parcial | Roteamento e readiness públicos reais aprovados; falta matriz funcional de `/falhas/` com dados de homologação e clientes de rede. |
| T18 | Aprovado nos casos cobertos | Reversão após cancelamento/observação e rollback aquecido. Tempo sob carga real pendente. |
| T19 | Aprovado | Configuração inválida recusada e anterior preservada. |
| T20 | Parcial | Intenção interrompida e queda do proxy cobertas; falta matar orquestrador em todos os pontos, incluindo build e launch de workers. |
| T21 | Aprovado nos casos cobertos | JSON corrompido bloqueia operação; gate com intenção é reconciliado. |
| T22 | Aprovado para o mutex | Conflito entre processos Python e PowerShell comprovado. |
| T23 | Parcial | Ações usam o mutex comum e operações legadas perigosas foram bloqueadas; falta stress concorrente de todas as combinações. |
| T24 | Aprovado | Cancelamento antes da troca e durante observação. |
| T25 | Pendente para migração online | Política atual recusa pendências; expansão/contração real precisa de homologação separada. |
| T26 | Aprovado de forma conservadora | Qualquer pendência de migração bloqueia a candidata. |
| T27 | Parcial | Timeout SQL real de 250 ms no cluster descartável; não há coordenador de migrations online homologado. |
| T28 | Parcial | Falha/pendência de esquema impede publicação; falta falha de migração real em cópia com duas versões. |
| T29 | Parcial | Guards web-only e ownership implementados; starters dos robôs corporativos não foram executados nem homologados. |
| T30 | Parcial | Assets e releases registradas protegidos; falta ensaio completo do prune com todos os supervisors de negócio reais. |
| T31 | Aprovado para preflight | Memória insuficiente recusa o início; carga e limites do host real pendentes. |
| T32 | Aprovado no laboratório | Queda e recuperação de proxy proprietário testadas. |
| T33 | Pendente | Reboot/logon e tasks no servidor corporativo não executados. |
| T34 | Parcial | Console independente e regressão de restart aprovadas; falta atualização ao vivo do console corporativo durante carga. |
| T35 | Pendente | Pipeline recompila com configuração do destino, sem copiar dist de outro ambiente; promoção real DEV/HOM não executada. |
| T36 | Parcial | Shutdown web ensaiado; retorno completo ao legado com workers e janela real pendente. |

## Condições antes de ativar MAIN

- Concluir os itens pendentes relevantes em ambiente de homologação autorizado.
- Dimensionar duas instâncias aquecidas e as sondas de saúde sob a carga real. As sondas compartilham a capacidade HTTP dos slots; não usar timeouts agressivos para mascarar saturação.
- Homologar a separação, execução única, recuperação e atualização dos workers com os responsáveis pelas automações.
- Confirmar permissão corporativa para o binário/proxy e proteção dos arquivos e da administração local.
- Ensaiar liberação dos listeners legados sem encerrar jobs e sem alterar firewall.
- Ensaiar ativação inicial, retorno ao legado e reboot/logon.
- Revisar API/cliente antigo, sessões, uploads, `/falhas/` e a retenção do acervo de assets.

O runbook está em [BLUEGREEN.md](BLUEGREEN.md). Nenhuma evidência local deste relatório autoriza, por si só, ativação em produção.

## Revisão em branches isoladas

- Operações: `feat/bluegreen-deploy-20261126`, worktree `.local/bluegreen-review-ops`.
- Commits de runtime: `0b43c70` (slots/proxy) e `dbd44a0` (recuperação após interrupção).
- Aplicação: `feat/bluegreen-web-only-20261126`, worktree `.local/bluegreen-review-app`; commit do contrato e guards: `b683f027`.

As branches originais foram preservadas. Os arquivos editados continuam disponíveis nos checkouts originais e também nas worktrees de revisão. Alterações preexistentes da aplicação não foram incluídas no commit dos guards. Não houve push, merge ou publicação.
