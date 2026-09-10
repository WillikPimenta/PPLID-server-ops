from django.test import SimpleTestCase, TestCase

from apps.dimensoes_processos.services.capacity_familia import (
    extract_familia,
    extract_familia_catalog,
    extract_familia_normalized,
    familias_differ,
    has_familia_separator,
    invalidate_familia_alias_cache,
    is_automatic_stage_name,
    is_single_word_familia,
    normalize_familia,
)


class ExtractFamiliaV1Tests(SimpleTestCase):
    def test_splits_on_first_separator(self):
        nome = "Análise Visual - Cliente A - Detalhe"
        self.assertEqual(extract_familia(nome), "Análise Visual")

    def test_preserves_full_name_without_separator(self):
        self.assertEqual(extract_familia("OCR"), "OCR")
        self.assertEqual(extract_familia("Análise Visual II"), "Análise Visual II")

    def test_strips_whitespace(self):
        self.assertEqual(extract_familia("  Análise Visual  - Cliente  "), "Análise Visual")

    def test_empty_input(self):
        self.assertEqual(extract_familia(""), "")
        self.assertEqual(extract_familia(None), "")

    def test_separator_only_tail(self):
        self.assertEqual(extract_familia("Gerenciador de Entrada - Cliente X"), "Gerenciador de Entrada")

    def test_does_not_split_on_single_dash(self):
        self.assertEqual(extract_familia("POC-PILOTO - Execução"), "POC-PILOTO")


class ExtractFamiliaCatalogTests(SimpleTestCase):
    def test_ocr_longest_match_before_naive_split(self):
        nome = "OCR - 99PAY - Documentoscopia Especializada"
        self.assertEqual(extract_familia(nome), "OCR")
        self.assertEqual(extract_familia_catalog(nome), "OCR")

    def test_gerenciador_regras_batimento_ii(self):
        nome = "Gerenciador de Regras Batimento de Dados II - Cliente"
        self.assertEqual(extract_familia(nome), "Gerenciador de Regras Batimento de Dados II")
        self.assertEqual(extract_familia_catalog(nome), "Gerenciador de Regras Batimento de Dados II")

    def test_integrador_prefix(self):
        nome = "Integrador - Workflow XYZ"
        self.assertEqual(extract_familia_catalog(nome), "Integrador")


class FamiliaEdgeCaseTests(SimpleTestCase):
    def test_analise_visual_vs_analise_visual_ii_raw_extract_differ(self):
        v1 = extract_familia("Análise Visual - Cliente A")
        v2 = extract_familia("Análise Visual II - Cliente B")
        self.assertNotEqual(v1, v2)
        self.assertEqual(v1, "Análise Visual")
        self.assertEqual(v2, "Análise Visual II")

    def test_single_word_detection(self):
        self.assertTrue(is_single_word_familia("OCR"))
        self.assertFalse(is_single_word_familia("Análise Visual"))

    def test_has_separator(self):
        self.assertTrue(has_familia_separator("A - B"))
        self.assertFalse(has_familia_separator("Análise Visual II"))

    def test_automatic_stage_detection(self):
        self.assertTrue(is_automatic_stage_name("OCR - Cliente"))
        self.assertTrue(is_automatic_stage_name("Gerenciador de Regras OCR - Cliente"))
        self.assertFalse(is_automatic_stage_name("Análise Visual - Cliente"))

    def test_catalog_differs_for_gerenciador_entrada_vs_regras(self):
        nome = "Gerenciador de Regras OCR - Cliente"
        self.assertEqual(extract_familia(nome), "Gerenciador de Regras OCR")
        self.assertEqual(extract_familia_catalog(nome), "Gerenciador de Regras OCR")
        self.assertFalse(familias_differ(nome))

        nome_entrada = "Gerenciador de Entrada - Cliente"
        self.assertEqual(extract_familia_catalog(nome_entrada), "Gerenciador de Entrada")


class NormalizeFamiliaTests(TestCase):
    def setUp(self):
        invalidate_familia_alias_cache()

    def tearDown(self):
        invalidate_familia_alias_cache()

    def test_case_alias_merges_analise_visual(self):
        self.assertEqual(normalize_familia("ANÁLISE VISUAL"), "Análise Visual")

    def test_malformed_selfie_vivo_alias(self):
        self.assertEqual(
            extract_familia_normalized("Comparação de Selfie- VIVO - Pré Venda"),
            "Comparação de Selfie",
        )

    def test_analise_visual_ii_merges_to_canonical(self):
        self.assertEqual(
            extract_familia_normalized("Análise Visual II - Cliente B"),
            "Análise Visual",
        )

    def test_unknown_familia_passthrough(self):
        self.assertEqual(normalize_familia("Sobreposição Composta"), "Sobreposição Composta")
