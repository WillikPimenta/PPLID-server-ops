// Render the actual availability component with its isolated demo fixtures.
// Usage: node ops-console/tools/preview_availability.mjs [output.html]
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const source = readFileSync(new URL("../public/js/monitoring.js", import.meta.url), "utf8");
const start = source.indexOf("  OC.buildAvailabilityDemo =");
const end = source.indexOf("\n  function renderTabContent(", start);
const component = source.slice(start, end);
const detailComponent = readFileSync(new URL('../public/js/availability-detail.js', import.meta.url), 'utf8');
const css = readFileSync(new URL("../public/styles.css", import.meta.url), "utf8");
const output = resolve(process.argv[2] || ".tmp-availability-preview.html");
writeFileSync(output, `<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Disponibilidade — prévia</title>
<style>${css}
body { margin:0; padding:36px; background:var(--theme-bg-page); font-family:var(--font-family-sans); }
main { max-width:1500px; margin:auto; } .preview-heading { margin-bottom:24px; } .preview-heading h1 { margin:0 0 8px; font-size:30px; color:var(--theme-text-primary); } .preview-heading p { margin:0; color:var(--theme-text-muted); font-size:14px; }
</style><main><header class="preview-heading"><h1>Disponibilidade</h1><p>Acompanhe a confiabilidade dos ambientes e investigue o histórico de falhas.</p></header><div id="preview"></div></main>
<script>
const OC = {
 monitorState: { availabilityWindow: "24h", availabilityDemo: true, availabilityScenario: "mixed", selectedEnvs: ["MAIN","DEV","HOM"] },
 currentRoute: { tab: "availability" },
 ENV_ORDER: ["MAIN","DEV","HOM"],
 escapeHtml: value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])),
 formatDate: value => new Date(value).toLocaleString("pt-BR", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}),
};
${component}
window.OpsConsole = OC;
${detailComponent}
function render() {
 document.getElementById("preview").innerHTML = renderAvailabilityView({});
 OC.bindAvailabilityDetails(document.getElementById("preview"));
 document.querySelectorAll("[data-availability-window]").forEach(button => button.onclick = () => { OC.closeAvailabilityDetail(false); OC.monitorState.availabilityWindow = button.dataset.availabilityWindow; render(); });
 document.querySelector("[data-availability-demo]").onclick = () => { OC.closeAvailabilityDetail(false); OC.monitorState.availabilityDemo = !OC.monitorState.availabilityDemo; render(); };
 document.querySelector('[data-availability-scenario]')?.addEventListener('change', event => { OC.closeAvailabilityDetail(false); OC.monitorState.availabilityScenario = event.target.value; render(); });
}
render();
if (new URLSearchParams(location.search).has('panel')) document.querySelector('.av-bar.av-tone-down[data-availability-env="DEV"]')?.click();
</script></html>`);
console.log(output);
