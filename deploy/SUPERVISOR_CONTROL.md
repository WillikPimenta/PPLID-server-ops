# Controle do executor independente

O contrato `supervisorControl: account-json-v1` complementa o protocolo de
drenagem, sem alterá-lo. As duas releases web e o supervisor devem possuir esse
contrato e a mesma cópia normalizada de `supervisor_control.py`. A preparação
recusa releases antigas, arquivo ausente, symlink e implementação diferente.

O supervisor publica `supervisor-control.json` no runtime do ambiente, com
geração, PID, instante de criação, porta efêmera de loopback e chave aleatória
protegida por DPAPI CurrentUser. A chave e todas as mensagens são protegidas
para a conta Windows atual; não usar `CRYPTPROTECT_LOCAL_MACHINE`. Outra conta
não consegue decifrar as mensagens ou a chave, mesmo se puder ler o descritor.
O runtime deve continuar restrito à conta operacional e administradores.
Não há pickle, invocação livre de atributos ou transporte de objetos Django.

Slots supervisionados usam `PPLID_SUPERVISOR_CONTROL_FILE`,
`PPLID_ENVIRONMENT`, `PPLID_WORKER_CONTROL_DIR` e `PPLID_WEB_ONLY=1`.
Nesse modo, ausência de descoberta falha fechada; não cria um singleton local.
Fora desse modo, as fábricas legadas permanecem locais. O snapshot de ambiente
de cada processo inclui os valores para resistir à releitura de `.env`.

As APIs HTTP mantêm autenticação, CSRF e permissões existentes. O canal
transporta somente os métodos explicitamente permitidos de robot manager,
poller SLA e bridge SLA. Start SLA com validação Okta ocorre inteiro no
executor. A auditoria de configuração reidrata o usuário pelo ID no executor.

Cada chamada valida ambiente, geração e identidade PID/criação/conta antes
de conectar e identidade/geração após a resposta. O supervisor verifica a
identidade do destinatário em cada mensagem. Limites: prazo total de três
segundos no cliente, frame de 1 MiB e dezesseis conexões simultâneas. Comandos
são serializados: concorrência retorna `busy`, não aguarda indefinidamente.
Drenagem recusa mutações; consultas de estado continuam permitidas enquanto o
canal estiver aberto. Shutdown fecha admissão e aguarda comandos síncronos já
aceitos antes da confirmação de término. A drenagem canônica continua
responsável por ciclos e descendentes em andamento.

Para retry de HTTP, o cliente deve criar `X-Pplid-Command-Id` (ASCII, até 80
caracteres) antes da primeira tentativa e preservá-lo, junto com
`X-Pplid-Executor-Generation` obtido do estado. Ambos voltam nos headers da
resposta. O ID é isolado por usuário autenticado, método e caminho HTTP.
Identidade repetida com payload diferente retorna conflito. A mesma identidade
e payload devolvem a resposta original, sem executar novamente, inclusive
após timeout do cliente. O transporte nunca repete mutação automaticamente.

`timeout_outcome_unknown` ou `unavailable_outcome_unknown` significam que o
comando pode ter sido aceito. Consultar estado ou repetir apenas com o mesmo
ID e a mesma geração. A API devolve 409 para ocupado/conflito e 503 para
indisponibilidade, drenagem ou geração inválida. Erros incluem código visível.
Não repetir um comando de resultado incerto com outro ID ou outra geração.
O ledger vive apenas na geração e não promete exatamente uma vez após queda.

Ao salvar configuração SLA, a persistência precede a aplicação no poller.
Se essa segunda etapa falhar no canal, a API informa
`configuration_saved: true`, `executor_apply: pending_or_unknown` e
`retry: same_command_id_and_generation`. O operador consulta configuração
desejada e estado efetivo para reconciliar a diferença. O erro não devolve
configurações ou credenciais. Não há retry automático com uma identidade nova.

O ledger conserva somente hash do payload e resposta DPAPI em memória;
não escreve comandos, senhas ou respostas em disco/log. Ao atingir 10.000
mutações por geração, novas identidades falham com `command_ledger_full`;
IDs conhecidos e consultas continuam atendidos. Planejar drenagem/reinício
controlado e reconexão das sessões em memória. Esse limite deliberado evita
expulsar IDs antigos e executar novamente um retry tardio.

Validação: `python -m unittest apps.automacoes.tests.test_supervisor_control -v`
no backend (Windows, dependências Django da release e SQLite sintético) e
`python -m pytest tests/bluegreen/test_background.py tests/bluegreen/test_worker_drain.py -q`
em operações. Os testes usam processos e workers sintéticos, sem navegador,
credenciais reais, banco corporativo ou trabalho externo.
