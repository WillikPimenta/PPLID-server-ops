# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from django.contrib.auth.models import AnonymousUser
from django.test import Client

from apps.cyber_psa.services.env_profile import get_env_profile
from apps.cyber_psa.services.glossary import append_glossary_to_markdown
from apps.cyber_psa.services.registry import load_findings, load_registry
from apps.cyber_psa.services.risk_overrides import apply_overrides_to_risks
from apps.cyber_psa.services.scanner import apply_governance_auto_checks, run_security_scan
from apps.psa.services.registry import load_last_health_report, load_registry as load_portal_registry


def _status_label(status: str) -> str:
    labels = {
        "open": "Aberto",
        "resolved": "Resolvido",
        "accepted": "Aceito",
        "implemented": "Implementado",
        "partial": "Parcial",
        "review": "Em revisão",
    }
    return labels.get(status, status)


def _governance_rows(findings: dict | None, registry: dict) -> list[dict]:
    if findings and findings.get("governance"):
        return findings["governance"]
    scan_items = run_security_scan(Client(), AnonymousUser())
    return apply_governance_auto_checks(registry.get("governance") or [], scan_items)


def _ops_health_summary() -> dict | None:
    report = load_last_health_report()
    if not report:
        return None
    summary = report.get("summary") or {}
    return {
        "generated_at": report.get("generated_at"),
        "passed": summary.get("passed", 0),
        "total": summary.get("total", 0),
        "suites": summary.get("suites"),
    }


def build_governance_markdown(*, username: str) -> str:
    registry = load_registry()
    portal = load_portal_registry()
    findings = load_findings()
    meta = registry.get("meta") or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ops_health = _ops_health_summary()

    lines: list[str] = [
        f"# {meta.get('title', 'PSA Cyber — PPLID')}",
        "",
        f"**Gerado em:** {now}",
        f"**Gerado por:** {username}",
        f"**Perfil de ambiente:** {get_env_profile()}",
        f"**Produto:** {meta.get('product', 'Portal PPLID')}",
        "",
        "> " + str(meta.get("disclaimer", "")),
        "",
        "---",
        "",
        "## 1. Resumo executivo",
        "",
    ]

    if findings:
        summary = findings.get("summary") or {}
        lines.extend(
            [
                f"- Checks automáticos: **{summary.get('passed', 0)}/{summary.get('total', 0)} OK**",
                f"- Riscos abertos: **{summary.get('open_risks', 0)}**",
                f"- Último scan: {findings.get('generated_at', '—')} por {findings.get('generated_by', '—')}",
                "",
            ]
        )
    else:
        lines.extend(["- Nenhum scan registrado ainda. Execute **Rodar assessment** no console.", ""])

    if ops_health:
        lines.append(
            f"- Console Ops health: **{ops_health['passed']}/{ops_health['total']} OK** "
            f"({ops_health.get('generated_at', '—')})"
        )
        lines.append("")

    portal_modules = portal.get("modules") or []
    live = sum(1 for m in portal_modules if m.get("status") == "live")
    placeholder = sum(1 for m in portal_modules if m.get("status") == "placeholder")
    lines.extend(
        [
            f"- Módulos do portal: **{live} live**, **{placeholder} placeholder**",
            "",
            "---",
            "",
            "## 2. Material para apresentação à Segurança da Informação",
            "",
        ]
    )

    for section in registry.get("presentation") or []:
        lines.append(f"### {section.get('title', section.get('id', ''))}")
        lines.append("")
        for bullet in section.get("bullets") or []:
            lines.append(f"- {bullet}")
        lines.append("")

    lines.extend(["---", "", "## 3. Controles de governança", ""])
    lines.append("| ID | Categoria | Controle | Status | Scan |")
    lines.append("|---|---|---|---|---|")
    for ctrl in _governance_rows(findings, registry):
        scan_col = "—"
        if "scan_ok" in ctrl:
            scan_col = "OK" if ctrl["scan_ok"] else "Falha"
            if ctrl.get("scan_detail"):
                scan_col += f" ({ctrl['scan_detail']})"
        lines.append(
            f"| {ctrl.get('id', '')} | {ctrl.get('category', '')} | {ctrl.get('title', '')} | "
            f"{_status_label(str(ctrl.get('status', '')))} | {scan_col} |"
        )
    lines.append("")

    lines.extend(["---", "", "## 4. Riscos", ""])
    risks = apply_overrides_to_risks((findings or {}).get("risks") or (registry.get("manual_risks") or []))
    if not risks:
        lines.append("_Sem riscos registrados._")
    else:
        lines.append("| ID | Título | Severidade | Status | Origem | Mitigação |")
        lines.append("|---|---|---|---|---|---|")
        for risk in risks:
            lines.append(
                f"| {risk.get('id', '')} | {risk.get('title', '')} | {risk.get('severity', '')} | "
                f"{_status_label(str(risk.get('status', '')))} | {risk.get('source', 'manual')} | "
                f"{risk.get('mitigation', '')} |"
            )
    lines.append("")

    if findings and findings.get("scan_items"):
        lines.extend(["---", "", "## 5. Detalhe do último scan", ""])
        for item in findings["scan_items"]:
            mark = "OK" if item.get("ok") else "FALHA"
            lines.append(f"- **[{mark}]** `{item.get('id', '')}` — {item.get('title', '')}")
            if item.get("extra"):
                lines.append(f"  - {item['extra']}")
        lines.append("")

    if ops_health and ops_health.get("suites"):
        lines.extend(["---", "", "## 6. Console Ops — saúde por suite", ""])
        for suite, stats in ops_health["suites"].items():
            lines.append(f"- **{suite}**: {stats.get('passed', 0)}/{stats.get('total', 0)} OK")
        lines.append("")

    lines.extend(["---", "", "## 7. Documentação de referência", ""])
    for doc in registry.get("documents") or []:
        lines.append(f"- **{doc.get('label', '')}** — `{doc.get('path', '')}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_Documento gerado pelo PSA Cyber do Portal PPLID. Anexar ao processo formal de SI._")
    lines.append("")
    append_glossary_to_markdown(lines)

    return "\n".join(lines)
