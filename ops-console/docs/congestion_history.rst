Congestionamento no Ops Console
===============================

Uso
---

Abrir Monitoramento > Congestionamento. A tela trabalha com um ambiente por
vez e inicia em MAIN quando habilitado. Os períodos são 1 h, 6 h, 24 h
(padrão), 7 dias e intervalo personalizado de até sete dias.

A última observação recebida é identificada por horário e PID. Ela não é um
health check atual. Os episódios apresentam fila máxima e ocupação máxima
observadas; selecionar um episódio abre seus gráficos e snapshots. Selecionar
um ponto do gráfico ou cartão abre as rotas aguardando/em execução e os IDs
das execuções demoradas. Espera, execução e acumulado são separados.

Ao inspecionar um episódio ou snapshot, o acompanhamento automático é pausado.
Marcar Acompanhar novos registros retoma a seleção da observação mais recente.
A atualização ocorre a cada quinze segundos enquanto a aba está visível e
o acompanhamento está ativo. Atualizar permite uma consulta manual pausada.
Novos eventos podem levar aproximadamente vinte segundos (coleta + tela) para
aparecer; importação inicial pendente acrescenta atraso informado na tela.

Há atalhos nos eventos de disponibilidade e nos detalhes dos logs backend/queue.
O atalho dos logs usa o horário original do evento, quando presente, em vez
do horário de ingestão. Extrair log mantém cinco minutos de contexto antes e
depois do trecho observado, limitado ao momento da extração.

Dados fictícios
---------------

O botão Dados fictícios alterna para demonstração local com identificação
visível. Nenhuma API real é consultada enquanto o modo está ativo; os dados
não são gravados no SQLite e o botão de extração de produção fica desabilitado.
Uma consulta real iniciada antes da troca é cancelada e sua resposta ignorada.

Os cenários disponíveis são:

* Fila curta: 16 threads ocupadas e uma requisição aguardando, seguidas de
  fila esvaziada com 13 execuções; intervalo de 71 ms inspirado no caso real.
* Saturação e recuperação: pico de 80 requisições pendentes, execuções
  demoradas e recuperação da fila. O produtor fictício usa intervalo de 30 s.
* Reinício e coleta incompleta: dois processos, episódio sem fim, início
  ausente, listas parciais, omissões e desconexão detectada.

Os filtros de ambiente e período também funcionam na demonstração. Voltar aos
dados reais descarta a seleção simulada e consulta novamente as fontes reais.
Recarregar a página inicia fora da demonstração.

Arquitetura e configuração
-------------------------

A implementação pertence ao PPLID-server-ops. Não muda o backend Django,
threads, conexões ou timeouts do Waitress. O backend precisa continuar com
PPLID_QUEUE_DIAGNOSTICS=1 e a instrumentação compatível já implantada.

Um coletor daemon separado lê somente os arquivos locais a cada cinco
segundos, independentemente das consultas de saúde, PostgreSQL ou métricas
da aplicação. O coletor antigo deixa de ingerir queue; o novo mantém esse
stream disponível no console sem duas ingestões concorrentes.

A leitura é limitada a 1 MiB por ambiente em cada ciclo. Importações maiores
continuam nos ciclos seguintes e permanecem sinalizadas como pendentes.
O schema é preparado uma vez por identidade do banco no processo do coletor.
A manutenção de retenção roda no máximo uma vez por minuto em situação normal;
o orçamento é verificado antecipadamente com os bytes de novas ingestões,
disparando limpeza imediata quando necessário. O contador em memória somente
avança após o commit. Reinícios reconciliam o total novamente; alterações por
outros processos são reconciliadas na próxima manutenção. A expiração por idade
pode levar até um minuto adicional. O orçamento não limita o arquivo SQLite,
seus índices, WAL, nem os arquivos de log originais.

O gráfico usa cache compartilhado entre leitores deste processo, por até quinze
segundos, com limite de 32 entradas e 4 MiB de conteúdo serializado. Alterações
nos metadados do episódio, inclusive retenção e novos snapshots, mudam a chave.
Leitores concorrentes da mesma chave reutilizam o cálculo. Páginas de snapshots,
seleção e estado do coletor continuam consultados no banco. Episódios pequenos
consultam somente os pontos exatos, sem calcular a agregação descartada.

A tela evita recarregar detalhes de um episódio inalterado durante o
acompanhamento. Atualizar força nova consulta. Falhas aplicam espera progressiva
de 15, 30, 60 e até 120 segundos às tentativas automáticas; a consulta manual
continua disponível. Aba oculta e acompanhamento pausado continuam sem polling.

Comparação local com 120960 snapshots fictícios no mesmo banco, sem tracemalloc:

============================== ========= =========
Operação                       Antes     Depois
============================== ========= =========
Primeiro detalhe               183,33 ms 178,98 ms
Detalhe repetido (mediana)      185,66 ms   7,88 ms
Coleta ociosa (mediana)          99,56 ms  22,41 ms
============================== ========= =========

As duas versões preservaram 302 pontos, o pico de 80 e a recuperação.
O ganho do cache depende de reutilização antes da expiração ou de novos eventos.
O experimento não mede a instrumentação do backend, RSS, volume real de logs,
nem garante esses tempos em produção. Não houve acesso ao servidor operacional.

As fontes são logDir/<repoName>.waitress.queue.log e suas três rotações.
queueDiagnosticsLog no objeto de configuração de cada ambiente substitui o
caminho padrão. O usuário do console precisa ter acesso de leitura.

As opções ficam no objeto monitoring do machine.config.json efetivo do
servidor, no baseDir resolvido pelo console. Mesclar com as opções existentes::

    {
      "monitoring": {
        "congestionEnabled": true,
        "congestionMaxStorageMb": 256
      }
    }

congestionEnabled é booleano e tem padrão true. O orçamento numérico aceita
1 a 4096 MiB, padrão 256. A retenção é sete dias. Essas opções não são
variáveis do .env Django nem alteram o intervalo de emissão do Waitress.

O schema SQLite passa de 2 para 3 mediante migração aditiva e idempotente no
init_store. São criadas tabelas para snapshots, episódios, processos,
checkpoints e estado da coleta, com índices de consulta e retenção. Confirmar
que o ops_store.py efetivamente importado pelo console é o lib atualizado.

Snapshots são deduplicados por seu conteúdo normalizado, preservando event_at
UTC e ingested_at separados. A ingestão da cópia no stream queue aplica uma
lista de campos permitidos e remove query strings. A fonte em disco permanece
inalterada. Métodos, templates de rota e IDs são suficientes para esta visão;
não são coletados corpos, cookies, credenciais ou cabeçalhos.

Episódios e limites
------------------

Os eventos enabled identificam sessões. Sem eles, a sessão é explicitamente
incompleta. Nunca são unidos processos diferentes. Eventos antigos importados
depois dos novos provocam reconstrução da projeção do processo afetado;
retenção normal ajusta apenas os episódios afetados, sem reproduzir uma semana
inteira a cada ciclo.

congestion_start abre um episódio; congestion_ongoing o atualiza; recovered
registra que a fila esvaziou. Sem início, a duração não é confirmada. Sem
recovered, o fim não é inventado, inclusive após reinício ou silêncio.
Eventos que compartilham o mesmo milissegundo usam ordem estável:
enabled, início, continuidade, recuperação.

* Leitura máxima de 4 MiB por ambiente/ciclo; linhas acima de 256 KiB são
  descartadas com contagem de inválidas. Linhas incompletas aguardam conclusão.
* Checkpoints transacionais persistentes acompanham identidade do arquivo,
  offset e prefixo, permitindo retomar e detectar rotação/truncamento.
* Paginação padrão de 100, máximo de 500 registros. Páginas de snapshots têm
  adicionalmente orçamento de 4 MiB de payload; o próximo offset permite
  continuar sem perder registros. Snapshot selecionado e último snapshot são
  retornados separadamente quando necessário.
* Gráficos expõem todos os pontos até 500 snapshots; acima disso, usam cerca
  de 300 intervalos com mínimos/máximos e transições reais preservadas.
  Agregados não são apresentados como um snapshot individual.
* Lacunas superiores a max(15 s, três intervalos esperados de emissão) não
  recebem linhas conectando observações. O intervalo vem de enabled, com
  fallback de 5 s quando essa evidência não está disponível.
* O orçamento lógico considera os payloads normalizados e sua cópia SQLite
  no stream queue. Índices, páginas livres, WAL, outros logs e arquivos do
  Waitress não entram nesse orçamento; o tamanho físico pode ser maior.
  Não há VACUUM bloqueante automático.
* Ao atingir o orçamento, os snapshots mais antigos e suas cópias queue são
  removidos e o descarte é informado. Episódios cortados ficam parciais.
* Após falha da coleta de arquivos, o histórico já persistido continua consultável.
  Se o próprio SQLite estiver indisponível, novas consultas falham explicitamente
  e os dados anteriormente carregados permanecem identificados na interface.
  A verificação sem
  atualização há mais de 20 s é sinalizada. Erros repetidos do coletor geram
  no máximo um registro de erro por minuto.

API e extração
--------------

Os endpoints seguem a autenticação existente do monitoramento:

* GET /api/v1/monitoring/{env}/congestion: window ou since/until com fuso,
  limit/offset; retorna coleta, cobertura, último snapshot e episódios.
* GET /api/v1/monitoring/{env}/congestion-detail: episode_id, limit/offset e
  snapshot_id opcional; retorna episódio, página, seleção e série histórica.

As respostas da tela usam somente SQLite, sem varrer os arquivos no caminho
HTTP e sem consultar o Django. A interface cancela consultas substituídas,
ignora respostas atrasadas e mantém os dados anteriores identificados após
uma falha. Consultas da aba têm limite de espera de dez segundos.

O Extrair log existente inclui uma seção adicional de snapshots estruturados
selecionados pelo horário original. Assim, ingestão atrasada e remoção de um
arquivo não apagam a evidência já retida no SQLite. Essa seção tem limite de
32 MiB e 10000 registros com aviso de corte. Arquivos e rotações continuam
como fontes alternativas explícitas, junto aos eventos e sondagens existentes.

Interpretação
-------------

Fila esvaziada não significa que as execuções terminaram nem que o backend
está saudável. A última observação pode ser antiga: o produtor não emite
heartbeat contínuo fora dos eventos de congestionamento. Arquivo acessível
não comprova processo vivo. Os totais da fila, canais do dispatcher e conexões
não são equivalentes. Flags de parcialidade não são removidas pela interface.

As durações das execuções ainda estavam em andamento no momento do snapshot.
Incluem streaming e esperas de saída dentro do serviço, não a entrega final
ao cliente. Uma rota demorada é evidência de ocupação, não causa raiz comprovada.

Validação e implantação
----------------------

A partir da raiz do repositório, executar::

    python -m pytest ops-console/tests/test_server_congestion.py ops-console/tests/test_availability_event_logs.py ops-console/tests/test_server_monitoring.py ops-console/tests/test_availability_collector.py ops-console/tests/test_availability_view.py ops-console/tests/test_independent_availability_probe.py ops-console/tests/test_server_host.py -q -p no:cacheprovider
    node --test ops-console/tests/congestion.test.mjs ops-console/tests/monitoring-cache.test.mjs ops-console/tests/availability-detail.test.mjs ops-console/tests/availability-export.test.mjs
    python ops-console/tools/benchmark_congestion.py

O benchmark usa banco temporário descartável e 120960 snapshots fictícios,
equivalentes a sete dias com uma observação a cada cinco segundos. Mede
montagem, retenção, resumo, detalhe, alocações Python e tamanho do SQLite.
Não acessa banco operacional nem a aplicação; seus resultados não equivalem
a uma medição de RSS ou do custo completo de ler arquivos reais em produção.

Medição local de referência com esse volume: montagem 17,960 s; resumo com
mediana de 464,89 ms; detalhe com mediana de 382,08 ms; limpeza de retenção
288,59 ms; pico de alocações Python das consultas 1008922 bytes; SQLite
212996096 bytes. A resposta tinha 100 snapshots e 301 pontos de gráfico.
São medições sintéticas desta máquina, com tracemalloc ativo nas consultas,
e precisam ser confirmadas no host operacional com carga representativa.

Os testes cobrem os dois snapshots reais (71 ms), rotação, retomada,
truncamento, duplicação, linhas inválidas/incompletas, falha transacional,
isolamento por ambiente e sessão, eventos órfãos, retenção, orçamento e picos.
O navegador foi validado com os módulos reais de monitoramento/congestionamento
em uma fixture local: seleção de cartões/pontos, exportação, temas claro/escuro,
larguras 1400/768/375/320 e demonstração sem chamadas a APIs reais.

Entregar juntos os arquivos do console e lib/ops_store.py pelo fluxo normal.
Fazer backup consistente do SQLite pela API de backup, ou com o console
parado, antes da atualização; reiniciar apenas o Ops Console
para carregar os módulos novos e invalidar os assets antigos pelo versionamento
incluído. Não há alteração ou reinício do backend por esta funcionalidade.

Antes de MAIN, validar no HOM publicado: acesso ao arquivo, bootstrap do
schema 3, importação, gráficos, cobertura, extração com a API Django indisponível
e cenários fictícios. Esta validação operacional não foi executada no host
publicado durante o desenvolvimento local.

Reversão: definir congestionEnabled=false no arquivo efetivo para interromper
novas ingestões, mantendo consultas do histórico e demonstração. Se necessário,
reverter o release do console e reiniciá-lo. As tabelas novas são aditivas e
podem permanecer; não apagar arquivos originais para reverter a funcionalidade.
