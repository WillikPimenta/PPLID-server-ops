# -*- coding: utf-8 -*-
from django.test import TestCase

from apps.cyber_psa.services.glossary import enrich_check_item, enrich_scan_items, glossary_for_api, load_glossary


class CyberGlossaryTests(TestCase):
    def test_glossary_loads_terms(self):
        glossary = load_glossary()
        self.assertGreater(len(glossary.get("terms") or []), 5)

    def test_enrich_cors_check(self):
        item = enrich_check_item({"id": "CYBER-AUTO-CORS", "title": "CORS permissivo", "description": "x", "remediation": "y", "ok": False})
        self.assertIn("plain_what", item)
        self.assertIn("cors", item.get("related_terms") or [])

    def test_glossary_for_api(self):
        payload = glossary_for_api()
        self.assertIn("terms", payload)
        terms = {t["term"] for t in payload["terms"]}
        self.assertIn("CORS", terms)
        self.assertIn("DRF", terms)

    def test_enrich_unauth_check(self):
        items = enrich_scan_items([
            {"id": "CYBER-AUTO-UNAUTH-api-v1-agents-", "title": "Endpoint", "description": "d", "remediation": "r", "ok": True, "extra": "status=403"}
        ])
        self.assertIn("plain_what", items[0])
