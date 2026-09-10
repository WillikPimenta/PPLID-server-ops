# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GLOSSARY_PATH = Path(__file__).resolve().parent.parent / "glossary.json"


def load_glossary() -> dict:
    if not GLOSSARY_PATH.is_file():
        return {"terms": [], "checks": {}, "control_hints": {}}
    data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _terms_by_id(glossary: dict) -> dict[str, dict]:
    return {t["id"]: t for t in (glossary.get("terms") or []) if t.get("id")}


def enrich_check_item(item: dict[str, Any], glossary: dict | None = None) -> dict[str, Any]:
    glossary = glossary or load_glossary()
    checks = glossary.get("checks") or {}
    row = dict(item)
    entry = checks.get(str(row.get("id", "")))
    if entry:
        row["plain_title"] = entry.get("plain_title", row.get("title"))
        row["plain_what"] = entry.get("plain_what", "")
        row["plain_why"] = entry.get("plain_why", "")
        row["plain_fix"] = entry.get("plain_fix", row.get("remediation", ""))
        row["related_terms"] = entry.get("related_terms") or []
    else:
        row["plain_title"] = row.get("title", "")
        row["plain_what"] = row.get("description", "")
        row["plain_why"] = ""
        row["plain_fix"] = row.get("remediation", "")
        row["related_terms"] = _guess_terms_from_text(
            f"{row.get('title', '')} {row.get('description', '')}", glossary
        )
    return row


def enrich_governance_control(ctrl: dict[str, Any], glossary: dict | None = None) -> dict[str, Any]:
    glossary = glossary or load_glossary()
    hints = glossary.get("control_hints") or {}
    by_id = _terms_by_id(glossary)
    row = dict(ctrl)
    hint = hints.get(str(row.get("id", ""))) or {}
    related = hint.get("related_terms") or _guess_terms_from_text(
        f"{row.get('title', '')} {row.get('description', '')} {row.get('evidence', '')}", glossary
    )
    row["related_terms"] = related
    row["plain_summary"] = row.get("description", "")
    term_snippets = []
    for tid in related:
        term = by_id.get(tid)
        if term:
            term_snippets.append(f"{term.get('term')}: {term.get('plain', '')}")
    if term_snippets:
        row["plain_terms"] = term_snippets
    return row


def _guess_terms_from_text(text: str, glossary: dict) -> list[str]:
    if not text:
        return []
    upper = text.upper()
    found: list[str] = []
    for term in glossary.get("terms") or []:
        label = str(term.get("term", "")).upper()
        if label and label in upper:
            found.append(str(term.get("id")))
    return found


def enrich_unauth_check(item: dict[str, Any], glossary: dict | None = None) -> dict[str, Any]:
    row = enrich_check_item(item, glossary)
    if row.get("id", "").startswith("CYBER-AUTO-UNAUTH-"):
        path = row.get("extra", "") or row.get("title", "")
        row["plain_title"] = "Dado sensível acessível sem login?"
        row["plain_what"] = f"Testamos se a URL responde sem autenticação ({path})."
        row["plain_why"] = "Status 200 para visitante anônimo pode indicar vazamento de dados."
        row["plain_fix"] = "Exigir login (401/403) em endpoints operacionais."
        row["related_terms"] = ["auth", "api-endpoint"]
    elif row.get("id", "").startswith("CYBER-AUTO-POST-"):
        row["plain_title"] = "Ação perigosa bloqueada sem login?"
        row["plain_what"] = "Tentamos enviar upload/sync sem estar logado."
        row["plain_why"] = "Importação de Falhas apaga e recria tabelas — não pode ser pública."
        row["plain_fix"] = "Manter 401/403 para usuário anônimo."
        row["related_terms"] = ["auth", "can-sync"]
    return row


def enrich_scan_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    glossary = load_glossary()
    result = []
    for item in items:
        if str(item.get("id", "")).startswith(("CYBER-AUTO-UNAUTH-", "CYBER-AUTO-POST-")):
            result.append(enrich_unauth_check(item, glossary))
        else:
            result.append(enrich_check_item(item, glossary))
    return result


def enrich_governance(controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    glossary = load_glossary()
    return [enrich_governance_control(c, glossary) for c in controls]


def glossary_for_api() -> dict[str, Any]:
    glossary = load_glossary()
    return {
        "meta": glossary.get("meta") or {},
        "terms": glossary.get("terms") or [],
    }


def append_glossary_to_markdown(lines: list[str]) -> None:
    glossary = load_glossary()
    lines.extend(["---", "", "## Glossário — termos em português claro", ""])
    meta = glossary.get("meta") or {}
    if meta.get("subtitle"):
        lines.append(f"_{meta['subtitle']}_")
        lines.append("")
    for term in glossary.get("terms") or []:
        lines.append(f"### {term.get('term')} ({term.get('full_name', '')})")
        lines.append("")
        lines.append(str(term.get("plain", "")))
        if term.get("analogy"):
            lines.append("")
            lines.append(f"*Analogia:* {term['analogy']}")
        lines.append("")
