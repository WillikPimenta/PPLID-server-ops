"""Normaliza descrição Jira (HTML / ADF) para exibição segura."""

from __future__ import annotations

import re
from html import unescape
from typing import Any

_BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}
_INLINE_BREAK = {"br", "li"}


def _adf_collect_text(node: Any, parts: list[str]) -> None:
    if isinstance(node, str):
        if node.strip():
            parts.append(node)
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "text":
        text = str(node.get("text") or "")
        marks = node.get("marks") or []
        if any(m.get("type") == "link" for m in marks if isinstance(m, dict)):
            for mark in marks:
                if isinstance(mark, dict) and mark.get("type") == "link":
                    href = (mark.get("attrs") or {}).get("href") or ""
                    if href:
                        text = f"{text} ({href})"
        if text:
            parts.append(text)
        return
    node_type = str(node.get("type") or "")
    if node_type in _BLOCK_TAGS and parts and not parts[-1].endswith("\n"):
        parts.append("\n")
    for child in node.get("content") or []:
        _adf_collect_text(child, parts)
    if node_type in _INLINE_BREAK:
        parts.append("\n")


def _adf_to_plain(raw: dict) -> str:
    parts: list[str] = []
    _adf_collect_text(raw, parts)
    text = "".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"<\s*(p|span|div|br|ul|ol|li|h[1-6]|strong|em|a)\b", text, re.I))


def _sanitize_html(html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
    cleaned = re.sub(r"(?is)<(script|style)[^>]*/?>", "", cleaned)
    cleaned = re.sub(r'(?i)\son\w+\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', "", cleaned)
    cleaned = re.sub(r"(?i)javascript:", "", cleaned)
    return cleaned.strip()


def _html_to_plain(html: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_jira_description(raw: Any) -> dict[str, str]:
    if raw is None:
        return {"plain": "", "html": ""}
    if isinstance(raw, dict):
        plain = _adf_to_plain(raw)
        html = ""
        if plain:
            paragraphs = [p.strip() for p in plain.split("\n\n") if p.strip()]
            html = "".join(f"<p>{unescape(p.replace(chr(10), '<br>'))}</p>" for p in paragraphs)
        return {"plain": plain, "html": html}
    text = str(raw).strip()
    if not text:
        return {"plain": "", "html": ""}
    if _looks_like_html(text):
        html = _sanitize_html(text)
        plain = _html_to_plain(html)
        return {"plain": plain, "html": html}
    return {"plain": text, "html": f"<p>{unescape(text).replace(chr(10), '<br>')}</p>"}
