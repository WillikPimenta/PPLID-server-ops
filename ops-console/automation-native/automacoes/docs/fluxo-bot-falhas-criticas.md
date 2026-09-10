# Fluxo — Bot Falhas Críticas (Power BI + Report)

> **Guia de envio (operacional):** [guia-envio-report-falhas-criticas.md](../../documentation/guia-envio-report-falhas-criticas.md) — passo a passo para Willik (bot, script manual, Outlook).

## O que faz

Modo `falhas_criticas` na tela **Planejamento → Automação**. Pipeline único:

1. Abre o relatório Power BI no Edge (Playwright)
2. Aplica filtros (MTD, FY27, Manual, Crítica, etc.)
3. Exporta a tabela **Falhas**
4. Consolida na aba **Base** de `FALHAS_CRITICAS_MANUAL.xlsx`
5. Opcional: RefreshAll das queries no Excel
6. Gera relatórios HTML e abre prévia no Outlook (BSB, SC, consolidado)

## Configuração

No modal do card:

| Campo | Descrição |
|-------|-----------|
| Executar imediatamente | Roda uma vez ao clicar Iniciar |
| Horário agendado | Se não imediato, aguarda HH:MM diário (padrão 08:00) |
| Planilha Excel | Caminho de `FALHAS_CRITICAS_MANUAL.xlsx` |
| Mês de referência | MM/AAAA do report (vazio = mês atual MTD) |
| Consolidado / Executivo / Outlook | Flags do `report_falhas` |
| Refresh queries | RefreshAll após merge na Base |

### Fechamento mensal (virada do mês)

Na virada do mês, use **`mes_referencia=MM/AAAA`** do mês que está fechando (ex.: `06/2026` em 02/07).

- **Data de Análise** define o mês do protocolo.
- No fechamento de junho, entram análises de **01/06 a 30/06**.
- **Data Auditoria** pode ir até o **5º dia útil de julho** (ex.: **07/07/2026**) sem sair do oficial.
- Análise em julho (ex.: 01/07 ou 07/07) pertence ao report de **julho**, não ao fechamento de junho.
- Com `mes_referencia` vazio em 02/07, o bot exporta MTD de **julho** (01/07 a hoje) — para fechar junho, informe `06/2026` explicitamente.

**Exemplo (jun/2026 rodado em 02/07):**

1. Modal: `mes_referencia = 06/2026`
2. Power BI filtra **01/06/2026 – 30/06/2026**
3. Report aplica carência de auditoria até **07/07/2026** no KPI oficial

**Credenciais Okta:** não exigidas (login Power BI via perfil Edge salvo).

## Paths em runtime

- Perfil Edge: `{ROBOT_CONFIG_DIR}/falhas_criticas/browser-profile`
- Downloads export: `{output_dir ou config}/falhas_criticas/downloads`
- Backups Base: pasta `backups` ao lado da planilha master

## Primeira execução

1. Configure o caminho da planilha no modal
2. Clique **Iniciar** com execução imediata
3. Se aparecer login Microsoft no Edge, o bot tenta **avançar sozinho** (conta salva, Entrar, Continuar, Sim) — intervenção manual só se pedir senha, MFA ou sessão realmente expirada
4. Nas próximas execuções a sessão costuma ser reutilizada pelo perfil Edge persistente

## Arquivos gerados (modal)

Após execução com sucesso, a aba **Arquivos** no modal do bot lista as pastas onde os HTMLs foram gravados (ex.: `...\report-falhas-criticas\brasilia\2026-07-03`). Use **Copiar** ou **Abrir no SharePoint** na sua máquina; o botão **Abrir** executa no servidor onde o bot roda. O Outlook continua abrindo apenas nesse servidor.

## Dependências

```powershell
cd backend
pip install -r requirements.txt
pip install -e ../automacoes
playwright install msedge
```

`backend/requirements.txt` já inclui `playwright`, `pywin32`, `matplotlib`, `pandas` e `openpyxl` (report + bot).

O bot usa **Edge** (`channel=msedge`); não é necessário `playwright install chromium`.

## Variáveis de ambiente (opcional)

- `FALHAS_CRITICAS_EXCEL_PATH` / `REPORT_EXCEL_PATH`
- `FALHAS_CRITICAS_SETTINGS` (JSON)
- `REPORT_FALHAS_BACKEND_PATH` (auto: `../backend` ao iniciar pelo portal)
