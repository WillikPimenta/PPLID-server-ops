# Dependências observadas na validação de produção

Data: 26/11/2026. Aplicação limpa em `b683f027`, com `npm ci` e lockfile versionado. Este documento registra o resultado automatizado, não uma avaliação de explorabilidade de cada advisory.

O build completo informou 17 vulnerabilidades considerando também dependências de desenvolvimento. A consulta adicional `npm audit --omit=dev --json` reportou seis entradas de severidade alta em dependências de produção:

| Pacote | Direto | Correção indicada pelo npm |
|---|---|---|
| `@vue/server-renderer` | Não | Disponível |
| `axios` | Sim | Disponível |
| `follow-redirects` | Não | Disponível |
| `source-map-js` | Não | Disponível |
| `vue` | Sim | Disponível |
| `xlsx` | Sim | Sem correção automática indicada |

Relatório bruto local: `.local/bluegreen-readiness/run-20261126/npm-audit-production.json`. As entradas podem compartilhar a mesma cadeia de dependências ou advisory; seis entradas não significam necessariamente seis falhas independentes exploráveis no navegador.

Antes de aprovar o pacote final:

1. Revisar os advisories do relatório, as versões afetadas e como cada pacote é usado no produto.
2. Atualizar dependências com correções compatíveis em uma branch própria da aplicação, revisar o diff do lockfile e executar seus testes.
3. Para `xlsx`, avaliar a distribuição corrigida suportada pelo mantenedor ou uma alternativa; validar leitura/exportação e os arquivos reais suportados pelo negócio. Não executar `npm audit fix --force` como solução genérica.
4. Recompilar a release final e repetir os ensaios afetados; os artefatos anteriores deixam de representar esse novo lockfile.
5. Registrar a decisão sobre risco residual. Não declarar o frontend aprovado em produção apenas porque Vite compilou.

O lockfile original e o checkout de trabalho da aplicação foram preservados nesta etapa. Não houve publicação dos artefatos auditados.
