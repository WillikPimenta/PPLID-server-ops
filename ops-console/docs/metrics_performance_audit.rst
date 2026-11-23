Revisao de custo das metricas operacionais
========================================

Base e escopo
-------------

Revisao de 23/11/2026. Backend PPLID baseado em origin/main cd9fdbc7
(referencia de producao solicitada). Console baseado na master a0559d5.
Os arquivos pendentes no checkout original do PPLID foram preservados.
As alteracoes estao na branch perf/ops-metrics-audit-20261123 dos dois
repositorios. O checkout isolado do backend e .worktrees/pplid-metrics.

Foram revisadas as cinco rotas ops-metrics, middleware, buffers, worker de
gravacao, retencao, diagnostico Waitress, chamadas HTTP do Console, fallback
PostgreSQL e persistencia SQLite de API, PostgreSQL, disponibilidade e logs.
A rotina do host ja usava gravacao em lote. Esta revisao nao reimplementa as
consultas de negocio do dashboard de qualidade, escala ou produtividade.

APIs do backend
---------------

* summary: deixa de forcar flush. Reutiliza o agrupamento por minuto para os
  totais de trafego e os conjuntos ja lidos para usuarios unicos, eliminando
  duas consultas redundantes. Resposta compartilhada por cinco segundos.
* around: resposta compartilhada, mantendo o limite de 50 amostras e raio de
  ate uma hora; correcao da conversao de datas sem fuso no Django atual.
* route-samples: deixa de forcar flush e de consultar um agente por usuario.
  Respeita since/until enviados pelo Console e limita o intervalo a sete dias.
* recent-samples: mesmas remocoes de flush e N+1. O feed do Console envia
  includeCount=0: busca limit+1 linhas e devolve hasMore/returnedCount, sem
  COUNT de todo o intervalo. Nesse modo sampleCount e null e
  sampleCountExact e false. O comportamento padrao ainda inclui total exato.
* in-flight: snapshot em memoria; nao consulta o banco para resolver nomes.

O indice de nomes usa consultas em lote e e renovado no maximo a cada cinco
minutos, tambem pelo worker de flush. Antes de aquecer, in-flight pode mostrar
Usuario desconhecido; alteracoes de nomes podem levar cinco minutos.
Os campos de usuario sao os mesmos usados anteriormente, inclusive nomes do
cadastro FalhasAgent e identificadores UUID convertidos para chave metrica.

As quatro leituras historicas tem cache local ao processo limitado a 64
entradas/4 MiB. Requisicoes iguais compartilham o trabalho; uma consulta que
nao libera a entrada em 250 ms permite responder 503, sem nova carga no banco.
A restricao a localhost e verificada antes de servir qualquer cache.
No PostgreSQL cada consulta tem statement_timeout de 2 s e lock_timeout de
250 ms, locais a transacao; isso nao e um limite total do endpoint nem do
estabelecimento da conexao. Falhas nao sao armazenadas como respostas validas.

Gravacoes no backend
--------------------

* O middleware apenas enfileira dados; atingir o limiar acorda o worker e nao
  executa SQL na thread que atende a requisicao de negocio.
* Parametros sao capturados apenas para amostras selecionadas e fora de
  backpressure. Uploads maiores que 4 KiB nao sao lidos para telemetria.
  A redacao tem limites de profundidade/tamanho e inclui a query string.
  Requisicoes normais usam sorteio de 10%, evitando a captura de rajadas
  inteiras pelo antigo modulo do relogio. Erros e lentas seguem prioritarios.
* Buffers limitados: 200 amostras, 500 chaves de trafego e 2000 presencas de
  usuario. Descartes e degradacao sao expostos pelo estado de backpressure.
* Um gravador por processo. Upserts de trafego usam lotes de 50 linhas e
  preservam somas, maximos e classes HTTP. Chaves sao ordenadas para reduzir
  inversoes de locks entre processos. Falhas revertem a transacao e restauram
  os dados nos buffers limitados; o worker faz nova tentativa posteriormente.
* O worker fecha conexoes antigas. A retencao Django remove ate 1000 IDs por
  transacao, com pausa curta e limites de SQL/lock, em lugar de um DELETE total.

Sob sobrecarga, a telemetria pode descartar dados para proteger o atendimento.
Nao ha promessa de contagem exata durante descarte. Buffers continuam em
memoria e nao garantem durabilidade em encerramento abrupto do processo.

Console e SQLite
----------------

* Leituras HTTP iguais reutilizam resultados por cinco segundos; in-flight
  usa um segundo. Erros e timeouts aplicam cooldown de 30 segundos. Desse modo
  varios paineis nao repetem chamadas cujo timeout nao cancelou o trabalho
  que ja esta rodando no Waitress. Caches tem limite de 64 entradas/4 MiB.
* O fallback legado usa somente colunas existentes na main, sem tentar uma
  consulta que falha por error_reason inexistente. Aplica limites de SQL/lock
  e reutiliza nomes de usuario por cinco minutos, separados por banco/usuario.
* Consultar pico de usuarios nao inicializa schema nem grava amostras. Apenas
  o coletor periodico persiste a metrica; a resposta considera o valor atual
  ao apresentar o pico conhecido.
* Metricas de API e PostgreSQL usam uma transacao por coleta. Logs de servico
  usam uma transacao por trecho de arquivo, limitado a 256000 bytes/ciclo.
  Linhas parciais ficam para o proximo ciclo. Falha no lote preserva o offset
  para nova tentativa; uma linha individual maior que o limite e descartada.
* Inicializacao de schema e compartilhada por identidade do banco, em vez
  de reexecutar DDL em cada ciclo de disponibilidade/monitoramento.
* Retencao SQLite usa lotes de 1000 linhas, cutoff em ISO UTC e indice por
  recorded_at/id para amostras. O indice e criado na inicializacao normal;
  um banco grande pode ter custo adicional no primeiro inicio desta versao.
* Novos lotes de logs aguardam ate 250 ms por lock SQLite; lotes de amostras,
  ate 1 s. Conexoes desses lotes sao fechadas explicitamente.

O flush pode acrescentar cerca de cinco segundos ate a persistencia. Os dois
níveis de cache historico podem acrescentar ate dez segundos em conjunto.
Isso e atraso de observacao; os horarios originais das amostras sao mantidos.

Validacao
---------

Resultado final local: 29 testes do backend em PostgreSQL 14 descartavel e
122 testes do Console aprovados. O PostgreSQL de teste foi encerrado ao final.

Testes de custo verificam propriedades, em vez de impor tempos de benchmark:

* in-flight frio: zero SQL, inclusive sem aquecer nomes.
* indice com 40 usuarios: duas consultas em lote; leitura seguinte, zero SQL.
  O codigo anterior executava uma consulta de usuarios e uma por agente.
* leituras historicas repetidas: zero SQL no cache quente e nenhum flush.
* feed includeCount=0: nenhuma consulta COUNT e paginacao explicita.
* 120 rotas: tres upserts de lote, preservando totais apos nova gravacao.
* bloqueio real em PostgreSQL: timeout curto, rollback, lote preservado e
  sucesso apos liberar a linha. Consulta pg_sleep verifica timeout e posterior
  recuperacao da conexao. O banco usado e descartavel, separado da producao.
* log parcial, falha de lote, concorrencia HTTP, cooldown, limites de cache,
  inicializacao unica e retencao/indexacao SQLite tem regressao automatizada.

Backend, a partir de backend/::

    python manage.py test apps.ops_monitoring --settings=config.settings_panel_tests --noinput

Para PostgreSQL, usar config.settings_panel_postgres_tests, credenciais de um
servidor descartavel e PANEL_TEST_DATABASE_NAME com prefixo test_panel_.
Nunca apontar esse comando para credenciais ou banco de producao.

Console, a partir da raiz::

    python -m pytest ops-console/tests/test_monitoring_cost.py ops-console/tests/test_server_monitoring.py ops-console/tests/test_availability_event_logs.py ops-console/tests/test_server_congestion.py ops-console/tests/test_server_host.py ops-console/tests/test_availability_collector.py ops-console/tests/test_health_probe.py ops-console/tests/test_independent_availability_probe.py -q -p no:cacheprovider

Implantacao e limites da conclusao
---------------------------------

Publicar primeiro o backend PPLID e depois o Console. O backend antigo ignora
includeCount e continua funcionando, mas mantem o trabalho antigo. O novo
backend conserva a resposta padrao para consumidores que precisam do total.
Nao ha migracao PostgreSQL nem mudanca de quantidade de threads Waitress.

A revisao nao mediu o servidor operacional, CPU, plano de execucao com os
dados reais ou p95 em horario de pico. Os testes nao demonstram que os 55 s
da imagem foram causados somente pelo monitoramento. Depois de implantar,
comparar duracao dessas rotas, filas/threads, SQL ativo e descartes de metrica
no mesmo periodo de carga. O dashboard de qualidade de 91 s permanece uma
investigacao de negocio separada, se continuar lento.
