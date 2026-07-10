"""Detecta regressao de mojibake nos JS do ops-console."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent.parent / "public" / "js"
BAD_PATTERNS = [
    re.compile(r"Ô"),
    re.compile(r"├"),
    re.compile(r"Ã[^a-zA-Z]"),
]


class EncodingJsTests(unittest.TestCase):
    def test_public_js_without_mojibake(self) -> None:
        offenders: list[str] = []
        skip = {"utils.js"}
        for path in sorted(JS_DIR.glob("*.js")):
            if path.name in skip:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in BAD_PATTERNS:
                if pattern.search(text):
                    offenders.append(path.name)
                    break
        self.assertEqual(offenders, [], f"Mojibake encontrado em: {offenders}")


if __name__ == "__main__":
    unittest.main()
