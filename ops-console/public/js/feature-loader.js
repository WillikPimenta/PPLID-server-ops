/* global window, document */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;
  const scripts = new Map();
  const features = new Map();
  const definitions = {
    env: ["/js/env-config.js"],
    database: ["/js/database-explorer.js"],
    automations: ["/js/automations-config.js?v=20260910b", "/js/automations.js?v=20260910b"],
    host: ["/js/ops-perf.js", "/js/host.js"],
    monitoring: ["/js/ops-perf.js", "/js/monitoring.js", "/js/monitor-drawer.js"],
    deployDetails: ["/js/deploy-drawer.js", "/js/filters.js", "/js/drawer.js"],
    deployProgress: [
      "/js/deploy-progress.js",
      "/js/deploy-drawer.js",
      "/js/filters.js",
      "/js/drawer.js",
    ],
  };

  function loadScript(src) {
    if (scripts.has(src)) return scripts.get(src);
    const promise = new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[src="${src}"]`);
      if (existing?.dataset.loaded === "true") {
        resolve();
        return;
      }
      const script = existing || document.createElement("script");
      script.src = src;
      script.async = false;
      script.addEventListener("load", () => {
        script.dataset.loaded = "true";
        resolve();
      }, { once: true });
      script.addEventListener("error", () => reject(new Error(`Falha ao carregar ${src}`)), { once: true });
      if (!existing) document.body.appendChild(script);
    });
    scripts.set(src, promise);
    return promise;
  }

  OC.ensureFeature = function ensureFeature(name) {
    if (features.has(name)) return features.get(name);
    const sources = definitions[name];
    if (!sources) return Promise.reject(new Error(`Funcionalidade desconhecida: ${name}`));
    const promise = sources.reduce((chain, src) => chain.then(() => loadScript(src)), Promise.resolve());
    features.set(name, promise);
    return promise;
  };
})();
