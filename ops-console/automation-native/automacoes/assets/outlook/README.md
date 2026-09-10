# Templates Outlook (Falhas Críticas)

O fluxo padrão pede ao usuário clicar manualmente em **Reenviar mensagem**; o bot cola **Para/Cc** do `.txt` e aguarda OK antes do próximo e-mail.

**Reenviar:** feito manualmente pelo usuário (diálogo modal no Windows). Em seguida o bot:

1. Cola destinatários no **Para** (`Alt+T`) e **Cc** (`Alt+C`) quando configurados
2. Aguarda revisão — anexe os arquivos manualmente, se necessário

O sidecar `email_falhas_{escopo}_destinatarios.txt` inclui:

```
Para: email1@...; email2@...
Cc: cc1@...
```

Os relatórios (XLSX/HTML) ficam na pasta de saída do escopo para anexo manual.
