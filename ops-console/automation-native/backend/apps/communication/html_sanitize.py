"""Sanitização mínima do HTML de notícias (sem dependências extras)."""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset(
    {
        "p",
        "br",
        "div",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "span",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "a",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
    }
)
BLOCK_STYLE_SUFFIXES = frozenset({"info", "warning", "idea", "grey", "dashed"})
STYLE_PROPS = frozenset(
    {"color", "background-color", "font-size", "font-weight", "font-style", "text-decoration", "width"}
)
STYLE_VALUE_RE = re.compile(
    r"^(?:#[0-9a-f]{3,8}|\d+(?:\.\d+)?(?:px|em|rem|%))$",
    re.IGNORECASE,
)
RGB_VALUE_RE = re.compile(
    r"^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(?:\s*,\s*(?:0?\.\d+|1))?\s*\)$",
    re.IGNORECASE,
)
FONT_WEIGHT_RE = re.compile(r"^(?:normal|bold|bolder|lighter|[1-9]00)$", re.IGNORECASE)
FONT_STYLE_RE = re.compile(r"^(?:normal|italic|oblique)$", re.IGNORECASE)
TEXT_DECORATION_RE = re.compile(r"^(?:none|underline|line-through)$", re.IGNORECASE)
SAFE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _sanitize_style(raw: str) -> str:
    parts: list[str] = []
    for chunk in (raw or "").split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key not in STYLE_PROPS:
            continue
        if (
            STYLE_VALUE_RE.match(value)
            or RGB_VALUE_RE.match(value)
            or (key == "font-weight" and FONT_WEIGHT_RE.match(value))
            or (key == "font-style" and FONT_STYLE_RE.match(value))
            or (key == "text-decoration" and TEXT_DECORATION_RE.match(value))
        ):
            parts.append(f"{key}: {value}")
    return "; ".join(parts)


def _sanitize_class(raw: str) -> str:
    allowed: list[str] = []
    for token in (raw or "").split():
        token = token.strip()
        if not token:
            continue
        if token in {"news-block", "news-table"}:
            allowed.append(token)
            continue
        if token.startswith("news-block--"):
            suffix = token.removeprefix("news-block--")
            if suffix in BLOCK_STYLE_SUFFIXES:
                allowed.append(token)
    return " ".join(dict.fromkeys(allowed))


def _sanitize_href(raw: str) -> str:
    href = (raw or "").strip()
    if not href or not SAFE_URL_RE.match(href):
        return ""
    return href


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def _append_open(self, tag: str, attrs: dict[str, str]) -> None:
        if not attrs:
            self._parts.append(f"<{tag}>")
            return
        serialized = " ".join(
            f'{key}="{escape(value, quote=True)}"' for key, value in attrs.items()
        )
        self._parts.append(f"<{tag} {serialized}>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in ALLOWED_TAGS:
            return
        attr_map = {name.lower(): (value or "") for name, value in attrs}
        clean_attrs: dict[str, str] = {}

        if tag == "a":
            href = _sanitize_href(attr_map.get("href", ""))
            if not href:
                return
            clean_attrs["href"] = href
            clean_attrs["target"] = "_blank"
            clean_attrs["rel"] = "noopener noreferrer"
            self._append_open(tag, clean_attrs)
            return

        if tag in {"div", "p"} or tag.startswith("h") and tag[1:].isdigit():
            cls = _sanitize_class(attr_map.get("class", ""))
            if cls:
                clean_attrs["class"] = cls

        if tag == "table":
            cls = _sanitize_class(attr_map.get("class", "") or "news-table")
            if cls:
                clean_attrs["class"] = cls

        if tag in {"span", "p", "div", "td", "th"} or tag.startswith("h") and tag[1:].isdigit():
            style = _sanitize_style(attr_map.get("style", ""))
            if style:
                clean_attrs["style"] = style

        if tag == "br":
            self._parts.append("<br>")
            return

        self._append_open(tag, clean_attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ALLOWED_TAGS and tag != "br":
            self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self._parts.append(escape(data))

    def get_html(self) -> str:
        return "".join(self._parts).strip()


def sanitize_news_html(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "<" not in raw:
        paragraphs = [line.strip() for line in raw.splitlines() if line.strip()]
        if not paragraphs:
            return escape(raw)
        return "".join(f"<p>{escape(part)}</p>" for part in paragraphs)

    parser = _Sanitizer()
    parser.feed(raw)
    parser.close()
    cleaned = parser.get_html()
    plain = re.sub(r"<[^>]+>", "", cleaned).replace("&nbsp;", " ").strip()
    return cleaned if plain else ""
