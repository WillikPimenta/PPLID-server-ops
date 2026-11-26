# Dependências observadas na validação de produção

## Atualização da etapa 1 — 26/11/2026

As seis entradas de produção descritas no histórico abaixo foram corrigidas na candidata `6ab3f747`, branch `feat/bluegreen-web-only-20261126`. Vue `3.5.46`, Axios `1.21.1` e suas transitivas foram atualizados; SheetJS `0.20.3` passou a ser distribuído no próprio repositório com origem, licença e hash registrados. `npm audit --omit=dev --json` passou a reportar **zero vulnerabilidades**. Relatório: `.local/bluegreen-step1-20261126/npm-audit-production-after.json`.

Foram fixadas 84 distribuições Python em `backend/requirements.deploy.constraints.txt`, aplicado às duas instalações pip pelo pipeline. Não se trata de um lock com hashes de todos os pacotes Python. Build e testes afetados estão descritos em [etapa 1](ETAPA_1_WORKERS_DEPENDENCIAS.md).

**Pendência residual:** `npm audit` incluindo desenvolvimento reporta sete entradas, cinco moderadas e duas altas, nas cadeias de `@vue/language-core`, `vue-tsc`, `minimatch`, `brace-expansion`, `editorconfig`, `glob` e `js-beautify`. A correção sugerida exige mudança major de `vue-tsc`; não foi aplicado `npm audit fix --force`. Revisar override de `brace-expansion` e compatibilidade da cadeia de ferramentas antes de promover essa atualização. Relatório: `.local/bluegreen-step1-20261126/npm-audit-all-after.json`.

A comparação Vitest identificou os mesmos 47 casos falhando antes/depois, sem novas falhas nominais; assert/tsx registrou sete arquivos falhando em ambas as árvores. Essas pendências preexistentes não foram corrigidas neste escopo e precisam de triagem de produto antes da aprovação geral. Quatro testes de importação/exportação SheetJS passaram. A suíte completa do frontend ainda não está verde.

## Histórico anterior à correção

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

O checkout original da aplicação foi preservado; as correções ficaram na branch de revisão isolada. Não houve publicação dos artefatos auditados.
