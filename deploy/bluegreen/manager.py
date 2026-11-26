"""Journaled deployment operations. Call with the environment lock held."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import uuid

import psutil

from . import processes
from .caddy import Caddy, build_config, request, verify_binary
from .config import Config
from .store import atomic_json, digest, read_json, utc


class Manager:
    def __init__(self, config: Config):
        self.config = config
        self.proxy = Caddy(config)

    def read(self, *, missing=False):
        path = self.config.state_path
        if not path.exists():
            if missing:
                return {"schemaVersion": 1, "environment": self.config.environment, "active": None,
                        "previous": None, "candidate": None, "slots": {}, "phase": "uninitialized",
                        "history": [], "intent": None}
            raise RuntimeError("Blue-green is not initialized; follow the first-activation runbook")
        state = read_json(path)  # Corruption is an error, never silently defaults.
        if state.get("schemaVersion") != 1 or state.get("environment") != self.config.environment:
            raise RuntimeError("Unsupported blue-green state")
        for name, slot in state["slots"].items():
            if name not in ("A", "B") or Path(slot["release"]) != self.config.release(slot["sha"]):
                raise RuntimeError("State contains an invalid release")
            if slot["backendPort"] != self.config.slots[name]["backendPort"] or slot["frontendPort"] != self.config.slots[name]["frontendPort"]:
                raise RuntimeError("Slot ports changed while state exists; use the migration runbook")
        return state

    def save(self, state, phase=None):
        if phase:
            state["phase"] = phase
        state["updatedAt"] = utc()
        atomic_json(self.config.state_path, state)
        # Append-only events remain outside the mutable release tree.
        self.config.directory.mkdir(parents=True, exist_ok=True)
        with (self.config.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": state["updatedAt"], "phase": state["phase"],
                                     "active": state.get("active"), "candidate": state.get("candidate"),
                                     "runId": state.get("runId"), "error": state.get("lastError")}) + "\n")

    def cancelled(self):
        if (self.config.root / "cancel-requested.json").exists():
            raise RuntimeError("DEPLOY_CANCELLED")

    def resources(self):
        self.config.directory.mkdir(parents=True, exist_ok=True)
        if psutil.virtual_memory().available < self.config.bg["minimumAvailableMb"] * 1024**2:
            raise RuntimeError("Insufficient available memory; active instance preserved")
        if shutil.disk_usage(self.config.directory).free < self.config.bg["minimumDiskMb"] * 1024**2:
            raise RuntimeError("Insufficient disk space")

    def contract(self, release: Path, sha: str):
        meta = read_json(release / "meta.json")
        full = meta.get("shaFull", meta.get("sha", ""))
        if meta.get("built") is not True or not full.startswith(sha) or len(full) != 40:
            raise RuntimeError("Release must be built and identify its full SHA")
        contract = read_json(release / "scripts/deploy/bluegreen-contract.json")
        if contract != {"schemaVersion": 1, "webOnlyFlag": "PPLID_WEB_ONLY", "backgroundWorkers": "independent"}:
            raise RuntimeError("Application web-only contract v1 is required")
        if not (release / "frontend/dist/index.html").is_file():
            raise RuntimeError("Missing frontend build")
        return full

    def publish_assets(self, release: Path):
        source = release / "frontend/dist/assets"
        if not source.is_dir():
            return
        destination = self.config.assets / "assets"
        for path in source.rglob("*"):
            if path.is_symlink():
                raise RuntimeError("Asset symlinks are forbidden")
            if not path.is_file():
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != path.read_bytes():
                    raise RuntimeError(f"Immutable asset collision: {path.name}")
                continue
            # Exclusive publication; never expose a partial asset.
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            shutil.copyfile(path, temporary)
            os.replace(temporary, target)

    def start_proxy(self, state, configuration):
        verify_binary(self.config)
        if state.get("proxy") and processes.owned(state["proxy"]):
            self.proxy.get()
            return
        # A foreign listener is never adopted or stopped.
        processes.assert_free(self.config.admin_port)
        path = self.config.directory / "proxy-start.json"
        atomic_json(path, configuration)
        state["proxyStarting"] = True
        self.save(state)
        process = processes.launch([str(self.config.caddy), "run", "--config", str(path)],
                                   self.config.directory / "proxy.log")
        state["proxy"] = processes.identity(process.pid)
        state["proxyStarting"] = False
        self.save(state)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Proxy exited; inspect proxy.log")
            try:
                if digest(self.proxy.get()) == digest(configuration):
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.1)
        raise RuntimeError("Proxy startup timed out")

    def reload(self, state, active):
        started = time.perf_counter()
        if not state.get("proxy") or not processes.owned(state["proxy"]):
            raise RuntimeError("Owned proxy is not running; recover before promoting")
        observed = self.proxy.get()
        if state.get("proxyHash") and digest(observed) != state["proxyHash"]:
            raise RuntimeError("Unexpected proxy configuration; recovery required")
        traffic_path = self.config.directory / "traffic.json"
        before = read_json(traffic_path) if traffic_path.exists() else {"allowed": []}
        if state.get("trafficHash") and digest(before) != state["trafficHash"]:
            raise RuntimeError("Unexpected traffic gate; recover before mutation")
        after = {"allowed": [state["slots"][active]["instanceId"]] if active else []}
        state["intent"] = {"before": state.get("active"), "after": active,
                           "beforeGate": before, "afterGate": after, "at": utc()}
        self.save(state)
        warm = {"allowed": list(dict.fromkeys(before["allowed"] + after["allowed"]))}
        atomic_json(traffic_path, warm)
        # Four successful health intervals before withdrawal. Direct readiness
        # is required too; no client requests are retried to hide failures.
        if active:
            self.healthy(state["slots"][active])
            time.sleep(1.1)
        convergence_started = time.perf_counter()
        atomic_json(traffic_path, after)
        state["trafficHash"] = digest(after)
        # Only first activation adds public listeners. Further switches use the
        # fixed config above and never invoke Caddy /load.
        if not observed.get("apps", {}).get("http", {}).get("servers"):
            desired = build_config(self.config, state["slots"], active)
            self.proxy.load(desired)
            state["proxyHash"] = digest(desired)
        state["active"] = active
        state["intent"] = None
        self.save(state)
        if active:
            deadline = time.monotonic() + 5
            while True:
                try:
                    self.healthy(state["slots"][active], public=True)
                    break
                except (OSError, ValueError, RuntimeError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.1)
        state["trafficSwitchMs"] = round((time.perf_counter() - convergence_started) * 1000, 2)
        state["warmupAndSwitchMs"] = round((time.perf_counter() - started) * 1000, 2)
        self.save(state)

    def start_slot(self, state, name, sha):
        self.resources()
        release = self.config.release(sha)
        full_sha = self.contract(release, sha)
        ports = self.config.slots[name]
        processes.assert_free(ports["backendPort"])
        # Frontend listeners belong to Caddy; a new slot port must be free.
        processes.assert_free(ports["frontendPort"])
        self.publish_assets(release)
        instance = uuid.uuid4().hex
        directory = self.config.directory / "instances" / instance
        directory.mkdir(parents=True)
        slot = {"sha": sha, "shaFull": full_sha, "release": str(release), "slot": name,
                "instanceId": instance, "environment": self.config.environment, **ports,
                "createdAt": utc(), "stopFile": str(directory / "stop"),
                "shaFile": str(directory / "deployed.json"),
                "trafficFile": str(self.config.directory / "traffic.json"),
                "assetsDirectory": str(self.config.assets / "assets"),
                "drainSeconds": self.config.timeouts["drainSeconds"]}
        atomic_json(Path(slot["shaFile"]), {"sha": sha, "shaFull": full_sha, "deployedSha": sha,
                                          "deployedShaFull": full_sha})
        spec = directory / "spec.json"
        atomic_json(spec, slot)
        state["slots"][name] = slot
        state["candidate"] = name
        self.save(state, "starting")
        python = release / "backend/.venv/Scripts/python.exe"
        if not python.exists():
            raise RuntimeError("Release Python venv missing")
        if not any(other["instanceId"] != instance and other["shaFull"] == full_sha for other in state["slots"].values()):
            with (directory / "preflight.log").open("ab") as output:
                result = subprocess.run([str(python), str(Path(__file__).with_name("slot_worker.py")),
                                         "--spec", str(spec), "--check-only", "--prepare-static"],
                                        stdout=output, stderr=subprocess.STDOUT,
                                        timeout=self.config.timeouts["commandSeconds"],
                                        creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError("Candidate schema/static preflight failed; inspect preflight.log")
        # No call to start_env: it migrates, provisions and starts schedulers.
        process = processes.launch([str(python), str(Path(__file__).with_name("slot_worker.py")), "--spec", str(spec)],
                                   directory / "backend.log", cwd=release / "backend")
        slot["process"] = processes.identity(process.pid)
        self.save(state)
        return slot

    def healthy(self, slot, *, public=False):
        backend = self.config.backend_port if public else slot["backendPort"]
        frontend = self.config.frontend_port if public else slot["frontendPort"]
        payload = request(f"http://127.0.0.1:{backend}/__pplid/ready", timeout=3)
        if payload.get("ready") is not True or payload.get("sha") != slot["sha"] or payload.get("instanceId") != slot["instanceId"]:
            raise RuntimeError("Readiness identity mismatch")
        from .caddy import OPENER
        with OPENER.open(f"http://127.0.0.1:{frontend}/", timeout=3) as response:
            if response.status != 200 or response.headers.get("X-Pplid-Release") != slot["sha"]:
                raise RuntimeError("Frontend identity mismatch")
            response.read()
        if not slot.get("process") or not processes.owned(slot["process"]):
            raise RuntimeError("Slot process ownership could not be verified")

    def wait_ready(self, slot):
        deadline = time.monotonic() + self.config.timeouts["startupSeconds"]
        while time.monotonic() < deadline:
            self.cancelled()
            if not slot.get("process") or not processes.owned(slot["process"]):
                raise RuntimeError("Candidate exited before readiness; inspect backend.log")
            try:
                self.healthy(slot)
                return
            except (OSError, ValueError, RuntimeError):
                time.sleep(.2)
        raise RuntimeError("Candidate readiness timed out")

    def retire(self, state, name):
        slot = state["slots"][name]
        if name == state.get("active"):
            raise RuntimeError("Refusing to retire the active slot")
        # After removal of frontend routes Caddy may still be proxying long
        # backend requests from an old config. Keep the backend alive for the
        # full configured drain window before asking it to stop.
        self.save(state, "draining")
        deadline = time.monotonic() + self.config.timeouts["drainSeconds"]
        while time.monotonic() < deadline:
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
        if slot.get("process"):
            processes.stop(slot["process"], Path(slot["stopFile"]), self.config.timeouts["stopSeconds"])
        # A dead/reused launcher PID is not proof that all its old listeners
        # disappeared. Keep the release protected if ownership is ambiguous.
        processes.assert_free(slot["backendPort"])
        processes.assert_free(slot["frontendPort"])
        del state["slots"][name]
        self.reload(state, state["active"])

    def observe(self, state, slot):
        deadline = time.monotonic() + self.config.timeouts["observeSeconds"]
        while time.monotonic() < deadline:
            self.cancelled()
            self.healthy(slot, public=True)
            time.sleep(min(.5, max(0, deadline - time.monotonic())))

    def prepare(self, sha):
        state = self.read(missing=True)
        if state["slots"] or state.get("proxy"):
            raise RuntimeError("Already prepared; use recovery or promotion")
        state["runId"] = "prepare-" + secrets.token_hex(6)
        slot = self.start_slot(state, "A", sha)
        atomic_json(self.config.directory / "traffic.json", {"allowed": []})
        configuration = build_config(self.config, state["slots"], None)
        self.start_proxy(state, configuration)
        state["proxyHash"] = digest(configuration)
        self.save(state)
        self.wait_ready(slot)
        self.save(state, "prepared")
        return state

    def activate(self):
        if self.config.bg.get("workersOwnershipConfirmed") is not True:
            raise RuntimeError("Confirm independent background worker ownership before first web activation")
        state = self.read()
        if state["phase"] != "prepared" or state["active"] is not None:
            raise RuntimeError("Prepare and validate the same release first")
        self.healthy(state["slots"]["A"])
        processes.assert_free(self.config.backend_port, self.config.bind)
        processes.assert_free(self.config.frontend_port, self.config.bind)
        self.reload(state, "A")
        self.healthy(state["slots"]["A"], public=True)
        state["candidate"] = None
        state["lastGoodSha"] = state["slots"]["A"]["sha"]
        self.save(state, "idle")
        self.sync_legacy(state)
        return state

    def promote(self, sha, run_id=""):
        state = self.read()
        if state["phase"] not in ("idle", "failed") or state.get("intent") or not state.get("active"):
            raise RuntimeError("Recover or complete initial activation before promoting")
        old = state["active"]
        self.healthy(state["slots"][old], public=True)
        target = "B" if old == "A" else "A"
        # A previous warm slot is retired only when a subsequent deploy needs
        # its slot. This retains a fast rollback throughout the observation.
        if target in state["slots"]:
            self.retire(state, target)
        state["runId"] = run_id or "promote-" + secrets.token_hex(6)
        state["lastError"] = None
        self.save(state, "preparing")
        switched = False
        try:
            self.cancelled()
            slot = self.start_slot(state, target, sha)
            self.reload(state, old)  # Adds the candidate's internal frontend only.
            self.wait_ready(slot)
            self.save(state, "switching")
            self.cancelled()
            self.reload(state, target)
            switched = True
            state["previous"] = old
            state["switchedAt"] = utc()
            self.save(state, "observing")
            self.observe(state, slot)
            state["candidate"] = None
            state["lastGoodSha"] = sha
            state["history"].append({"from": state["slots"][old]["sha"], "to": sha, "at": utc()})
            self.save(state, "idle")
            self.sync_legacy(state)
            return state
        except Exception as error:
            # A response lost after /load is ambiguous. Reconcile the intent
            # against the real proxy before deciding whether A is still active.
            if state.get("intent"):
                self.reconcile_intent(state)
                switched = state.get("active") == target
            if switched:
                self.healthy(state["slots"][old])
                self.reload(state, old)
                state["previous"] = target
            state["lastError"] = str(error)
            state["candidate"] = None
            self.save(state, "failed")
            self.sync_legacy(state)
            # Preserve registered failed candidates until explicit recovery or
            # the next deployment; never kill an in-flight old request here.
            raise

    def reconcile_intent(self, state):
        intent = state.get("intent")
        if not intent:
            return
        gate = read_json(self.config.directory / "traffic.json")
        allowed = set(gate.get("allowed", []))
        known = set(intent["beforeGate"]["allowed"] + intent["afterGate"]["allowed"])
        if not allowed <= known:
            raise RuntimeError("Traffic/state ambiguous; preserving processes")
        after = intent["after"]
        # Finish a journaled switch only when its destination is healthy.
        try:
            if after:
                self.healthy(state["slots"][after])
            selected, chosen = after, intent["afterGate"]
        except (OSError, ValueError, RuntimeError):
            selected, chosen = intent["before"], intent["beforeGate"]
            if selected:
                self.healthy(state["slots"][selected])
        atomic_json(self.config.directory / "traffic.json", chosen)
        time.sleep(1.1)
        state["active"] = selected
        state["trafficHash"] = digest(chosen)
        state["intent"] = None
        self.save(state, "recovering")

    def rollback(self, sha=""):
        state = self.read()
        if state["phase"] != "idle" or state.get("intent"):
            raise RuntimeError("Recover interrupted operation before rollback")
        previous = state.get("previous")
        if not previous or previous not in state["slots"]:
            raise RuntimeError("No warm previous slot; use a reviewed redeploy")
        slot = state["slots"][previous]
        if sha and not slot["shaFull"].startswith(sha):
            raise RuntimeError("Requested rollback SHA is not the warm previous release")
        self.healthy(slot)
        old = state["active"]
        self.reload(state, previous)
        state["previous"] = old
        state["lastGoodSha"] = slot["sha"]
        state["candidate"] = None
        self.save(state, "idle")
        self.sync_legacy(state)
        return state

    def recover(self):
        state = self.read()
        if state.get("proxyStarting"):
            verify_binary(self.config)
            path = str(self.config.directory / "proxy-start.json")
            matches = []
            for process in psutil.process_iter(["pid", "exe", "cmdline"]):
                try:
                    if (process.info["exe"] and os.path.normcase(process.info["exe"]) == os.path.normcase(str(self.config.caddy))
                            and process.info["cmdline"] == [str(self.config.caddy), "run", "--config", path]):
                        matches.append(processes.identity(process.pid))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if len(matches) > 1:
                raise RuntimeError("Multiple proxy launch matches; manual diagnosis required")
            state["proxy"] = matches[0] if matches else None
            state["proxyStarting"] = False
            self.save(state)
        # Restart registered web-only instances from immutable physical paths;
        # never call legacy bootstrap or apply migrations during recovery.
        restart_names = list(dict.fromkeys(name for name in (state.get("active"), state.get("previous")) if name))
        if not restart_names and "A" in state["slots"]:
            restart_names = ["A"]
        for name in restart_names:
            slot = state["slots"][name]
            if not slot.get("process") or not processes.owned(slot["process"]):
                sha = slot["sha"]
                try:
                    self.start_slot(state, name, sha)
                    self.wait_ready(state["slots"][name])
                except Exception as error:
                    state.setdefault("recoveryWarnings", []).append(str(error))
        if not state.get("proxy") or not processes.owned(state["proxy"]):
            # Only restart if no unknown listener owns the admin port. Start
            # with internal routes; publish only after readiness succeeds.
            if state.get("intent"):
                raise RuntimeError("Proxy died during a pending switch; manual diagnosis required")
            self.start_proxy(state, build_config(self.config, state["slots"], None))
            state["proxyHash"] = digest(self.proxy.get())
            self.save(state)
        self.reconcile_intent(state)
        active = state.get("active")
        if active:
            try:
                self.healthy(state["slots"][active])
            except (OSError, ValueError, RuntimeError):
                previous = state.get("previous")
                if not previous or previous not in state["slots"]:
                    raise RuntimeError("No healthy registered fallback; processes preserved")
                self.healthy(state["slots"][previous])
                active = previous
            self.reload(state, active)
            state["candidate"] = None
            state["lastGoodSha"] = state["slots"][active]["sha"]
            self.save(state, "idle")
            self.sync_legacy(state)
        else:
            self.healthy(state["slots"]["A"])
            self.save(state, "prepared")
        (self.config.root / "cancel-requested.json").unlink(missing_ok=True)
        return state

    def sync_legacy(self, state):
        """Called under the same mutex; runtime journal remains authoritative."""
        active = state.get("active")
        if not active:
            return
        slot = state["slots"][active]
        path = self.config.root / "deploy-state.json"
        value = read_json(path) if path.exists() else {}
        value.update(activeSha=slot["sha"], lastGoodSha=slot["sha"], status="failed" if state["phase"] == "failed" else "idle",
                     lastError=state.get("lastError"), lockHolder=None,
                     previousSha=state["slots"].get(state.get("previous"), {}).get("sha"),
                     updatedAt=utc())
        atomic_json(path, value)
        marker = self.config.base / "logs" / f"PPLID_{self.config.environment}.deployed.json"
        atomic_json(marker, {"sha": slot["sha"], "shaFull": slot["shaFull"], "deployedSha": slot["sha"],
                             "deployedShaFull": slot["shaFull"], "deployedAt": utc()})
        status_path = self.config.base / "logs/deploy-status.json"
        status = read_json(status_path) if status_path.exists() else {"environments": {}}
        entry = status.setdefault("environments", {}).setdefault(self.config.environment, {})
        entry.update(phase="failed" if state["phase"] == "failed" else "healthy",
                     deployedSha=slot["sha"], deployedShaFull=slot["shaFull"],
                     lastDeployFinishedAt=utc(), lastDeployResult="failed" if state["phase"] == "failed" else "success",
                     lastDeployMessage=state.get("lastError") or "", updatePending=False)
        atomic_json(status_path, status)
        if os.name == "nt":
            # Junctions are compatibility pointers only. Use literal cmdlets
            # with arguments passed as environment values, never shell text.
            script = Path(__file__).parents[1] / "lib/junction.ps1"
            environment = dict(os.environ, BG_JUNCTION_LIB=str(script), BG_JUNCTION_ROOT=str(self.config.root),
                               BG_JUNCTION_TARGET=slot["release"])
            command = '. $env:BG_JUNCTION_LIB; Set-DirectoryJunction -LinkPath (Join-Path $env:BG_JUNCTION_ROOT "current") -TargetPath $env:BG_JUNCTION_TARGET'
            completed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                                       env=environment, capture_output=True, timeout=30,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            if completed.returncode:
                raise RuntimeError("Traffic is active but current junction reconciliation failed; run recover")

    def workers_start(self, sha):
        if self.config.bg.get("workersOwnershipConfirmed") is not True:
            raise RuntimeError("Confirm independent worker ownership in the migration runbook before starting business jobs")
        path = self.config.directory / "background.json"
        if path.exists():
            previous = read_json(path)
            if previous.get("process") and processes.owned(previous["process"]):
                return previous
            raise RuntimeError("Background record exists; inspect it before replacing business workers")
        release = self.config.release(sha)
        self.contract(release, sha)
        directory = self.config.directory / "background"
        directory.mkdir(parents=True, exist_ok=True)
        spec = {"release": str(release), "sha": sha, "environment": self.config.environment,
                "stopFile": str(directory / "stop"), "shaFile": str(directory / "deployed.json"),
                "readyFile": str(directory / "ready.json")}
        if Path(spec["stopFile"]).exists():
            raise RuntimeError("Previous background stop marker exists; review before restarting")
        spec_path = directory / "spec.json"
        atomic_json(spec_path, spec)
        python = str(release / "backend/.venv/Scripts/python.exe")
        # Read-only schema preflight runs with every background hook disabled.
        with (directory / "preflight.log").open("ab") as output:
            checked = subprocess.run([python, str(Path(__file__).with_name("slot_worker.py")), "--spec", str(spec_path), "--check-only"],
                                     stdout=output, stderr=subprocess.STDOUT, timeout=self.config.timeouts["startupSeconds"],
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        if checked.returncode:
            raise RuntimeError("Background schema preflight failed; no business jobs started")
        process = processes.launch([python, str(Path(__file__).with_name("background_worker.py")),
                                    "--spec", str(spec_path), "--pplid-supervised"], directory / "worker.log")
        record = {**spec, "process": processes.identity(process.pid), "startedAt": utc()}
        atomic_json(path, record)
        deadline = time.monotonic() + self.config.timeouts["startupSeconds"]
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Background supervisor exited; inspect worker.log")
            if Path(spec["readyFile"]).exists():
                return record
            time.sleep(.2)
        raise RuntimeError("Background supervisor readiness timed out; inspect worker.log before retrying")

    def shutdown(self):
        """Explicit web-only maintenance; business workers remain independent."""
        state = self.read()
        self.reconcile_intent(state)
        self.reload(state, None)
        state["candidate"] = None
        self.save(state, "stopping")
        for name in list(state["slots"]):
            self.retire(state, name)
        from .caddy import OPENER
        import urllib.request
        req = urllib.request.Request(self.proxy.url + "/stop", data=b"", headers={"Origin": self.proxy.url})
        with OPENER.open(req, timeout=self.config.timeouts["stopSeconds"]):
            pass
        if state.get("proxy"):
            processes.stop(state["proxy"], None, self.config.timeouts["stopSeconds"])
        state["proxy"] = None
        state["previous"] = None
        self.save(state, "stopped")
        return state

    def workers_stop(self):
        path = self.config.directory / "background.json"
        record = read_json(path)
        processes.stop(record["process"], Path(record["stopFile"]), self.config.timeouts["commandSeconds"])
        record["stoppedAt"] = utc()
        atomic_json(path, record)
        return record
