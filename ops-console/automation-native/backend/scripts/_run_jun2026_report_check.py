# -*- coding: utf-8 -*-
"""Gera report jun/2026 e confere KPIs nos HTML."""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from report_falhas.config_report import require_excel_path

EXCEL = Path(require_excel_path())

EXPECTED = {
    "brasilia": {"total": 25, "oficial": 11},
    "sao_carlos": {"total": 31, "oficial": 19},
    "consolidado": {"total": 56, "oficial": 30},
}


def extract_kpis(html: str) -> dict[str, int | str | None]:
    period = None
    m_per = re.search(
        r"Falhas com Data de An[aá]lise no per[ií]odo \((\d{2}/\d{2}/\d{4}) → (\d{2}/\d{2}/\d{4})\)",
        html,
    )
    if m_per:
        period = f"{m_per.group(1)} → {m_per.group(2)}"

    def _count_in_fragment(fragment: str, label: str) -> int | None:
        m_sub = re.search(
            r"Falhas com Data de An[aá]lise no per[ií]odo[^<]*<b>(\d+)</b>",
            fragment,
        )
        if m_sub:
            return int(m_sub.group(1))
        pat = (
            rf"{re.escape(label)}</div>"
            r"<div style='font-size:22px;font-weight:700;color:#212529;'>(\d+)</div>"
        )
        m = re.search(pat, fragment)
        return int(m.group(1)) if m else None

    oficial_html = html
    total_html = html
    pages_match = re.search(r"var _pages = (\{.*?\});\s*\n", html, flags=re.DOTALL)
    if pages_match:
        try:
            import json

            pages = json.loads(pages_match.group(1))
            oficial_html = (pages.get("oficial") or {}).get("body") or html
            total_html = (pages.get("total") or {}).get("body") or html
        except Exception:
            pass

    return {
        "period": period,
        "total": _count_in_fragment(total_html, "Falhas (por Data de Análise)"),
        "oficial": _count_in_fragment(
            oficial_html,
            "Falhas (por Data de Análise) - Métrica Oficial",
        )
        or _count_in_fragment(oficial_html, "Falhas (por Data de Análise)"),
    }


def main() -> int:
    if not EXCEL.is_file():
        print(f"FAIL: Excel não encontrado: {EXCEL}")
        return 1

    from report_falhas.legacy_main import run_report

    settings = {
        "falhas_excel_path": str(EXCEL),
        "mes_referencia": "06/2026",
        "usar_periodo_custom": True,
        "custom_start": "01/06/2026",
        "custom_end": "30/06/2026",
        "gerar_consolidado": True,
        "preview_email": False,
        "gerar_executivo": True,
        "salvar_html_individuais": False,
    }
    print("Gerando report jun/2026...")
    run_report(settings)

    out_base = EXCEL.parent
    period_day = "2026-06-30"

    print(f"\nConferindo HTML em: {out_base}")
    failed = 0
    for scope, exp in EXPECTED.items():
        scope_dir = out_base / scope / period_day
        if not scope_dir.is_dir():
            print(f"FAIL {scope}: pasta não encontrada ({scope_dir})")
            failed += 1
            continue
        html_files = sorted(scope_dir.glob("relatorio_*_ABAS_*.html"), reverse=True)
        if not html_files:
            html_files = sorted(scope_dir.glob("**/relatorio_*_TOTAL_*.html"), reverse=True)
        if not html_files:
            print(f"FAIL {scope}: nenhum HTML gerado")
            failed += 1
            continue
        path = html_files[0]
        text = path.read_text(encoding="utf-8", errors="replace")
        kpis = extract_kpis(text)
        ok_total = kpis["total"] == exp["total"]
        ok_of = kpis["oficial"] == exp["oficial"]
        mark = "OK" if ok_total and ok_of else "FAIL"
        if mark == "FAIL":
            failed += 1
        print(
            f"{mark} {scope:12} arquivo={path.name}\n"
            f"     periodo={kpis['period']}\n"
            f"     total HTML={kpis['total']} esperado={exp['total']} | "
            f"oficial HTML={kpis['oficial']} esperado={exp['oficial']}"
        )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
