/* global window */
(function () {
  window.OpsConsole = window.OpsConsole || {};
  const OC = window.OpsConsole;

  OC.filterState = {
  env: "",
  status: "",
  branch: "",
  search: "",
};

OC.readFiltersFromDom = function readFiltersFromDom() {
  OC.filterState = {
    env: document.getElementById("filter-env")?.value || "",
    status: document.getElementById("filter-status")?.value || "",
    branch: document.getElementById("filter-branch")?.value || "",
    search: (document.getElementById("filter-search")?.value || "").trim().toLowerCase(),
  };
};

OC.applyFilters = function applyFilters(rows) {
  const { env, status, branch, search } = OC.filterState;

  return rows.filter((row) => {
    if (env && row.environment !== env) return false;
    if (branch && (row.branch || "").toLowerCase() !== branch.toLowerCase()) return false;

    if (status) {
      const key = row.statusKey;
      if (status === "running" && !(key === "running" || key === "deploying")) {
        return false;
      }
      if (status === "deploy_pending" && key !== "deploy_pending") return false;
      if (status === "success" && key !== "success") return false;
      if (status === "failed" && key !== "failed" && key !== "unhealthy") return false;
      if (status === "online" && !["online", "success", "idle"].includes(key)) return false;
      if (status === "offline" && key !== "offline") return false;
      if (status === "syncing" && key !== "syncing") return false;
    }

    if (search) {
      const hay = [
        row.environment,
        row.branch,
        row.sha,
        row.subject,
        row.author,
        row.message,
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(search)) return false;
    }

    return true;
  });
};

OC.bindFilters = function bindFilters(onChange) {
  ["filter-env", "filter-status", "filter-branch", "filter-search"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const eventName = id === "filter-search" ? "input" : "change";
    el.addEventListener(eventName, () => {
      OC.readFiltersFromDom();
      onChange();
    });
  });

  const technical = document.getElementById("filter-technical");
  if (technical) {
    technical.addEventListener("change", () => {
      OC.showTechnicalActivity = technical.checked;
      onChange(true);
    });
  }
};
})();