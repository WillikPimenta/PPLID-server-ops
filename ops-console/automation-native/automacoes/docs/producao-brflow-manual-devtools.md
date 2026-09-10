# Produção BRFlow — fluxo manual (Python)

Kit em **`tools/producao-brflow-kit/`** — dois arquivos para compartilhar.

## Uso rapido

### 1. Baixar CSVs (DevTools)

1. BRFlow → **Produtividade**
2. DevTools → **Sources** → **Snippets**
3. Cole [`tools/producao-brflow-kit/brflow_baixar_3dias.js`](../tools/producao-brflow-kit/brflow_baixar_3dias.js)
4. Execute (`Ctrl+Enter`)

### 2. Tratar CSVs (Python)

```powershell
cd automacoes
python tools/producao-brflow-kit/tratar_producao.py
```

Le os 3 CSVs mais recentes em Downloads, gera XLSX detalhado e remove os brutos (use `--manter-csv` para conservar).

## Saida

```
relatorio_produtividade_detalhado_YYYY-MM-DD.xlsx
```

## Compartilhar

Envie a pasta `producao-brflow-kit` inteira. Detalhes em [`tools/producao-brflow-kit/README.md`](../tools/producao-brflow-kit/README.md).

## Legado

| Arquivo | Status |
|---------|--------|
| [`brflow_producao_panel.js`](../tools/snippets/brflow_producao_panel.js) | Painel JS 100% no navegador (alternativa sem Python) |
| [`tratar_producao_brflow.py`](../tools/tratar_producao_brflow.py) | Redireciona para o kit |
| [`producao_panel_server.py`](../tools/producao_panel_server.py) | Servidor HTTP legado |
