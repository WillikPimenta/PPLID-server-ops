# Documentação PPLIDBOTS

## Robôs e automação

| Documento | Descrição |
|-----------|-----------|
| [replicacao-aud-d1-meta-mensal-e-retencao-planos.md](replicacao-aud-d1-meta-mensal-e-retencao-planos.md) | Bot **Replicação de Auditoria** (`replicacao_auditoria_d1`): planejamento D-1, meta mensal, APIs e retenção de planos |
| [fluxo-bot-rotina.md](fluxo-bot-rotina.md) | Fluxo completo da **rotina diária** (`rotina`), tarefas, tratamentos e conferência de replicados |

## Organização na interface web

- **Grade principal:** robôs em uso no dia a dia, incluindo `replicacao_auditoria_d1` (*Replicação de Auditoria*).
- **Seção Legados (colapsável):** `replicacao_auditoria` (*Replicação de Auditoria (legado)*), pasta `brflow-auditoria-replic/`.

A rotina diária consome relatórios e grava conferências em `brflow-auditoria-replic-d1/`, alinhada ao bot principal.

Constantes de modos: [`app/config/constants.py`](../app/config/constants.py).
