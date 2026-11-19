Integração do Extrair log com diagnóstico Waitress
================================================

A instrumentação pertence ao repositório PPLID, em
backend/apps/ops_monitoring/waitress_diagnostics.py, ativada pelo WSGI real.
Não editar o bundle automation-native para publicar esse recurso.

O guia completo de implantação, configuração, testes, limitações e reversão
está em PPLID/backend/docs/waitress_queue_diagnostics.rst. Entregar as duas
mudanças (PPLID e PPLID-server-ops), validar HOM e somente então preparar MAIN.
Não houve publicação ou alteração de capacidade neste trabalho.

O console coleta backend/queue de logDir/<repoName>.waitress.queue.log.
Uma configuração por ambiente queueDiagnosticsLog permite outro caminho
local, que deve coincidir com PPLID_QUEUE_LOG do backend. O usuário do
serviço do console precisa ter permissão de leitura.

O botão existente Extrair log chama availability-event-log e passa a incluir
esse arquivo e as três rotações .1/.2/.3. A coleta funciona sem HTTP para o
backend Django, preservando event_at UTC, environment e pid no conteúdo.
No SQLite, logged_at do stream queue é o instante de ingestão; para os logs
legados o relatório declara que a distinção histórica não está disponível.
A leitura direta dos arquivos filtra pelo horário original, sem reescrever
o conteúdo. Não há migração de banco nem mudança no frontend.

A leitura é limitada a 32 MiB e 10000 linhas por arquivo, com avisos para
limites e fontes ausentes. A instrumentação tem retenção própria e flags de
listas parciais. A base de monitoramento do console continua necessária;
não há promessa de operação com o SQLite do console indisponível.

Validado localmente: 57 testes de extração e monitoramento passaram, incluindo
extração offline, rotações, ambiente/PID, relógios e ingestão incremental.
Também foi gerado um relatório pelo builder real do console sobre o arquivo
da fila HTTP controlada, preservando as rotas pendentes e em execução.
Isso não substitui a validação do botão e do serviço Django no HOM publicado,
que está desabilitado na configuração deste computador.

Comando de teste a partir de PPLID-server-ops::

    python -m pytest ops-console/tests/test_availability_event_logs.py ops-console/tests/test_server_monitoring.py -q -p no:cacheprovider

Fila e execução longa são evidências de saturação, não comprovação de causa
raiz nem explicação suficiente para uma porta posteriormente fechada.
