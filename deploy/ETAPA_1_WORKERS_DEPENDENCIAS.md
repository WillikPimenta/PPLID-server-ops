# Etapa 1 — workers, dependências e release candidata

Execução em 26/11/2026. Escopo solicitado: concluir o mecanismo de pausa/drenagem/confirmação de parada, corrigir os alertas das dependências de produção e gerar uma release com versões fixadas. A ativação no servidor permanece uma etapa posterior.

## Entrega para revisão

- Aplicação: branch `feat/bluegreen-web-only-20261126`, commit `6ab3f7479a2daceaf9d0abd5486811e99e2e3bec`, worktree `.local/bluegreen-review-app`.
- Operações: branch `feat/bluegreen-deploy-20261126`, commits de implementação `f196754` e `12cb2b2`, worktree `.local/bluegreen-review-ops`; a documentação é registrada em commit posterior na mesma branch.
- O checkout original da aplicação e suas alterações de negócio foram preservados. Não houve push, merge, promoção, mudança de tarefa agendada ou ação em banco/serviço corporativo.

## Protocolo de drenagem

`PPLID_WORKER_CONTROL_DIR` habilita o contrato supervisionado. A execução legada sem essa variável mantém sua semântica. A biblioteca canônica fica em `deploy/bluegreen/worker_protocol.py`; a aplicação incorpora uma cópia em `automacoes/app/core/worker_drain.py`. O orquestrador verifica o manifesto de contrato e a igualdade do conteúdo, normalizando CRLF, antes de aceitar a release.

1. Cada ciclo registra sua admissão sob lock entre processos. Fechar a admissão impede novo despacho, sem acionar os eventos de cancelamento de trabalho atual.
2. Schedulers deixam a espera ociosa e terminam os ciclos já iniciados. Filas persistidas permanecem pendentes. O monitor Excel espera os trabalhos e a limpeza dos navegadores atuais; arquivos de entrada permanecem disponíveis para a próxima varredura.
3. Subprocessos Windows são criados suspensos e associados a um Job Object antes de executar código. O registro de lançamento é durável e anterior à criação; o objeto contabiliza também descendentes cujo pai já terminou.
4. Um token de admissão de uso único, vinculado ao processo suspenso, preserva trabalho aceito quando a drenagem acontece antes da inicialização do interpretador filho.
5. O Job Object não configura encerramento ao fechar o handle nem limites de tempo/memória. Queda do dono preserva os filhos. Somente uma falha comprovadamente anterior à retomada permite abortar o processo recém-criado e ainda suspenso.
6. Um timeout mantém supervisor e registros em `draining`. Repetir a parada pode concluí-la depois. Start não substitui supervisor em drenagem, mesmo se uma interrupção impediu a criação do marcador de parada.
7. A confirmação exige ausência de unidades e descendentes, estado global `stopped` e recibo com SHA/instância correspondentes. PID morto, registro corrompido ou identidade incerta não equivalem a parada confirmada. Registros órfãos bloqueiam substituição e exigem investigação explícita.

A confirmação de uma geração já drenada é idempotente: uma falha temporária na gravação do recibo não prende o supervisor após a recuperação da escrita. O teste usa uma obstrução local no destino do recibo, confirma a preservação do processo e remove a obstrução para verificar conclusão sem intervenção nos jobs.

`background_worker.py` deixou de chamar `stop_all()` no encerramento de deploy. A parada explícita legada por botão continua tendo seu contrato próprio. No modo supervisionado, a preparação de um robô também recusa tomar à força um processo preexistente do mesmo modo.

O primeiro start exige que todos os slots registrados contenham o mesmo protocolo compatível. Depois que há registro de supervisor, releases web incompatíveis são recusadas, inclusive em rollback; um registro de supervisor legado exige migração explícita. Preparar duas releases compatíveis antes da transferência de workers. Os SHAs antigos usados no ensaio anterior de tráfego não são, por si só, candidatos válidos para esse novo contrato.

O papel do supervisor é vinculado ao PID: filhos que inicializam Django não iniciam schedulers duplicados. A retirada de um slot fecha apenas o grupo dessa instância web, espera suas requisições e seus trabalhos aceitos e preserva o supervisor independente e o outro slot. Há exclusividade por modo de robô, lane de sincronização, slot de preparação e serviço SLA.

Referências das APIs nativas: [Microsoft — Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), [AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject) e [ResumeThread](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread). Os testes nativos desta etapa rodaram com conta sem administrador; a conta/tarefa do servidor ainda precisa passar pelo mesmo ensaio.

## Dependências

- Vue fixado em `3.5.46` e Axios em `1.21.1`, com atualização das dependências transitivas e lockfile.
- SheetJS Community Edition `0.20.3` distribuído em `frontend/vendor/xlsx-0.20.3.tgz`, origem oficial e licença Apache-2.0 no arquivo. SHA-256: `8dc73fc3b00203e72d176e85b50938627c7b086e607c682e8d3c22c02bb99fe8`. O lockfile registra sua integridade. A [documentação oficial do SheetJS](https://docs.sheetjs.com/docs/getting-started/installation/nodejs/) identifica o CDN como fonte da distribuição atual e descreve o uso de pacote local versionado.
- `backend/requirements.deploy.constraints.txt` fixa 84 distribuições Python resolvidas no build anterior. O pipeline aplica esse arquivo às instalações do backend e de `automacoes`; a alteração também invalida o cache de dependências. São versões fixadas, sem lock completo de hashes dos pacotes Python.
- `npm audit --omit=dev --json`: **zero vulnerabilidades reportadas** para a árvore de produção atualizada. Isso não substitui análise de segurança de código ou entradas do negócio.
- A auditoria incluindo desenvolvimento ainda reporta **sete entradas: cinco moderadas e duas altas** na cadeia de ferramentas. A correção automática sugerida envolve mudança major de `vue-tsc`; não foi aplicada com `--force`. A pendência está registrada em [dependências](PENDENCIAS_DEPENDENCIAS_BLUEGREEN.md).

## Verificação executada

Artefatos locais desta etapa: `.local/bluegreen-step1-20261126`. Não somar reexecuções como novos casos.

| Verificação | Resultado |
|---|---|
| Infraestrutura, conjunto consolidado desde a validação anterior | 100 casos distintos aprovados, pela união de identificadores dos XMLs. |
| Protocolo após a correção da corrida de admissão do filho | 12 aprovados; inclui queda do dono, neto vivo após saída do pai e trabalho aceito antes da drenagem. |
| Protocolo e supervisor/retirada web na revisão final | 17 aprovados em 89,57 s; inclui falha temporária na gravação do recibo e recuperação sem marcador. |
| Entry points reais de reset, purga, SLA e fila com callbacks controlados | 4 aprovados, sem criação de banco nem trabalho externo. |
| Regressão selecionada de robôs | 30 aprovados. |
| Compatibilidade SheetJS | 4 aprovados: XLSX, XLS, CSV e preservação de datas/números/fórmulas/nomes de planilhas. |
| Frontend Vitest antes e depois | Mesmos 47 casos falhando; comparação nominal sem novas falhas. A execução completa registrou 1.318 aprovações em cada árvore; os quatro novos casos SheetJS passaram na execução direcionada. |
| Assert/tsx antes e depois | 7 dos 32 arquivos falham nas duas árvores; as falhas preexistentes incluem configuração de ambiente do runner e paridade de menus/rotas. |
| Build do frontend atualizado | `npm run build:deploy` aprovado. |

A regressão ampliada de operações teve inicialmente 96 aprovações e uma falha de bind `WinError 10013` antes da injeção de cancelamento: uma porta reservada pelo harness podia ser reutilizada como origem de conexões de saída. Os seletores de portas de laboratório passaram a escolher portas testadas na faixa 12000–31999, fora da faixa dinâmica local 49152–65535. O cancelamento e os casos afetados passaram em nova execução direcionada. A configuração de portas de produção não foi modificada.

A comparação assert/tsx inicialmente exportou somente o frontend; faltava um arquivo backend lido por um teste de paridade. A comparação válida foi repetida com o backend da mesma revisão exportado. Logs anteriores foram preservados. Nenhuma dessas falhas foi escondida declarando a suíte inteira da aplicação aprovada.

Evidências principais: `ops-regression.xml`, `ops-targeted-final.xml`, `protocol-final.xml`, `lifecycle-handoff-final.xml`, `background-confirm.xml`, `receipt-retry-final.xml`, `lifecycle-final-17.xml`, `backend-worker-tests.log`, `robot-regression.xml`, `frontend-comparison.json`, `assert-comparison.json`, `baseline-vitest.json`, `updated-vitest-failures.json`, `baseline-assert-tests-complete.log`, `frontend-assert-tests.log`, `frontend-build.log` e `npm-audit-*-after.json`.

## Build limpo da candidata

**Aprovado:** o pipeline real `deploy/bluegreen_prepare.ps1` construiu e validou o commit final `6ab3f7479a2daceaf9d0abd5486811e99e2e3bec`, em `.local/bluegreen-readiness/build-ca04e41c`, com venv nova, perfil temporário, PostgreSQL descartável, constraints versionadas e pacote SheetJS local. Build e `pip check` retornaram zero; as 84 versões fixadas foram conferidas contra o ambiente instalado. O Django passou no system check e o Vite compilou a candidata. Nenhuma migração foi aplicada durante a preparação.

O build anterior `5e81e47c`, com as mesmas definições de dependências, também passou; a candidata final acrescenta a confirmação idempotente do recibo. A inspeção de encerramento encontrou zero processos de laboratório restantes e PostgreSQL encerrado. A conta executora não era administradora.

Resultados: `report-6ab3f747.json`, `pip-freeze-6ab3f747.txt`, `pip-check-6ab3f747.txt` e `artifact-manifest-6ab3f747.json` no laboratório. O manifesto registra 349 arquivos com SHA-256 e as 84 versões conferidas. O consolidado desta etapa fica em `.local/bluegreen-step1-20261126/step1-summary.json`, com `account-proof.json` e `cleanup-proof.json` ao lado. Os artefatos são locais, sem publicação; o pipeline de preparação não promove a release nem inicia os workers de negócio.

Os artefatos do laboratório usam configurações sintéticas, inclusive a URL de API incorporada no frontend. Para o piloto, construir o mesmo SHA aprovado com a configuração autoritativa do servidor e repetir as verificações afetadas; não copiar o `.env` nem promover diretamente o `dist` do laboratório.

Para reproduzir a preparação em outro laboratório descartável desta máquina, com as branches de revisão presentes e a aplicação limpa:

```powershell
python -m deploy.bluegreen.readiness_build --source .local/bluegreen-review-app --pg-bin "C:/Program Files/PostgreSQL/14/bin"
python -m pytest tests/bluegreen/test_worker_drain.py tests/bluegreen/test_background.py -q
```

## Condições para avançar

A implementação local da etapa 1 não equivale à liberação para produção. Permanecem a homologação funcional e operacional com dados/destinos controlados, a matriz de jobs longos e reinício, a integração dos comandos/estado do painel com o supervisor independente (inclusive SLA), a carga representativa de 150 usuários e a validação da conta/tarefa/portas no servidor `C:\PPLID`.

Antes de ativar, revisar as falhas preexistentes da aplicação e as dependências de desenvolvimento; registrar a decisão sobre risco residual. Ensaiar retorno ao legado, limites de upload e tempo máximo operacional aceitável. MAIN já foi autorizado pelo usuário; não houve solicitação de nova autorização nesta etapa.
