"""Testes do normalizador de rotas do ops_monitoring."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from apps.ops_monitoring.route_normalizer import normalize_route, should_skip_path  # noqa: E402


class RouteNormalizerTests(unittest.TestCase):
    def test_skip_static_and_health(self) -> None:
        self.assertTrue(should_skip_path("/static/app.js"))
        self.assertTrue(should_skip_path("/api/v1/health/"))

    def test_normalize_uuid_and_id(self) -> None:
        path = "/api/v1/items/550e8400-e29b-41d4-a716-446655440000/comments/99/"
        normalized = normalize_route(path)
        self.assertIn("{uuid}", normalized)
        self.assertIn("{id}", normalized)


if __name__ == "__main__":
    unittest.main()
