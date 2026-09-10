# Relatório de validação — Automações Ops Console

## 1. Inventário de configuração

- [PASS] Arquivo machine config existe (machine.config.local.json)
- [PASS] Arquivo env config existe (env.config.local.json)
- [PASS] automationRuntime.root definido — C:\Users\c93123a\PPLID-server-ops\.local\automation-runtime
- [PASS] automationOpsDir contém automacoes/app — C:\Users\c93123a\PPLID-server-ops\ops-console\automation-native
- [PASS] automation-native contém backend — C:\Users\c93123a\PPLID-server-ops\ops-console\automation-native\backend
- [PASS] HOM desativado no env config
- [PASS] backend.env de MAIN alinhado com postgresDb — POSTGRES_DB='pplid_main', esperado 'pplid_main'
- [PASS] backend.env de MAIN sem BOM UTF-8 — remova o BOM (salve como UTF-8 sem BOM) ou regrave pelo Ops Console
- [PASS] backend.env de DEV alinhado com postgresDb — POSTGRES_DB='pplid_dev', esperado 'pplid_dev'
- [PASS] backend.env de DEV sem BOM UTF-8 — remova o BOM (salve como UTF-8 sem BOM) ou regrave pelo Ops Console
- [PASS] backend.env de HOM alinhado com postgresDb — ambiente desativado — verificação ignorada
- [PASS] Seed padrão production definido no control plane — {"headless": false, "executar_imediatamente": false, "tempo_espera_minutos": 60, "dias_download_brflow": 3, "executar_co
- [PASS] Seed padrão rotina definido no control plane — {"headless": false, "executar_imediatamente": false, "rotina_data_inicio": "", "rotina_data_fim": "", "tarefas": [], "ge
- [FAIL] get_config(production) retorna seed portal
- [FAIL] get_config(rotina) retorna seed portal
## 2. Regressão automatizada (pytest)

- [PASS] pytest control plane + orphan + API routes — .....................................                                    [100%]
37 passed in 2.83s
## 3. Runtime e isolamento (estático)

- [PASS] Supervisor aceita --pplid-supervised
- [PASS] Supervisor congela backend.env no start
- [PASS] Supervisor valida expected-database
- [PASS] orphan_bot_manager preserva --pplid-supervised
- [PASS] UI publish/rollback oculta (risco documentado) — oculta — usar API POST /api/v1/automations/runtime/publish
- [PASS] Publish bloqueado com bot running
## 4. Ciclo de vida (estático)

- [PASS] UI exige validação Okta antes de iniciar
- [PASS] Chips multi-select de banco destino
- [PASS] Formulário de configuração visível
- [PASS] Salvar config bloqueado só com bot Ops running
- [PASS] Módulo automations-config.js registrado
- [PASS] Helper de credenciais existe
## 5. Banco e hooks (estático)

- [PASS] Supervisor executa django.setup()
- [PASS] Supervisor usa robot_manager.start
- [PASS] Hook stdout ROTINA_BRUTO_SAVED presente no robot_manager
- [PASS] Hook stdout MONITOR_EVENTOS_SAVED presente no robot_manager
- [PASS] Hook stdout PRODUCTION_DETALHADO_SAVED presente no robot_manager
- [PASS] App Django automacoes presente no bundle nativo — C:\Users\c93123a\PPLID-server-ops\ops-console\automation-native\backend\apps\automacoes
## 6. Config backend

- [PASS] normalize_bot_config implementado
- [PASS] Importação robot_config.json
- [PASS] update_config bloqueia bot Ops running
- [PASS] targetEnvironments persistido (1-2 bancos)
- [PASS] probe_target_availability exposto no overview

**Resumo:** 37 passou, 2 falhou, 39 total
