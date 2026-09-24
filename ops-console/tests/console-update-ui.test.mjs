import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

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
