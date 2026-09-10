# -*- coding: utf-8 -*-
import json
import re
import sys
from pathlib import Path

pat = re.compile(
    r"Falhas com Data de An[aá]lise no per[ií]odo \((\d{2}/\d{2}/\d{4}) → (\d{2}/\d{2}/\d{4})\): <b>(\d+)</b>"
)

def parse_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    out = {"file": path.name, "visible": None, "tabs": {}}
    m_vis = pat.search(text.split("var _pages", 1)[0])
    if m_vis:
        out["visible"] = {"period": f"{m_vis.group(1)} → {m_vis.group(2)}", "count": int(m_vis.group(3))}
    m = re.search(r"var _pages = (\{.*\})\s*;\s*</script>", text, re.S)
    if not m:
        return out
    data = json.loads(m.group(1))
    for tab, payload in data.items():
        body = payload.get("body", "")
        hits = pat.findall(body)
        if hits:
            start, end, count = hits[0]
            out["tabs"][tab] = {"period": f"{start} → {end}", "count": int(count)}
    return out


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from report_falhas.config_report import resolve_output_dir

    base = resolve_output_dir()
    scopes = ["brasilia", "sao_carlos", "consolidado"]
    for scope in scopes:
        files = sorted((base / scope / "2026-06-30").glob("relatorio_*_ABAS_2026-07-01_17-43.html"), reverse=True)
        if not files:
            print(f"{scope}: sem HTML jun/2026")
            continue
        info = parse_file(files[0])
        print(f"\n=== {scope} ===")
        print(f"  aba visível (oficial): {info['visible']}")
        for tab, val in sorted(info["tabs"].items()):
            print(f"  aba {tab}: {val}")


if __name__ == "__main__":
    main()
