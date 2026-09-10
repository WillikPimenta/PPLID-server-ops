from django.test import SimpleTestCase

from apps.planejamento_demandas.models import JiraDemanda
from apps.planejamento_demandas.services.classifier import classify_demanda_text


class JiraDemandaClassifierTests(SimpleTestCase):
    def test_headcount(self):
        cat, path = classify_demanda_text("Desligamento colaborador matrícula 12345")
        self.assertEqual(cat, JiraDemanda.CATEGORIA_HEADCOUNT)
        self.assertEqual(path, "/planejamento/megazord/headcount")

    def test_regras_workflow(self):
        cat, path = classify_demanda_text("Atualizar regra workflow alerta indefinida")
        self.assertEqual(cat, JiraDemanda.CATEGORIA_REGRAS)
        self.assertEqual(path, "/planejamento/regras-workflow")

    def test_outro(self):
        cat, path = classify_demanda_text("Reunião sem contexto operacional")
        self.assertEqual(cat, JiraDemanda.CATEGORIA_OUTRO)
        self.assertEqual(path, "")
