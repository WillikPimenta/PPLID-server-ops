import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

function updateContext(status) {
  let click;
  const root = { dataset: {}, classList: { toggle() {} }, innerHTML: "", querySelector() { return null; }, contains() { return false; }, addEventListener(type, handler) { click = handler; } };
  const OC = { currentRoute: { view: "deploy" }, escapeHtml: value => String(value).replaceAll("<", "&lt;"), fetchJson: async () => status };
  vm.runInNewContext(fs.readFileSync(new URL("../public/js/console-update.js", import.meta.url), "utf8"), {
    window: { OpsConsole: OC }, document: { getElementById: () => root }, setInterval: () => 1, clearInterval() {}, setTimeout, Date,
  });
  return { OC, root, click: id => click({ target: { closest: selector => selector === `#${id}` || selector === "button" ? {} : null }, preventDefault() {} }) };
}

test("log copies raw events and escapes error lines in the panel", async () => {
  const { OC, root, click } = updateContext({ ok: true, lastResult: { phase: "failed" } });
  OC.consoleUpdateState.logOpen = true;
  OC.consoleUpdateState.logLines = ["[ERROR] <script>alert(1)</script>"];
  await OC.refreshConsoleUpdateBar({ silent: true });
  assert.match(root.innerHTML, /is-error/);
  assert.ok(!root.innerHTML.includes("<script>"));
  let copied;
  OC.copyText = async text => { copied = text; };
  click("btn-console-update-copy-log");
  assert.equal(copied, "[ERROR] <script>alert(1)</script>");
});

test("cancel requests are deduplicated and wait for server confirmation", async () => {
  const status = { inProgress: true, lastResult: { phase: "dependencies" } };
  const { OC, root, click } = updateContext(status);
  await OC.refreshConsoleUpdateBar({ silent: true });
  let finish;
  let requests = 0;
  OC.postAction = () => { requests++; return new Promise(resolve => { finish = resolve; }); };
  click("btn-console-update-cancel");
  click("btn-console-update-cancel");
  assert.equal(requests, 1);
  assert.match(root.innerHTML, /Cancelando…/);
  status.cancelRequested = true;
  finish({ ok: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(root.innerHTML, /Aguardando a etapa atual terminar/);
  status.inProgress = false;
  status.lastResult = { cancelled: true, phase: "cancelled", ok: true };
  await OC.refreshConsoleUpdateBar();
  assert.match(root.innerHTML, /Deploy cancelado/);
  assert.ok(!root.innerHTML.includes('id="btn-console-update-cancel"'));
});

test("initConsoleUpdate preserves an active update during dashboard redraw", () => {
  const source = fs.readFileSync(new URL("../public/js/console-update.js", import.meta.url), "utf8");

  assert.match(
    source,
    /const activeState = OC\.consoleUpdateState\.busy \|\| OC\.consoleUpdateState\.uiState !== "idle";/
  );
  assert.match(
    source,
    /activeState \? OC\.consoleUpdateState\.uiState : "idle"/
  );
  assert.match(source, /if \(activeState\) return;/);
});

test("status fetch uses singleflight and progress-only polling", () => {
  const source = fs.readFileSync(new URL("../public/js/console-update.js", import.meta.url), "utf8");

  assert.match(source, /statusInFlight/);
  assert.match(source, /progressInFlight/);
  assert.match(source, /progressOnly:\s*true/);
  assert.match(source, /STATUS_URL\}\?progress=1/);
  assert.match(source, /SILENT_STATUS_MIN_INTERVAL_MS/);
});

test("active update exposes pipeline phases", () => {
  const source = fs.readFileSync(new URL("../public/js/console-update.js", import.meta.url), "utf8");
  const script = fs.readFileSync(new URL("../../update_ops_console.ps1", import.meta.url), "utf8");

  for (const phase of ["pulling", "dependencies", "automation_runtime", "restarting"]) {
    assert.match(source, new RegExp(phase));
    assert.match(script, new RegExp(`phase = \"${phase}\"`));
  }
});
