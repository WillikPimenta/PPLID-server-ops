# Teste - Tratamento de Erros em Extração de Produtividade

## Resumo da Alteração

Modificada a função `_executar_extracao()` em `app/bots/bot_production.py` para permitir que o robot **finalize com sucesso usando o dados que conseguiu baixar**, mesmo que um dos sistemas (BRFlow ou Confer) falhe.

Além disso, adicionada funcionalidade de **logs de execução** salvos em `DEFAULT_SHAREPOINT_PRODUCAO / "Log" / execucao.log` com:
- Resultado de sucesso/falha de cada sistema
- Mensagens de erro (se houver)
- Resultado geral da execução
- **Sem histórico** - arquivo é sobrescrito a cada execução

### Comportamento Antes
```
Se Confer falha → RuntimeError (parada completa)
Se BRFlow falha → RuntimeError (parada completa)
```

### Comportamento Depois
```
Se Confer falha, BRFlow OK → ✓ Continua com dados de BRFlow + LOG salvo
Se BRFlow falha, Confer OK → ✓ Continua com dados de Confer + LOG salvo
Se AMBOS falham → ❌ RuntimeError (parada completa) + LOG de falha
```

## Alterações Específicas

### 1. Nova Função: `_salvar_log_execucao()`
Salva arquivo de log em `DEFAULT_SHAREPOINT_PRODUCAO / "Log" / execucao.log` com:
- Timestamp da execução
- Data de processamento
- Status de Confer (✓ SUCESSO ou ❌ FALHA)
- Status de BRFlow (✓ SUCESSO ou ❌ FALHA)
- Mensagens de erro (primeiros 200 caracteres de cada)
- Resultado final (AMBOS COM SUCESSO / SUCESSO PARCIAL / FALHA COMPLETA)

Exemplo de arquivo gerado:
```
Timestamp: 2026-05-21 14:35:22
Data de Processamento: 21/05/2026 14:35:10

=== STATUS DOS SISTEMAS ===
Confer: ✓ SUCESSO
BRFlow: ❌ FALHA

Erro BRFlow: Connection timeout ao acessar BRFlow após 120 segundos

RESULTADO FINAL: ⚠ SUCESSO PARCIAL (um sistema falhou)
```

### 2. Chamada em Execução Bem-Sucedida
Função `_executar_extracao()` agora chama `_salvar_log_execucao()` ao final com:
- Status sucesso de ambos os sistemas ou parcial
- Erros capturados

### 3. Chamada em Falha Completa
Função `_executar_ciclo_producao()` também chama `_salvar_log_execucao()` nos blocos de erro para registrar:
- Falha em ambos os sistemas
- Motivo da falha

## Como Testar

### Teste 1: Simular Falha de Confer (Sucesso Parcial)
Modifique temporariamente a função `_worker_confer()` para lançar exceção:
```python
def _worker_confer(...):
    raise Exception("TESTE: Falha simulada em Confer")
    # resto do código...
```

**Resultado esperado:**
- Robot continua e baixa dados de BRFlow
- Arquivo criado em: `DEFAULT_SHAREPOINT_PRODUCAO / "Log" / execucao.log`
- Conteúdo:
  ```
  Confer: ❌ FALHA
  BRFlow: ✓ SUCESSO
  Erro Confer: TESTE: Falha simulada em Confer
  RESULTADO FINAL: ⚠ SUCESSO PARCIAL (um sistema falhou)
  ```

### Teste 2: Simular Falha de BRFlow (Sucesso Parcial)
Modifique temporariamente a função `_worker_brflow()` para lançar exceção:
```python
def _worker_brflow(...):
    raise Exception("TESTE: Falha simulada em BRFlow")
    # resto do código...
```

**Resultado esperado:**
- Robot continua e baixa dados de Confer
- Arquivo criado/sobrescrito em: `DEFAULT_SHAREPOINT_PRODUCAO / "Log" / execucao.log`
- Conteúdo:
  ```
  Confer: ✓ SUCESSO
  BRFlow: ❌ FALHA
  Erro BRFlow: TESTE: Falha simulada em BRFlow
  RESULTADO FINAL: ⚠ SUCESSO PARCIAL (um sistema falhou)
  ```

### Teste 3: Ambos Sistemas Falhando (Falha Completa)
Faça ambas as funções lançarem exceção.

**Resultado esperado:**
- RuntimeError é lançado
- Arquivo criado/sobrescrito em: `DEFAULT_SHAREPOINT_PRODUCAO / "Log" / execucao.log`
- Conteúdo:
  ```
  Confer: ❌ FALHA
  BRFlow: ❌ FALHA
  Erro Confer: [mensagem]
  Erro BRFlow: [mensagem]
  RESULTADO FINAL: ❌ FALHA EM AMBOS OS SISTEMAS
  ```

### Teste 4: Execução Completa com Sucesso
Execute normalmente sem modificações.

**Resultado esperado:**
- Robot executa normalmente
- Arquivo criado/sobrescrito em: `DEFAULT_SHAREPOINT_PRODUCAO / "Log" / execucao.log`
- Conteúdo:
  ```
  Confer: ✓ SUCESSO
  BRFlow: ✓ SUCESSO
  RESULTADO FINAL: ✓ AMBOS OS SISTEMAS COM SUCESSO
  ```

## Locais de Log

### Log de Execução (Resultado)
- **Local:** `DEFAULT_SHAREPOINT_PRODUCAO / "Log" / execucao.log`
- **Frequência:** Sobrescrito a cada execução (sem histórico)
- **Conteúdo:** Resultado sucesso/erro de cada sistema

### Log de Application (Histórico)
- **Local:** Vários arquivos em `Config.LOG_DIR`
- **Frequência:** Acumula histórico
- **Conteúdo:** Todos os logs detalhados da execução

## Impacto em Outros Componentes

✓ **CSV combinado:** Pode conter dados de um ou ambos sistemas  
✓ **Processamento:** `_processar_e_salvar_csv()` funciona com dados parciais  
✓ **Data de processamento:** Usa fallback caso BRFlow falhe  
✓ **Limpeza temporária:** Continua normal  
✓ **Log de execução:** Sempre salvo (sucesso ou erro)

## Rollback (caso necessário)

Para reverter para comportamento anterior (falha se um sistema falha):
1. Remove try/except do loop em `_executar_extracao()`
2. Restaura verificação original: `if not confer_result or not brflow_result: raise`
3. Remove código de fallback de DataFrames vazios
4. Remove função `_salvar_log_execucao()`
5. Remove chamadas a `_salvar_log_execucao()`

---

**Data:** 21/05/2026  
**Arquivo:** `app/bots/bot_production.py`  
**Funções:** `_executar_extracao()`, `_executar_ciclo_producao()`, `_salvar_log_execucao()` (nova)
