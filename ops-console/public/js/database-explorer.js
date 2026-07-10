/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.dbState = {
    env: "DEV",
    viewMode: "catalog",
    table: null,
    page: 1,
    search: "",
    tableFilter: "",
    schema: null,
    rows: [],
    metrics: null,
    tables: [],
    queryResult: null,
    queryError: null,
    querySql: "",
  };
  OC.dbRowEditorOpen = false;
  OC.dbExplorerInitialized = false;

  function envTabsHtml(active) {
    return OC.ENV_ORDER.map(
      (name) =>
        `<button type="button" class="env-tab ${name === active ? "is-active" : ""}" data-db-env="${name}">${name}</button>`
    ).join("");
  }

  function metricsHtml(metrics) {
    if (!metrics) return "";
    const conns = metrics.connections || {};
    const items = [
      `<div class="db-metric"><span class="db-metric-label">Banco</span><span class="db-metric-value"><code>${OC.escapeHtml(metrics.database || "—")}</code></span></div>`,
    ];
    if (metrics.sizeHuman) {
      items.push(
        `<div class="db-metric"><span class="db-metric-label">Tamanho</span><span class="db-metric-value">${OC.escapeHtml(metrics.sizeHuman)}</span></div>`
      );
    }
    if (conns.total != null) {
      items.push(
        `<div class="db-metric"><span class="db-metric-label">Conexões</span><span class="db-metric-value">${conns.total}</span></div>`
      );
    }
    if (metrics.migrations?.pending != null) {
      items.push(
        `<div class="db-metric"><span class="db-metric-label">Migrações pendentes</span><span class="db-metric-value">${metrics.migrations.pending}</span></div>`
      );
    }
    return `<div class="db-metrics-bar">${items.join("")}</div>`;
  }

  function formatRowCount(n) {
    if (n == null) return "0";
    return Number(n).toLocaleString("pt-BR");
  }

  function syncTableRowEstimate(tableName, count) {
    const entry = OC.dbState.tables.find((t) => t.name === tableName);
    if (entry) entry.rowEstimate = count;
    const cardCount = document.querySelector(
      `.db-table-card[data-table="${CSS.escape(tableName)}"] .db-table-card-count`
    );
    if (cardCount) cardCount.textContent = formatRowCount(count);
  }

  function updateDbContextNav() {
    const nav = document.getElementById("db-context-nav");
    if (!nav) return;
    if (OC.dbState.viewMode === "table" && OC.dbState.table) {
      nav.classList.remove("hidden");
      nav.setAttribute("aria-hidden", "false");
      nav.innerHTML = `<button type="button" class="view-back-btn" data-nav-back="tables">← Voltar para tabelas</button>`;
      nav.querySelector('[data-nav-back="tables"]')?.addEventListener("click", backToCatalog);
    } else {
      nav.classList.add("hidden");
      nav.setAttribute("aria-hidden", "true");
      nav.innerHTML = "";
    }
  }

  function renderTableCatalog() {
    const { tables, tableFilter } = OC.dbState;
    const q = (tableFilter || "").toLowerCase();
    const filtered = tables.filter((t) => !q || t.name.toLowerCase().includes(q));
    if (!filtered.length) {
      return `<div class="empty-state"><p class="empty-hint">Nenhuma tabela encontrada.</p></div>`;
    }
    return `<div class="db-table-cards">${filtered
      .map((t) => {
        const accessBadge = t.readOnly
          ? '<span class="db-access-badge db-access-badge-readonly">Somente leitura</span>'
          : '<span class="db-access-badge db-access-badge-editable">Editável</span>';
        return `<article
          class="db-table-card ${t.readOnly ? "is-readonly" : "is-editable"}"
          data-table="${OC.escapeHtml(t.name)}"
          data-open-table="${OC.escapeHtml(t.name)}"
          role="button"
          tabindex="0"
          title="${OC.escapeHtml(t.name)}"
          aria-label="Abrir tabela ${OC.escapeHtml(t.name)}">
          <h3 class="db-table-card-name" title="${OC.escapeHtml(t.name)}"><code>${OC.escapeHtml(t.name)}</code></h3>
          ${accessBadge}
          <span class="db-table-card-action">Abrir tabela →</span>
        </article>`;
      })
      .join("")}</div>`;
  }

  function renderQueryPanel() {
    const { table, queryResult, queryError } = OC.dbState;
    if (!table) return "";

    let resultHtml = "";
    if (queryError) {
      resultHtml = `<p class="error-msg db-query-error">${OC.escapeHtml(queryError)}</p>`;
    } else if (queryResult) {
      const cols = queryResult.columns || [];
      const rows = queryResult.rows || [];
      const header = cols.map((c) => `<th>${OC.escapeHtml(c)}</th>`).join("");
      const body = rows
        .map(
          (row) =>
            `<tr>${cols.map((c) => `<td>${OC.escapeHtml(String(row[c] ?? ""))}</td>`).join("")}</tr>`
        )
        .join("");
      resultHtml = `
        <p class="db-query-meta">${formatRowCount(queryResult.rowCount ?? rows.length)} linha(s) retornada(s)</p>
        <div class="db-grid-wrap db-query-grid-wrap">
          <table class="db-grid"><thead><tr>${header}</tr></thead>
          <tbody>${body || `<tr><td colspan="${cols.length || 1}">Sem resultados</td></tr>`}</tbody></table>
        </div>`;
    }

    return `
      <details class="db-query-panel">
        <summary>Consulta SQL (SELECT)</summary>
        <p class="drawer-hint">Consultas disponíveis somente nesta tabela. Apenas SELECT é permitido.</p>
        <textarea id="db-query-sql" class="db-query-input" rows="4" placeholder="SELECT * FROM ${OC.escapeHtml(table)} LIMIT 50">${OC.escapeHtml(OC.dbState.querySql || "")}</textarea>
        <div class="db-query-actions">
          <button type="button" class="btn btn-secondary btn-sm" id="db-query-run">Executar</button>
          <button type="button" class="btn btn-secondary btn-sm" id="db-query-fill">Preencher exemplo</button>
        </div>
        <div id="db-query-result">${resultHtml}</div>
      </details>`;
  }

  function renderGrid() {
    const { rows, schema, table, readOnly, page, pages, total } = OC.dbState;
    if (!table) {
      return `<p class="empty-hint">Selecione uma tabela para visualizar registros.</p>`;
    }
    const cols = schema?.columns?.map((c) => c.name) || [];
    const header = cols.map((c) => `<th>${OC.escapeHtml(c)}</th>`).join("");
    const body = rows
      .map((row, idx) => {
        const pk = JSON.stringify(
          Object.fromEntries((OC.dbState.primaryKey || []).map((k) => [k, row[k]]))
        );
        return `<tr data-row-idx="${idx}" data-pk="${OC.escapeHtml(pk)}">${cols
          .map((c) => `<td>${OC.escapeHtml(String(row[c] ?? ""))}</td>`)
          .join("")}</tr>`;
      })
      .join("");

    return `
      <div class="db-toolbar">
        <div class="db-toolbar-title">
          <h3><code>${OC.escapeHtml(table)}</code></h3>
          <span class="db-row-total">${formatRowCount(total)} registros</span>
          ${readOnly ? '<span class="db-access-badge db-access-badge-readonly">Somente leitura</span>' : '<span class="db-access-badge db-access-badge-editable">Editável</span>'}
        </div>
        <div class="db-toolbar-actions">
          <input type="search" id="db-search" class="filter-search" placeholder="Buscar registros…" value="${OC.escapeHtml(OC.dbState.search)}" />
          ${readOnly ? "" : '<button type="button" class="btn btn-primary btn-sm" id="db-new-row">+ Nova linha</button>'}
        </div>
      </div>
      ${renderQueryPanel()}
      <div class="db-grid-wrap">
        <table class="db-grid"><thead><tr>${header}</tr></thead>
        <tbody>${body || `<tr><td colspan="${cols.length}">Sem registros</td></tr>`}</tbody></table>
      </div>
      <div class="db-pagination">
        <button type="button" class="btn btn-secondary btn-sm" id="db-prev" ${page <= 1 ? "disabled" : ""}>Anterior</button>
        <span class="db-page-info">Página <strong>${page}</strong> / ${pages}</span>
        <button type="button" class="btn btn-secondary btn-sm" id="db-next" ${page >= pages ? "disabled" : ""}>Próxima</button>
      </div>
    `;
  }

  function patchCatalogGrid() {
    const host = document.getElementById("db-catalog-grid-host");
    if (!host) {
      renderCatalogView();
      return;
    }
    host.innerHTML = renderTableCatalog();
    const countEl = document.querySelector(".db-catalog-count");
    if (countEl) countEl.textContent = `${OC.dbState.tables.length} tabela(s)`;
    bindCatalogCardEvents();
  }

  function bindCatalogCardEvents() {
    const openFromCard = (el) => {
      const tableName = el.getAttribute("data-open-table");
      if (tableName) openTable(OC.dbState.env, tableName).catch(showDbError);
    };
    document.querySelectorAll(".db-table-card[data-open-table]").forEach((card) => {
      if (card.dataset.boundCatalog === "1") return;
      card.dataset.boundCatalog = "1";
      card.addEventListener("click", () => openFromCard(card));
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openFromCard(card);
        }
      });
    });
  }

  function renderCatalogView() {
    const main = document.getElementById("db-explorer-main");
    if (!main) return;
    updateDbContextNav();
    main.innerHTML = `
      <div class="db-catalog-toolbar">
        <input type="search" id="db-table-search" class="filter-search db-table-search" placeholder="Buscar tabela…" aria-label="Buscar tabela" value="${OC.escapeHtml(OC.dbState.tableFilter)}" />
        <span class="db-catalog-count">${OC.dbState.tables.length} tabela(s)</span>
      </div>
      <div id="db-catalog-grid-host">${renderTableCatalog()}</div>
    `;
    bindCatalogEvents();
  }

  function bindCatalogEvents() {
    document.getElementById("db-table-search")?.addEventListener("input", (e) => {
      OC.dbState.tableFilter = e.target.value;
      patchCatalogGrid();
    });
    bindCatalogCardEvents();
  }

  function backToCatalog() {
    OC.dbState.viewMode = "catalog";
    OC.dbState.table = null;
    OC.dbState.search = "";
    OC.dbState.queryResult = null;
    OC.dbState.queryError = null;
    updateDbContextNav();
    renderCatalogView();
  }

  async function openTable(env, tableName) {
    OC.dbState.viewMode = "table";
    OC.dbState.table = tableName;
    OC.dbState.page = 1;
    OC.dbState.search = "";
    OC.dbState.queryResult = null;
    OC.dbState.queryError = null;
    const main = document.getElementById("db-explorer-main");
    if (main) {
      main.innerHTML = `<p class="loading-inline">Carregando ${OC.escapeHtml(tableName)}…</p>`;
    }
    await loadTable(env, tableName, 1);
  }

  async function loadTable(env, table, page, options) {
    const opts = options || {};
    OC.dbState.table = table;
    OC.dbState.page = page;
    const q = new URLSearchParams({ page: String(page), limit: "50" });
    if (OC.dbState.search) q.set("search", OC.dbState.search);
    const [schema, data] = await Promise.all([
      OC.fetchJson(`/api/v1/database/${env}/tables/${table}/schema`),
      OC.fetchJson(`/api/v1/database/${env}/tables/${table}/rows?${q}`),
    ]);
    OC.dbState.schema = schema;
    OC.dbState.rows = data.rows || [];
    OC.dbState.primaryKey = data.primaryKey || schema.primaryKey || [];
    OC.dbState.readOnly = data.readOnly || schema.readOnly;
    OC.dbState.pages = data.pages || 1;
    OC.dbState.total = data.total || 0;
    syncTableRowEstimate(table, OC.dbState.total);
    if (!opts.skipMainRender) {
      OC.renderDatabaseMain();
    }
  }

  OC.renderDatabaseMain = function renderDatabaseMain() {
    const main = document.getElementById("db-explorer-main");
    if (!main) return;
    updateDbContextNav();
    main.innerHTML = renderGrid();
    bindGridEvents();
  };

  OC.refreshDatabasePartial = async function refreshDatabasePartial() {
    const root = document.getElementById("view-database");
    if (!root || root.classList.contains("hidden") || !OC.dbExplorerInitialized) return;
    if (OC.dbRowEditorOpen) return;

    const env = OC.dbState.env;
    if (!env) return;

    try {
      const [metrics, tablesData] = await Promise.all([
        OC.fetchJson(`/api/v1/database/${env}`),
        OC.fetchJson(`/api/v1/database/${env}/tables`),
      ]);
      OC.dbState.metrics = metrics;
      OC.dbState.tables = tablesData.tables || [];
      const metricsPanel = document.getElementById("db-metrics-panel");
      if (metricsPanel) metricsPanel.innerHTML = metricsHtml(metrics);

      if (OC.dbState.viewMode === "catalog") {
        const searchFocused = document.activeElement?.id === "db-table-search";
        if (searchFocused) {
          patchCatalogGrid();
        } else {
          renderCatalogView();
        }
      } else if (OC.dbState.table) {
        await loadTable(env, OC.dbState.table, OC.dbState.page, { skipMainRender: false });
      }
    } catch (err) {
      /* refresh silencioso */
    }
  };

  function confirmMain(env) {
    if (env !== "MAIN") return true;
    return window.confirm("CONFIRMAR operação de escrita em MAIN (produção)?");
  }

  function bindGridEvents() {
    const env = OC.dbState.env;
    const table = OC.dbState.table;
    const readOnly = OC.dbState.readOnly;

    document.getElementById("db-search")?.addEventListener("change", (e) => {
      OC.dbState.search = e.target.value;
      if (table) loadTable(env, table, 1).catch(showDbError);
    });

    document.getElementById("db-prev")?.addEventListener("click", () => {
      if (table && OC.dbState.page > 1) loadTable(env, table, OC.dbState.page - 1).catch(showDbError);
    });
    document.getElementById("db-next")?.addEventListener("click", () => {
      if (table && OC.dbState.page < OC.dbState.pages) loadTable(env, table, OC.dbState.page + 1).catch(showDbError);
    });

    if (!readOnly) {
      document.getElementById("db-new-row")?.addEventListener("click", () => openRowDrawer(env, table, "insert"));
    }

    document.querySelectorAll(".db-grid tbody tr[data-pk]").forEach((tr) => {
      if (readOnly) return;
      tr.style.cursor = "pointer";
      tr.addEventListener("click", () => {
        const idx = parseInt(tr.getAttribute("data-row-idx"), 10);
        const row = OC.dbState.rows[idx];
        if (row) openRowDrawer(env, table, "edit", row);
      });
    });

    document.getElementById("db-query-fill")?.addEventListener("click", () => {
      const ta = document.getElementById("db-query-sql");
      if (ta && table) {
        ta.value = `SELECT * FROM ${table} LIMIT 50`;
        OC.dbState.querySql = ta.value;
      }
    });

    document.getElementById("db-query-sql")?.addEventListener("input", (e) => {
      OC.dbState.querySql = e.target.value;
    });

    document.getElementById("db-query-run")?.addEventListener("click", async () => {
      const ta = document.getElementById("db-query-sql");
      const sql = ta?.value?.trim();
      if (!sql) {
        window.alert("Informe uma consulta SELECT.");
        return;
      }
      try {
        OC.dbState.queryError = null;
        OC.dbState.queryResult = await OC.fetchJson(
          `/api/v1/database/${env}/tables/${table}/query`,
          { method: "POST", body: JSON.stringify({ sql }) }
        );
        OC.renderDatabaseMain();
        document.querySelector(".db-query-panel")?.setAttribute("open", "");
      } catch (err) {
        OC.dbState.queryError = err.message || "Falha na consulta";
        OC.dbState.queryResult = null;
        OC.renderDatabaseMain();
        document.querySelector(".db-query-panel")?.setAttribute("open", "");
      }
    });
  }

  function closeRowDrawer() {
    OC.dbRowEditorOpen = false;
    document.getElementById("db-row-overlay")?.classList.add("hidden");
    document.getElementById("db-row-overlay")?.setAttribute("aria-hidden", "true");
    const drawer = document.getElementById("db-row-drawer");
    drawer?.classList.remove("open");
    drawer?.classList.add("hidden");
    drawer?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("db-row-drawer-open");
    const body = document.getElementById("db-row-drawer-body");
    if (body) body.innerHTML = "";
  }

  function openRowDrawer(env, table, mode, row) {
    const schema = OC.dbState.schema;
    const pk = OC.dbState.primaryKey || [];
    const fields = (schema?.columns || [])
      .filter((c) => mode === "insert" || !pk.includes(c.name))
      .map((c) => {
        const val = row ? row[c.name] ?? "" : "";
        return `<label>${OC.escapeHtml(c.name)} <small>(${OC.escapeHtml(c.type)})</small>
          <input class="env-input db-field" data-col="${OC.escapeHtml(c.name)}" value="${OC.escapeHtml(String(val ?? ""))}" /></label>`;
      })
      .join("");

    const title = document.getElementById("db-row-drawer-title");
    if (title) title.textContent = mode === "insert" ? `Nova linha — ${table}` : `Editar linha — ${table}`;

    const body = document.getElementById("db-row-drawer-body");
    if (!body) return;

    body.innerHTML = `
      <form id="db-row-form" class="modal-form">${fields}</form>
      <div class="drawer-actions">
        <button type="button" class="btn btn-primary btn-sm" id="db-row-save">Salvar</button>
        ${mode === "edit" ? '<button type="button" class="btn btn-danger btn-sm" id="db-row-delete">Excluir</button>' : ""}
        <button type="button" class="btn btn-secondary btn-sm" id="db-row-cancel">Cancelar</button>
      </div>
    `;

    const overlay = document.getElementById("db-row-overlay");
    const drawer = document.getElementById("db-row-drawer");
    overlay?.classList.remove("hidden");
    overlay?.setAttribute("aria-hidden", "false");
    drawer?.classList.remove("hidden");
    drawer?.classList.add("open");
    drawer?.setAttribute("aria-hidden", "false");
    document.body.classList.add("db-row-drawer-open");
    OC.dbRowEditorOpen = true;

    body.querySelector("#db-row-cancel")?.addEventListener("click", closeRowDrawer);
    overlay?.addEventListener("click", closeRowDrawer);

    body.querySelector("#db-row-save")?.addEventListener("click", async () => {
      if (!confirmMain(env)) return;
      const values = {};
      body.querySelectorAll(".db-field").forEach((input) => {
        const col = input.getAttribute("data-col");
        if (col) values[col] = input.value;
      });
      const reqBody = { values, confirmMain: env === "MAIN" };
      try {
        if (mode === "insert") {
          await OC.fetchJson(`/api/v1/database/${env}/tables/${table}/rows`, {
            method: "POST",
            body: JSON.stringify(reqBody),
          });
        } else {
          const pkObj = Object.fromEntries(pk.map((k) => [k, row[k]]));
          await OC.fetchJson(`/api/v1/database/${env}/tables/${table}/rows`, {
            method: "PATCH",
            body: JSON.stringify({ pk: pkObj, values, confirmMain: env === "MAIN" }),
          });
        }
        closeRowDrawer();
        await loadTable(env, table, OC.dbState.page);
      } catch (err) {
        window.alert(err.message || "Falha ao salvar");
      }
    });

    body.querySelector("#db-row-delete")?.addEventListener("click", async () => {
      if (!confirmMain(env)) return;
      if (!window.confirm("Excluir este registro?")) return;
      const pkObj = Object.fromEntries(pk.map((k) => [k, row[k]]));
      try {
        await OC.fetchJson(`/api/v1/database/${env}/tables/${table}/rows`, {
          method: "DELETE",
          body: JSON.stringify({ pk: pkObj, confirmMain: env === "MAIN" }),
        });
        closeRowDrawer();
        await loadTable(env, table, OC.dbState.page);
      } catch (err) {
        window.alert(err.message || "Falha ao excluir");
      }
    });
  }

  function showDbError(err) {
    const main = document.getElementById("db-explorer-main");
    if (main) main.innerHTML = `<p class="error-msg">${OC.escapeHtml(err.message)}</p>`;
    if (err.code === 401 && OC.onUnauthorized) OC.onUnauthorized();
  }

  OC.renderDatabaseExplorer = async function renderDatabaseExplorer(envName) {
    const root = document.getElementById("view-database");
    if (!root) return;
    OC.dbState.env = envName;
    OC.dbState.viewMode = "catalog";
    OC.dbState.table = null;
    OC.dbState.search = "";
    OC.dbState.queryResult = null;
    OC.dbState.queryError = null;
    OC.dbExplorerInitialized = false;
    root.innerHTML = `
      <section class="section-block">
        ${OC.renderInternalPageHeader({ title: "Explorador de banco", env: envName })}
        <div id="db-context-nav" class="view-back-nav view-back-nav-page hidden" aria-hidden="true"></div>
        <div class="section-head-row section-head-row-compact">
          <span class="section-head-spacer"></span>
          <div class="env-tabs" role="tablist">${envTabsHtml(envName)}</div>
        </div>
        ${envName === "MAIN" ? '<div class="alert alert-production"><strong>Produção (MAIN)</strong><span>Escrita exige confirmação explícita.</span></div>' : ""}
        <div id="db-metrics-panel"><div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Carregando métricas…</div></div>
        <div id="db-explorer-main" class="db-main db-main-catalog"><div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Carregando tabelas…</div></div>
      </section>
    `;

    OC.bindBackNavigation(root);

    root.querySelectorAll("[data-db-env]").forEach((btn) => {
      btn.addEventListener("click", () => OC.navigate("database", btn.getAttribute("data-db-env")));
    });

    try {
      const [metrics, tablesData] = await Promise.all([
        OC.fetchJson(`/api/v1/database/${envName}`),
        OC.fetchJson(`/api/v1/database/${envName}/tables`),
      ]);
      OC.dbState.metrics = metrics;
      OC.dbState.tables = tablesData.tables || [];
      document.getElementById("db-metrics-panel").innerHTML = metricsHtml(metrics);
      OC.dbExplorerInitialized = true;
      renderCatalogView();
    } catch (err) {
      showDbError(err);
    }
  };

  document.getElementById("db-row-drawer-close")?.addEventListener("click", closeRowDrawer);
})();
