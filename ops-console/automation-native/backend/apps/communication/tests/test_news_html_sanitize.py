from django.test import SimpleTestCase

from apps.communication.html_sanitize import sanitize_news_html


class NewsHtmlSanitizeTests(SimpleTestCase):
    def test_strips_script_and_keeps_basic_formatting(self):
        raw = (
            '<p>Olá <strong>mundo</strong></p>'
            '<script>alert("x")</script>'
            '<p style="color: #112233; font-size: 18px">Texto</p>'
        )
        cleaned = sanitize_news_html(raw)
        self.assertIn("<strong>mundo</strong>", cleaned)
        self.assertNotIn("script", cleaned)
        self.assertIn("color: #112233", cleaned)
        self.assertIn("font-size: 18px", cleaned)

    def test_plain_text_becomes_paragraphs(self):
        cleaned = sanitize_news_html("Linha 1\n\nLinha 2")
        self.assertEqual(cleaned, "<p>Linha 1</p><p>Linha 2</p>")

    def test_rejects_empty_html(self):
        self.assertEqual(sanitize_news_html("<p><br></p>"), "")

    def test_keeps_headings_links_tables_and_blocks(self):
        raw = (
            "<h2>Título</h2>"
            '<div class="news-block news-block--info">Aviso</div>'
            '<div class="news-block news-block--dashed">Observação</div>'
            '<a href="https://example.com">Site</a>'
            '<table class="news-table"><tr><th>A</th><td>1</td></tr></table>'
            '<span style="background-color: #fff59d">Destaque</span>'
        )
        cleaned = sanitize_news_html(raw)
        self.assertIn("<h2>Título</h2>", cleaned)
        self.assertIn('class="news-block news-block--info"', cleaned)
        self.assertIn('class="news-block news-block--dashed"', cleaned)
        self.assertIn('href="https://example.com"', cleaned)
        self.assertIn('target="_blank"', cleaned)
        self.assertIn('class="news-table"', cleaned)
        self.assertIn("background-color: #fff59d", cleaned)

    def test_rejects_unsafe_links_and_classes(self):
        raw = (
            '<a href="javascript:alert(1)">X</a>'
            '<div class="news-block news-block--evil">Y</div>'
        )
        cleaned = sanitize_news_html(raw)
        self.assertNotIn("<a", cleaned)
        self.assertNotIn("news-block--evil", cleaned)
