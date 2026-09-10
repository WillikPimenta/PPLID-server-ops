# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from apps.suporte_claro.services.analytics import BucketStat, ReportStats, build_report_narrative

FORBIDDEN_JARGON = (
    "portal",
    "priorizar",
    "proximo passo",
    "formaliz",
    "registro para",
    "fila em aberto",
    "auditoria",
)


def _stats(**kwargs) -> ReportStats:
    defaults = dict(
        total=8,
        by_status=[
            BucketStat("aberto", "Nao iniciado", 2, 25.0),
            BucketStat("em_atendimento", "Em andamento", 3, 37.5),
            BucketStat("concluido", "Concluido", 3, 37.5),
        ],
        by_origem=[
            BucketStat("teams", "Teams", 3, 37.5),
            BucketStat("email", "E-mail", 3, 37.5),
            BucketStat("ligacao", "Ligacao", 2, 25.0),
        ],
        with_retorno=7,
        with_retorno_pct=87.5,
        with_anexos=0,
        with_anexos_pct=0.0,
        top_cadastradores=[],
        without_retorno=1,
        without_retorno_pct=12.5,
        sla_resolved=7,
        sla_pending=1,
        sla_avg_minutes=135,
        sla_avg_label="2h 15min",
    )
    defaults.update(kwargs)
    return ReportStats(**defaults)


class ReportNarrativeTests(SimpleTestCase):
    def test_empty_period(self):
        stats = _stats(total=0, by_status=[], by_origem=[], with_retorno=0, with_retorno_pct=0)
        paras = build_report_narrative(stats, "01/07/2026 a 07/07/2026")
        self.assertEqual(len(paras), 1)
        self.assertIn("não houve solicitações", paras[0].lower())

    def test_typical_period_mentions_sla_and_channels(self):
        paras = build_report_narrative(_stats(), "01/07/2026 a 07/07/2026")
        joined = " ".join(paras).lower()
        self.assertIn("8 demandas", joined)
        self.assertIn("2h 15min", joined)
        self.assertIn("teams", joined)
        self.assertIn("acompanhamento", joined)

    def test_all_concluded(self):
        stats = _stats(
            total=3,
            by_status=[BucketStat("concluido", "Concluido", 3, 100.0)],
            by_origem=[BucketStat("email", "E-mail", 3, 100.0)],
            with_retorno=3,
            with_retorno_pct=100.0,
            without_retorno=0,
            without_retorno_pct=0.0,
            sla_resolved=3,
            sla_pending=0,
            sla_avg_label="45 min",
        )
        paras = build_report_narrative(stats, "jul/2026")
        joined = " ".join(paras).lower()
        self.assertIn("todas foram concluídas", joined)
        self.assertIn("retorno informado", joined)

    def test_narrative_avoids_internal_jargon(self):
        paras = build_report_narrative(_stats(), "01/07/2026 a 07/07/2026")
        joined = " ".join(paras).lower()
        for term in FORBIDDEN_JARGON:
            self.assertNotIn(term, joined, msg=f"Jargao interno encontrado: {term}")
