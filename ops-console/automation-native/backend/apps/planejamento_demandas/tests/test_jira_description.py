from django.test import SimpleTestCase

from apps.planejamento_demandas.services.jira_description import normalize_jira_description


class JiraDescriptionTests(SimpleTestCase):
    def test_html_to_plain_and_html(self):
        raw = '<p dir="auto"><span style="font-size:12pt">Olá, pessoal.</span></p>'
        out = normalize_jira_description(raw)
        self.assertIn("Olá, pessoal.", out["plain"])
        self.assertIn("<p", out["html"])
        self.assertNotIn("<script", out["html"].lower())

    def test_plain_text(self):
        out = normalize_jira_description("Texto simples\ncom quebra")
        self.assertEqual(out["plain"], "Texto simples\ncom quebra")
        self.assertIn("Texto simples", out["html"])
