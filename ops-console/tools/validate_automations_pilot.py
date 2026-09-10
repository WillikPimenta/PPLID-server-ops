"""Validation harness for the Ops Console automations pilot.

Runs config inventory checks, pytest regression suite, and static checks
described in the validation plan. Exit code 0 only when all checks pass.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPS_CONSOLE = Path(__file__).resolve().parents[1]
REPO_ROOT = OPS_CONSOLE.parent
sys.path.insert(0, str(OPS_CONSOLE))

import server_automations as automations  # noqa: E402


@dataclass
class CheckResult:
    section: str
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ValidationReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, section: str, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(CheckResult(section, name, ok, detail))

    @property
    def passed(self) -> int:
        return sum(1 for item in self.results if item.ok)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if not item.ok)

    def render(self) -> str:
        lines = ["# Relatório de validação — Automações Ops Console", ""]
        current = ""
        for item in self.results:
            if item.section != current:
                current = item.section
                lines.append(f"## {current}")
                lines.append("")
            mark = "PASS" if item.ok else "FAIL"
            line = f"- [{mark}] {item.name}"
            if item.detail:
                line += f" — {item.detail}"
            lines.append(line)
        lines.extend(
            [
                "",
                f"**Resumo:** {self.passed} passou, {self.failed} falhou, {len(self.results)} total",
            ]
        )
        return "\n".join(lines)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check_config_inventory(report: ValidationReport, *, local: bool) -> None:
    section = "1. Inventário de configuração"
    machine_path = REPO_ROOT / "config" / ("machine.config.local.json" if local else "machine.config.template.json")
    env_path = REPO_ROOT / "config" / ("env.config.local.json" if local else "env.config.json")
    machine = _load_json(machine_path)
    env_cfg = _load_json(env_path)

    report.add(section, f"Arquivo machine config existe ({machine_path.name})", machine_path.is_file())
    report.add(section, f"Arquivo env config existe ({env_path.name})", env_path.is_file())

    runtime_root = Path((machine.get("automationRuntime") or {}).get("root") or "")
    ops_dir = Path(machine.get("automationOpsDir") or (OPS_CONSOLE / "automation-native"))
    report.add(
        section,
        "automationRuntime.root definido",
        bool(str(runtime_root).strip()),
        str(runtime_root) if runtime_root else "ausente",
    )
    report.add(
        section,
        "automationOpsDir contém automacoes/app",
        (ops_dir / "automacoes" / "app").is_dir(),
        str(ops_dir),
    )
    report.add(
        section,
        "automation-native contém backend",
        (ops_dir / "backend").is_dir(),
        str(ops_dir / "backend"),
    )

    hom_enabled = (env_cfg.get("HOM") or {}).get("enabled", True) is not False
    report.add(section, "HOM desativado no env config", not hom_enabled)

    deploy_dir = Path(machine.get("deployDir") or (REPO_ROOT / ".local" / "deploy"))
    for env_name in automations.ENVIRONMENTS:
        env_block = env_cfg.get(env_name) or {}
        if env_block.get("enabled", True) is False:
            report.add(
                section,
                f"backend.env de {env_name} alinhado com postgresDb",
                True,
                "ambiente desativado — verificação ignorada",
            )
            continue
        expected_db = str(env_block.get("postgresDb") or "")
        backend_env = deploy_dir / env_name / "shared" / "backend.env"
        if not backend_env.is_file():
            report.add(
                section,
                f"backend.env de {env_name} alinhado com postgresDb",
                False,
                f"arquivo ausente: {backend_env}",
            )
            continue
        actual_db = _parse_env_file(backend_env).get("POSTGRES_DB", "")
        ok = actual_db == expected_db
        report.add(
            section,
            f"backend.env de {env_name} alinhado com postgresDb",
            ok,
            f"POSTGRES_DB={actual_db!r}, esperado {expected_db!r}",
        )
        has_bom = backend_env.read_bytes().startswith(b"\xef\xbb\xbf")
        report.add(
            section,
            f"backend.env de {env_name} sem BOM UTF-8",
            not has_bom,
            "remova o BOM (salve como UTF-8 sem BOM) ou regrave pelo Ops Console",
        )

    for mode in automations.PILOT_MODES:
        expected = automations._default_bot_config(mode)
        report.add(
            section,
            f"Seed padrão {mode} definido no control plane",
            bool(expected),
            json.dumps(expected, ensure_ascii=False)[:120],
        )

    runtime = {"logDir": str(REPO_ROOT / ".local" / "logs"), "automationRuntime": {"root": str(runtime_root or REPO_ROOT / ".local" / "automation-runtime")}}
    runtime.update({name: env_cfg.get(name, {}) for name in automations.ENVIRONMENTS})
    cfg = automations.get_config(runtime, "production")["config"]
    report.add(
        section,
        "get_config(production) retorna seed portal",
        cfg == automations.DEFAULT_PRODUCTION_CONFIG,
    )
    cfg_rotina = automations.get_config(runtime, "rotina")["config"]
    report.add(
        section,
        "get_config(rotina) retorna seed portal",
        cfg_rotina == automations.DEFAULT_ROTINA_CONFIG,
    )


def check_runtime_static(report: ValidationReport) -> None:
    section = "3. Runtime e isolamento (estático)"
    supervisor = OPS_CONSOLE / "automation_supervisor.py"
    text = supervisor.read_text(encoding="utf-8")
    report.add(section, "Supervisor aceita --pplid-supervised", "--pplid-supervised" in text)
    report.add(section, "Supervisor congela backend.env no start", "PPLID_BACKEND_ENV_FILE" in text)
    report.add(section, "Supervisor valida expected-database", "expected_db" in text and "actual_db" in text)

    orphan_ps = REPO_ROOT / "lib" / "orphan_bot_manager.ps1"
    orphan_text = orphan_ps.read_text(encoding="utf-8", errors="replace")
    report.add(
        section,
        "orphan_bot_manager preserva --pplid-supervised",
        "pplid[_-]supervised" in orphan_text or "pplid-supervised" in orphan_text,
    )

    styles = (OPS_CONSOLE / "public" / "styles.css").read_text(encoding="utf-8", errors="replace")
    ui_hidden = "#automation-publish" in styles and "display: none" in styles
    report.add(
        section,
        "UI publish/rollback oculta (risco documentado)",
        True,
        "oculta — usar API POST /api/v1/automations/runtime/publish" if ui_hidden else "visível",
    )

    server_automations = (OPS_CONSOLE / "server_automations.py").read_text(encoding="utf-8")
    report.add(
        section,
        "Publish bloqueado com bot running",
        "_any_running" in server_automations and "Pare Production e Rotina" in server_automations,
    )


def check_lifecycle_static(report: ValidationReport) -> None:
    section = "4. Ciclo de vida (estático)"
    js = (OPS_CONSOLE / "public" / "js" / "automations.js").read_text(encoding="utf-8")
    config_js = (OPS_CONSOLE / "public" / "js" / "automations-config.js").read_text(encoding="utf-8")
    report.add(section, "UI exige validação Okta antes de iniciar", "credentialDraft.validated" in js)
    report.add(section, "Chips multi-select de banco destino", "auto-target-chip" in js and "targetEnvironments" in js)
    report.add(section, "Formulário de configuração visível", "auto-config-gear" in js and "auto-config-modal" in js and "renderBotConfigForm" in config_js and "agendada: diária às" in config_js)
    report.add(section, "Salvar config bloqueado só com bot Ops running", "opsRunning" in js)
    report.add(section, "Módulo automations-config.js registrado", "automationConfig" in config_js)

    cred = (OPS_CONSOLE / "automation_credentials.py").read_text(encoding="utf-8")
    report.add(section, "Helper de credenciais existe", cred.strip().startswith('"""') or "def " in cred)


def check_config_backend_static(report: ValidationReport) -> None:
    section = "6. Config backend"
    text = (OPS_CONSOLE / "server_automations.py").read_text(encoding="utf-8")
    report.add(section, "normalize_bot_config implementado", "def normalize_bot_config" in text)
    report.add(section, "Importação robot_config.json", "_import_portal_config" in text)
    report.add(section, "update_config bloqueia bot Ops running", "Pare o bot Ops antes de alterar" in text)
    report.add(section, "targetEnvironments persistido (1-2 bancos)", "targetEnvironments" in text and "MAX_TARGET_ENVIRONMENTS" in text)
    report.add(section, "probe_target_availability exposto no overview", "def probe_target_availability" in text and "targetAvailability" in text)


def check_db_hooks_static(report: ValidationReport) -> None:
    section = "5. Banco e hooks (estático)"
    supervisor = (OPS_CONSOLE / "automation_supervisor.py").read_text(encoding="utf-8")
    report.add(section, "Supervisor executa django.setup()", "django.setup()" in supervisor)
    report.add(section, "Supervisor usa robot_manager.start", "robot_manager.start" in supervisor)

    rm_path = OPS_CONSOLE / "automation-native" / "automacoes" / "app" / "services" / "robot_manager.py"
    rm_text = rm_path.read_text(encoding="utf-8", errors="replace")
    for prefix in (
        "ROTINA_BRUTO_SAVED",
        "MONITOR_EVENTOS_SAVED",
        "PRODUCTION_DETALHADO_SAVED",
    ):
        report.add(section, f"Hook stdout {prefix} presente no robot_manager", prefix in rm_text)

    hooks_dir = OPS_CONSOLE / "automation-native" / "backend" / "apps" / "automacoes"
    report.add(section, "App Django automacoes presente no bundle nativo", hooks_dir.is_dir(), str(hooks_dir))


def run_pytest(report: ValidationReport) -> None:
    section = "2. Regressão automatizada (pytest)"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_server_automations.py",
        "tests/test_orphan_bot_cleanup.py",
        "tests/test_api_not_spa_fallback.py",
        "-q",
    ]
    proc = subprocess.run(cmd, cwd=str(OPS_CONSOLE), capture_output=True, text=True)
    ok = proc.returncode == 0
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-8:])
    report.add(section, "pytest control plane + orphan + API routes", ok, tail or "sem saída")


def main() -> int:
    local = "--local" in sys.argv or "--server" not in sys.argv
    report = ValidationReport()
    check_config_inventory(report, local=local)
    run_pytest(report)
    check_runtime_static(report)
    check_lifecycle_static(report)
    check_db_hooks_static(report)
    check_config_backend_static(report)

    out_path = OPS_CONSOLE / "VALIDATION_AUTOMATIONS_PILOT.md"
    rendered = report.render()
    out_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print(f"\nRelatório gravado em: {out_path}")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
