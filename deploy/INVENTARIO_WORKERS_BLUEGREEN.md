# Workers: inventário e condições de migração

Revisão estática em 26/11/2026, aplicação em `b683f027`. Nenhum starter corporativo foi executado para produzir este documento. Caminhos abaixo são relativos ao repositório da aplicação.

| Grupo | Ponto de entrada | Dependências e verificação necessária |
|---|---|---|
| BI/painéis | `backend/apps/common/bi_sync_scheduler.py` | Thread despacha filas; conferir configuração, lease e ausência de duplicação. |
| Robôs/SLA | `backend/apps/automacoes/apps.py` e `automacoes/app/services/automatic_start.py` | Auto-start pode usar credenciais Okta, navegador e sistemas externos. Inventariar os modos habilitados e destinos de teste. |
| Relatórios de falhas | `backend/apps/falhas_criticas/services/report_scheduler.py` | Dispatcher compartilha lock com BI; validar agenda, arquivos e parada durante exportação. |
| Férias | `backend/apps/ferias/renewal_scheduler.py` | Renovação, cancelamentos e expirações: comprovar efeitos únicos e recuperação. |
| Escala/timeout | `backend/apps/escala_flex/services/status_timeout_scheduler.py` | Timeouts, cancelamento de ocorrências e coleta de alertas: validar concorrência. |
| Escala/reset diário | `backend/apps/escala_flex/services/daily_status_reset_scheduler.py` | Validar horário, reinício dentro/fora da janela e idempotência. |
| Escala/intervalos | `backend/apps/escala_flex/services/clear_plantao_intervalos_scheduler.py` | Conferir dados afetados, frequência e execução única. |
| Suporte | `backend/apps/suporte_operacional/services/queue_timeout_scheduler.py` | Thread de timeout: conferir consistência e concorrência das transições. |
| Campanhas/eventos | `backend/apps/halloween/management/commands/run_event_scheduler.py` | Supervisor chama `--once`: validar cadência e efeitos no banco, inclusive falhas. |
| Métricas | `backend/apps/ops_monitoring/apps.py` | Flush por processo pode coexistir; purga periódica deve permanecer única. |

Os guards nos sete `AppConfig.ready()` bloqueiam os starters de negócio nos slots web e preservam os hooks necessários à aplicação. Isso não fornece um contrato de saúde ou drenagem de cada job.

## Persistência

`automacoes/app/config/paths.py` usa `ROBOT_RUNTIME_DIR`, normalmente em AppData, e caminhos OneDrive/SharePoint. Algumas importações criam diretórios. Conferir conta executora, caminhos efetivos e compartilhamento entre supervisor, slots e legado antes do piloto. O harness de build fornece perfil descartável aos subprocessos; não copiar credenciais ou dados corporativos para o laboratório.

## Bloqueio concreto: parada dos robôs

O supervisor chama `get_robot_manager().stop_all()` ao encerrar. Na aplicação revisada, `RobotProcessManager._kill_pid`, em `automacoes/app/services/robot_manager.py`, usa `taskkill /PID ... /T /F` no Windows. **A parada dos robôs pode interromper trabalho em andamento**, mesmo que o arquivo de controle do supervisor seja cooperativo.

Também falta uma interface comum para suspender todos os dispatchers, drenar trabalho e comprovar ausência de jobs órfãos. Não homologar `workers-stop` nem atualização do supervisor em produção com base no teste sintético.

Para fechar o requisito:

1. Definir quiescência por classe de job: impedir novo despacho, concluir/cancelar com segurança, encerrar e confirmar.
2. Conferir identidade completa dos subprocessos e persistência de progresso; um PID isolado não basta.
3. Implementar confirmação de drenagem; timeout deve preservar o executor e impedir substituição concorrente.
4. Ensaiar jobs longos, falha durante parada, queda e atualização com responsáveis de negócio.
5. Verificar SLA e sessões de navegador separadamente do robot manager genérico.

Saída inesperada bloqueia novo `workers-start`; não apagar `background.json` para contornar isso. Identidade perdida antes da gravação do PID pode ser reconciliada quando o processo exato é encontrado. Retomar jobs após queda depende do contrato de negócio acima.

## Cobertura sintética

`tests/bluegreen/test_background.py` usa Django real e jobs que apenas gravam marcadores locais. Cobre web-only, mutex de vida real, starts repetidos, perda de identidade, preservação durante promoção/rollback web, parada, novo start com instance ID diferente, readiness antigo e saída não confirmada.

Essa cobertura valida infraestrutura nos casos exercitados. Não valida modos reais, credenciais, agendas, destinos externos ou efeitos de negócio.
