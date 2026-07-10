/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.envConfigState = {
    env: "DEV",
    saved: true,
    data: null,
    removed: { backend: new Set(), frontend: new Set() },
  };
  OC.ENV_KEY_PATTERN = /^[A-Z][A-Z0-9_]*$/;
  OC.ENV_MASK_PLACEHOLDER = "••••••••";

  function envTabsHtml(active) {
    return OC.ENV_ORDER.map(
      (name) =>
        `<button type="button" class="env-tab ${name === active ? "is-active" : ""}" data-env-tab="${name}">${name}</button>`
    ).join("");
  }

  OC.validateEnvKey = function validateEnvKey(key) {
    const k = (key || "").trim();
    if (!k) return "Informe o nome da variável.";
    if (!OC.ENV_KEY_PATTERN.test(k)) {
      return "Nome inválido. Use letras maiúsculas, números e underscore (ex.: MINHA_VAR).";
    }
    return null;
  };

  function renderVarItem(scope, key, item) {
    const masked = item.masked;
    const type = masked ? "password" : "text";
    const secretBadge = masked ? '<span class="badge-secret">Segredo</span>' : "";
    const revealBtn = masked
      ? `<button type="button" class="env-icon-btn env-reveal-btn" data-reveal-scope="${scope}" data-reveal-key="${OC.escapeHtml(key)}" title="Mostrar valor" aria-label="Mostrar valor">­ƒæü</button>`
      : "";
    return `<div class="env-var-item" data-var-row="${OC.escapeHtml(key)}">
      <div class="env-var-item-top">
        <code class="env-var-key">${OC.escapeHtml(key)}</code>
        ${secretBadge}
        <button type="button" class="env-icon-btn env-remove-btn" data-remove-scope="${scope}" data-remove-key="${OC.escapeHtml(key)}" title="Remover variável" aria-label="Remover ${OC.escapeHtml(key)}">×</button>
      </div>
      <div class="env-var-item-value">
        <input class="env-input" data-scope="${scope}" data-key="${OC.escapeHtml(key)}" data-masked="${masked ? "1" : "0"}" type="${type}" value="${OC.escapeHtml(item.value)}" aria-label="Valor de ${OC.escapeHtml(key)}" />
        ${revealBtn}
      </div>
    </div>`;
  }

  function renderVarsPanel(scope, title, vars) {
    const keys = Object.keys(vars || {}).sort();
    const items = keys.length
      ? keys.map((key) => renderVarItem(scope, key, vars[key])).join("")
      : `<p class="empty-state-inline">Nenhuma variável neste grupo.</p>`;
    return `<section class="env-panel-card">
      <header class="env-panel-head">
        <h3 class="env-panel-title">${title}</h3>
        <span class="env-panel-count">${keys.length}</span>
      </header>
      <div class="env-var-list" data-env-tbody="${scope}">${items}</div>
      <button type="button" class="btn btn-secondary btn-sm env-add-var-btn" data-add-var="${scope}">+ Nova variável</button>
    </section>`;
  }

  OC.bindEnvVarInputs = function bindEnvVarInputs(container, onDirty) {
    container.querySelectorAll(".env-input").forEach((input) => {
      input.addEventListener("input", onDirty);
    });
    container.querySelectorAll("[data-add-var]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const scope = btn.getAttribute("data-add-var");
        const list = container.querySelector(`[data-env-tbody="${scope}"]`);
        if (!list) return;
        list.querySelector(".empty-state-inline")?.remove();
        const item = document.createElement("div");
        item.className = "env-var-item env-new-row";
        item.innerHTML = `
          <div class="env-var-item-top">
            <input class="env-input env-new-key" data-scope="${scope}" placeholder="NOME_VAR" aria-label="Nome da variável" />
          </div>
          <input class="env-input env-new-desc" data-scope="${scope}" placeholder="Descrição (opcional)" aria-label="Descrição opcional" />
          <div class="env-var-item-value">
            <input class="env-input env-new-value" data-scope="${scope}" placeholder="Valor" aria-label="Valor da variável" />
          </div>`;
        list.appendChild(item);
        item.querySelectorAll("input").forEach((input) => input.addEventListener("input", onDirty));
        item.querySelector(".env-new-key")?.focus();
        onDirty();
      });
    });
    container.querySelectorAll(".env-reveal-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const scope = btn.getAttribute("data-reveal-scope");
        const key = btn.getAttribute("data-reveal-key");
        const env = OC.envConfigState.env;
        if (!scope || !key || !env) return;
        const input = container.querySelector(
          `.env-input[data-scope="${scope}"][data-key="${CSS.escape(key)}"]`
        );
        if (btn.dataset.revealed === "1" && input) {
          input.type = "password";
          input.value = OC.ENV_MASK_PLACEHOLDER;
          delete input.dataset.revealed;
          btn.dataset.revealed = "0";
          btn.title = "Mostrar valor";
          return;
        }
        btn.disabled = true;
        try {
          const data = await OC.fetchJson(
            `/api/v1/env/${env}/reveal?scope=${encodeURIComponent(scope)}&key=${encodeURIComponent(key)}`
          );
          if (input) {
            input.type = "text";
            input.value = data.value ?? "";
            input.dataset.revealed = "1";
          }
          btn.dataset.revealed = "1";
          btn.title = "Ocultar valor";
        } catch (err) {
          window.alert(err.message || "Não foi possível revelar o valor.");
        } finally {
          btn.disabled = false;
        }
      });
    });
    container.querySelectorAll(".env-remove-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const scope = btn.getAttribute("data-remove-scope");
        const key = btn.getAttribute("data-remove-key");
        if (!scope || !key) return;
        if (!window.confirm(`Remover a variável ${key}?`)) return;
        OC.envConfigState.removed[scope].add(key);
        btn.closest(".env-var-item")?.remove();
        const list = container.querySelector(`[data-env-tbody="${scope}"]`);
        if (list && !list.querySelector(".env-var-item")) {
          list.innerHTML = `<p class="empty-state-inline">Nenhuma variável neste grupo.</p>`;
        }
        onDirty();
      });
    });
  };

  OC.collectEnvPayload = function collectEnvPayload(container) {
    const payload = { backend: {}, frontend: {} };
    const remove = { backend: [], frontend: [] };
    const seen = { backend: new Set(), frontend: new Set() };

    container.querySelectorAll(".env-input[data-key]").forEach((input) => {
      const scope = input.getAttribute("data-scope");
      const key = input.getAttribute("data-key");
      if (!scope || !key) return;
      if (OC.envConfigState.removed[scope]?.has(key)) return;
      if (input.dataset.revealed !== "1" && input.dataset.masked === "1" && input.value === OC.ENV_MASK_PLACEHOLDER) {
        return;
      }
      payload[scope][key] = input.value;
      seen[scope].add(key);
    });

    container.querySelectorAll(".env-new-row").forEach((row) => {
      const scope = row.querySelector(".env-new-key")?.getAttribute("data-scope");
      const key = row.querySelector(".env-new-key")?.value?.trim() || "";
      const value = row.querySelector(".env-new-value")?.value ?? "";
      if (!scope || !key) return;
      const err = OC.validateEnvKey(key);
      if (err) throw new Error(err);
      if (seen[scope].has(key)) throw new Error(`Variável duplicada: ${key}`);
      payload[scope][key] = value;
      seen[scope].add(key);
    });

    for (const scope of ["backend", "frontend"]) {
      remove[scope] = [...(OC.envConfigState.removed[scope] || [])];
    }
    if (remove.backend.length || remove.frontend.length) {
      payload.remove = remove;
    }

    return payload;
  };

  function diffCellClass(vals, env) {
    const v = vals[env] ?? "—";
    const others = OC.ENV_ORDER.filter((e) => e !== env).map((e) => vals[e] ?? "—");
    return others.some((o) => o !== v) ? "diff-changed" : "";
  }

  OC.renderEnvConfig = async function renderEnvConfig(envName) {
    const root = document.getElementById("view-env");
    if (!root) return;
    OC.envConfigState.env = envName;
    OC.envConfigState.removed = { backend: new Set(), frontend: new Set() };
    root.innerHTML = `
      <section class="section-block">
        ${OC.renderInternalPageHeader({ title: "Variáveis de ambiente", env: envName })}
        <div class="section-head-row section-head-row-compact">
          <p class="env-meta-compact">Banco <code>${OC.escapeHtml("…")}</code></p>
          <div class="env-tabs" role="tablist">${envTabsHtml(envName)}</div>
        </div>
        ${envName === "MAIN" ? '<div class="alert alert-production"><strong>Produção (MAIN)</strong><span>Alterações sensíveis exigem confirmação explícita.</span></div>' : ""}
        <div id="env-config-body"><div class="loading-inline"><div class="loading-spinner loading-spinner-sm"></div> Carregando variáveis…</div></div>
      </section>
    `;

    OC.bindBackNavigation(root);
    root.querySelectorAll("[data-env-tab]").forEach((btn) => {
      btn.addEventListener("click", () => OC.navigate("env", btn.getAttribute("data-env-tab")));
    });

    const body = document.getElementById("env-config-body");
    try {
      const [data, diffData] = await Promise.all([
        OC.fetchJson(`/api/v1/env/${envName}`),
        OC.fetchJson("/api/v1/env/diff").catch(() => ({ diff: {} })),
      ]);
      OC.envConfigState.data = data;
      OC.envConfigState.saved = true;

      const metaEl = root.querySelector(".env-meta-compact code");
      if (metaEl) metaEl.textContent = data.postgresDb || "—";

      const diff = diffData.diff || {};
      const diffRows = Object.keys(diff)
        .sort()
        .map((key) => {
          const vals = diff[key];
          const cells = OC.ENV_ORDER.map(
            (e) => `<td class="${diffCellClass(vals, e)}"><code>${OC.escapeHtml(vals[e] ?? "—")}</code></td>`
          ).join("");
          return `<tr><td class="env-var-name"><code>${OC.escapeHtml(key)}</code></td>${cells}</tr>`;
        })
        .join("");

      body.innerHTML = `
        <div class="env-columns">
          ${renderVarsPanel("backend", "Backend (.env)", data.backend)}
          ${renderVarsPanel("frontend", "Frontend (.env)", data.frontend)}
        </div>
        <div class="env-footer-actions">
          <button type="button" class="btn btn-primary btn-sm" id="env-save-btn">Salvar alterações</button>
          <button type="button" class="btn btn-secondary btn-sm" id="env-apply-btn" disabled>Aplicar (reiniciar backend)</button>
          <p class="drawer-hint env-save-hint">Segredos mascarados por padrão. Após salvar, use Aplicar para reiniciar o backend.</p>
        </div>
        ${
          diffRows
            ? `<details class="env-diff-section">
          <summary>Comparação entre ambientes <span class="section-count">${Object.keys(diff).length}</span></summary>
          <div class="env-diff-scroll">
            <table class="env-diff-table">
              <thead><tr><th>Chave</th>${OC.ENV_ORDER.map((e) => `<th>${e}</th>`).join("")}</tr></thead>
              <tbody>${diffRows}</tbody>
            </table>
          </div>
        </details>`
            : ""
        }
      `;

      const markDirty = () => {
        OC.envConfigState.saved = false;
        document.getElementById("env-apply-btn").disabled = true;
      };
      OC.bindEnvVarInputs(body, markDirty);

      document.getElementById("env-save-btn")?.addEventListener("click", async () => {
        let payload;
        try {
          payload = OC.collectEnvPayload(body);
        } catch (err) {
          window.alert(err.message);
          return;
        }
        if (envName === "MAIN" && !window.confirm("Confirmar alterações em MAIN?")) return;
        if (envName === "MAIN") payload.confirmMain = true;
        try {
          const result = await OC.putJson(`/api/v1/env/${envName}`, payload);
          OC.envConfigState.saved = true;
          document.getElementById("env-apply-btn").disabled = false;
          window.alert(result.needsRestart ? "Salvo. Use Aplicar para reiniciar o backend." : "Salvo.");
          OC.renderEnvConfig(envName);
        } catch (err) {
          window.alert(err.message || "Falha ao salvar");
        }
      });

      document.getElementById("env-apply-btn")?.addEventListener("click", async () => {
        if (!window.confirm(`Reiniciar backend de ${envName} para aplicar variáveis?`)) return;
        try {
          const result = await OC.postAction(`/api/v1/actions/restart/${envName}`, { service: "backend" });
          window.alert(result.ok ? "Backend reiniciado." : result.error || "Falha ao reiniciar");
        } catch (err) {
          window.alert(err.message || "Falha ao aplicar");
        }
      });
    } catch (err) {
      body.innerHTML = `<div class="empty-state"><p class="error-msg">${OC.escapeHtml(err.message)}</p></div>`;
      if (err.code === 401 && OC.onUnauthorized) OC.onUnauthorized();
    }
  };
})();
