from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from apps.controle_sla.models import EtapaGap, SlaBreach
from apps.controle_sla.services.sla_eval import (
    FarolThresholds,
    SlaWindow,
    business_elapsed_seconds,
    classify_criticidade,
    compute_breach_moment,
    evaluate_sla_batch,
    evaluate_and_persist,
    expand_dias_token,
)
from apps.dimensoes_processos.models import (
    DimCliente,
    DimEtapa,
    DimNivelHierarquico,
    DimWorkflow,
    ProjecaoSla,
)

TZ = ZoneInfo("America/Sao_Paulo")


class ExpandDiasTests(TestCase):
    def test_range_and_single(self):
        self.assertEqual(expand_dias_token("{0..4}"), {0, 1, 2, 3, 4})
        self.assertEqual(expand_dias_token("{5}"), {5})


class ClassifyCriticidadeTests(TestCase):
    def test_bands(self):
        self.assertEqual(classify_criticidade(80), SlaBreach.CRIT_BAIXO)
        self.assertEqual(classify_criticidade(89.9), SlaBreach.CRIT_BAIXO)
        self.assertEqual(classify_criticidade(90), SlaBreach.CRIT_MEDIO)
        self.assertEqual(classify_criticidade(94.9), SlaBreach.CRIT_MEDIO)
        self.assertEqual(classify_criticidade(95), SlaBreach.CRIT_ALTO)
        self.assertEqual(classify_criticidade(99.9), SlaBreach.CRIT_ALTO)
        self.assertEqual(classify_criticidade(100), SlaBreach.CRIT_CRITICO)
        self.assertEqual(classify_criticidade(150), SlaBreach.CRIT_CRITICO)

    def test_custom_thresholds(self):
        t = FarolThresholds(alerta=70, medio=80, alto=90, critico=100)
        self.assertEqual(classify_criticidade(75, t), SlaBreach.CRIT_BAIXO)
        self.assertEqual(classify_criticidade(85, t), SlaBreach.CRIT_MEDIO)
        self.assertEqual(classify_criticidade(92, t), SlaBreach.CRIT_ALTO)


class BusinessHoursSlaTests(TestCase):
    def test_breach_starts_at_window_open(self):
        """Item 00:00 + janela 08–18 + SLA 4h → estouro 12:00."""
        windows = [
            SlaWindow(
                days=frozenset(range(7)),
                hora_inicio=time(8, 0, 0),
                hora_fim=time(18, 0, 0),
            )
        ]
        start = datetime(2026, 7, 22, 0, 0, 0, tzinfo=TZ)  # qua
        moment = compute_breach_moment(start, 4 * 3600, windows)
        self.assertIsNotNone(moment)
        assert moment is not None
        self.assertEqual(moment.astimezone(TZ).hour, 12)
        self.assertEqual(moment.astimezone(TZ).weekday(), 2)

    def test_elapsed_skips_outside_window(self):
        windows = [
            SlaWindow(
                days=frozenset({0, 1, 2, 3, 4}),  # seg–sex
                hora_inicio=time(8, 0, 0),
                hora_fim=time(18, 0, 0),
            )
        ]
        # Qua 00:00 → qua 12:00 wall: 12h, mas só conta 08–12 = 4h
        start = datetime(2026, 7, 22, 0, 0, 0, tzinfo=TZ)
        end = datetime(2026, 7, 22, 12, 0, 0, tzinfo=TZ)
        self.assertEqual(business_elapsed_seconds(start, end, windows), 4 * 3600)

    def test_breach_skips_weekend(self):
        windows = [
            SlaWindow(
                days=frozenset({0, 1, 2, 3, 4}),
                hora_inicio=time(8, 0, 0),
                hora_fim=time(18, 0, 0),
            )
        ]
        # Sex 17:00 + SLA 2h → 1h sex + 1h seg 08–09
        start = datetime(2026, 7, 24, 17, 0, 0, tzinfo=TZ)  # sexta
        moment = compute_breach_moment(start, 2 * 3600, windows)
        self.assertIsNotNone(moment)
        assert moment is not None
        local = moment.astimezone(TZ)
        self.assertEqual(local.weekday(), 0)  # segunda
        self.assertEqual(local.hour, 9)


class EvaluatePersistTests(TestCase):
    def setUp(self):
        self.cliente = DimCliente.objects.create(id_cliente=186, nome="Claro")
        self.workflow = DimWorkflow.objects.create(
            id_workflow=10, nome="Claro", tipo_atendimento="Manual"
        )
        self.nh = DimNivelHierarquico.objects.create(id_nh=10, nome="NH10")
        DimEtapa.objects.create(id_etapa=1, nome="Analise 1a Instancia - Claro")
        now = timezone.now().astimezone(TZ)
        ProjecaoSla.objects.create(
            cliente=self.cliente,
            workflow=self.workflow,
            nivel_hierarquico=self.nh,
            data_inicio=now.date() - timedelta(days=30),
            data_fim=None,
            dias_semana="{0..6}",
            hora_inicio=time(0, 0, 0),
            hora_fim=time(23, 59, 59),
            sla_segundos=120,
            sla_ajuste=9999,
        )

    def _row(self, *, idade_offset: timedelta, nom_fluxo: str = "Analise 1a Instancia - Claro", **extra):
        antigo = timezone.now().astimezone(TZ) - idade_offset
        base = {
            "codCliente": 186,
            "nomCliente": "Claro",
            "nomWorkflow": "Claro",
            "codNivelHierarquico": 10,
            "nomFluxo": nom_fluxo,
            "qtdFila": 5,
            "qtdRegistro": 5,
            "datRegistroAntigo": antigo.strftime("%Y-%m-%d %H:%M:%S"),
        }
        base.update(extra)
        return base

    def test_batch_evaluator_returns_full_operational_states(self):
        now = timezone.now().astimezone(TZ)
        result = evaluate_sla_batch(
            [
                {"cliente": " Claro ", "workflow": "Claro", "oldest_at": now - timedelta(seconds=300)},
                {"cliente": "Inexistente", "workflow": "Claro", "oldest_at": now},
                {"cliente": "Claro", "workflow": "Claro", "oldest_at": None},
            ],
            reference_at=now,
        )
        self.assertEqual(result[0]["sla_status"], "breached")
        self.assertEqual(result[0]["sla_limit_seconds"], 120)
        self.assertLess(result[0]["sla_remaining_seconds"], 0)
        self.assertGreaterEqual(result[0]["sla_pct"], 100)
        self.assertIsNotNone(result[0]["sla_due_at"])
        self.assertEqual(result[1]["sla_status"], "unmapped")
        self.assertEqual(result[2]["sla_status"], "no_date")

    def test_breach_persisted_and_gap_for_unknown_etapa(self):
        rows = [
            self._row(idade_offset=timedelta(minutes=10)),
            {
                "codCliente": 186,
                "nomCliente": "Claro",
                "nomWorkflow": "Claro",
                "codNivelHierarquico": 99,
                "nomFluxo": "Etapa Nova Nao Cadastrada",
                "qtdFila": 2,
                "datRegistroAntigo": (timezone.now().astimezone(TZ) - timedelta(minutes=10)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            },
        ]
        stats = evaluate_and_persist(rows, update_gaps=True)
        # Mesmo workflow → 1 acompanhamento; etapa desconhecida → gap (não bloqueia)
        self.assertEqual(stats["breaches"], 1)
        self.assertEqual(stats["gaps"], 1)
        self.assertEqual(SlaBreach.objects.count(), 1)
        self.assertEqual(EtapaGap.objects.count(), 1)
        breach = SlaBreach.objects.get()
        self.assertGreaterEqual(breach.excedente_segundos, 1)
        self.assertEqual(breach.criticidade, SlaBreach.CRIT_CRITICO)
        self.assertGreaterEqual(breach.pct_sla, 100)

    def test_resolves_cliente_by_name_when_brflow_id_differs(self):
        """BrFlow codCliente 564 ≠ Megazord id_cliente 4 — casa pelo nome."""
        cliente = DimCliente.objects.create(
            id_cliente=4, nome="TELEFONICA BRASIL SA | VIVO SA"
        )
        workflow = DimWorkflow.objects.create(
            id_workflow=107, nome="Novo Pré Venda", tipo_atendimento="Manual"
        )
        nh = DimNivelHierarquico.objects.create(id_nh=99, nome="NH VIVO")
        now = timezone.now().astimezone(TZ)
        ProjecaoSla.objects.create(
            cliente=cliente,
            workflow=workflow,
            nivel_hierarquico=nh,
            data_inicio=now.date() - timedelta(days=30),
            data_fim=None,
            dias_semana="{0..6}",
            hora_inicio=time(0, 0, 0),
            hora_fim=time(23, 59, 59),
            sla_segundos=120,
        )
        rows = [
            {
                "codCliente": 564,
                "nomCliente": "TELEFONICA BRASIL SA | VIVO SA",
                "nomWorkflow": "Novo Pré Venda",
                "codNivelHierarquico": 13633,
                "nomFluxo": "Sobreposição - VIVO - Pré Venda",
                "qtdFila": 4,
                "qtdRegistro": 4,
                "datRegistroAntigo": (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
            }
        ]
        stats = evaluate_and_persist(rows, update_gaps=False, thresholds=FarolThresholds(alerta=1))
        self.assertEqual(stats["breaches"], 1)
        self.assertEqual(stats.get("skip_cliente_desconhecido", 0), 0)
        breach = SlaBreach.objects.get(cod_cliente=564)
        self.assertEqual(breach.id_workflow, 107)
        self.assertEqual(breach.nom_workflow, "Novo Pré Venda")

    def test_persists_at_eighty_percent_as_baixo(self):
        # SLA=120s → 80% = 96s
        rows = [self._row(idade_offset=timedelta(seconds=100))]
        evaluate_and_persist(rows, update_gaps=False)
        breach = SlaBreach.objects.get()
        self.assertEqual(breach.criticidade, SlaBreach.CRIT_BAIXO)
        self.assertEqual(breach.excedente_segundos, 0)
        self.assertGreaterEqual(breach.pct_sla, 80)
        self.assertLess(breach.pct_sla, 90)

    def test_ignores_below_eighty_percent(self):
        # 70% of 120s = 84s
        rows = [self._row(idade_offset=timedelta(seconds=84))]
        evaluate_and_persist(rows, update_gaps=False)
        self.assertEqual(SlaBreach.objects.count(), 0)

    def test_breach_with_brflow_nh_not_in_megazord(self):
        """NH do BrFlow é só informativo; SLA e chave são por workflow (nome)."""
        rows = [self._row(idade_offset=timedelta(minutes=10), codNivelHierarquico=8524)]
        stats = evaluate_and_persist(rows, update_gaps=False)
        self.assertEqual(stats["breaches"], 1)
        breach = SlaBreach.objects.get()
        self.assertEqual(breach.cod_nivel_hierarquico, 8524)
        self.assertEqual(breach.id_workflow, self.workflow.id_workflow)

    def test_aggregates_by_workflow_keeping_worst_fila(self):
        DimEtapa.objects.create(id_etapa=2, nome="Outra Etapa Claro")
        rows = [
            self._row(idade_offset=timedelta(seconds=100), nom_fluxo="Analise 1a Instancia - Claro"),
            self._row(
                idade_offset=timedelta(minutes=10),
                nom_fluxo="Outra Etapa Claro",
                codNivelHierarquico=99,
            ),
        ]
        stats = evaluate_and_persist(rows, update_gaps=False)
        self.assertEqual(stats["breaches"], 1)
        self.assertEqual(SlaBreach.objects.count(), 1)
        breach = SlaBreach.objects.get()
        self.assertEqual(breach.nom_fluxo, "Outra Etapa Claro")
        self.assertEqual(breach.id_workflow, self.workflow.id_workflow)

    def test_tipo_analise_from_etapa_manual_flag(self):
        DimEtapa.objects.filter(nome="Analise 1a Instancia - Claro").update(manual=False)
        rows = [self._row(idade_offset=timedelta(minutes=10))]
        evaluate_and_persist(rows, update_gaps=False, thresholds=FarolThresholds(alerta=1))
        self.assertEqual(SlaBreach.objects.get().tipo_analise, "Automática")

        DimEtapa.objects.filter(nome="Analise 1a Instancia - Claro").update(manual=True)
        evaluate_and_persist(rows, update_gaps=False, thresholds=FarolThresholds(alerta=1))
        self.assertEqual(SlaBreach.objects.get().tipo_analise, "Manual")
        self.workflow.tipo_atendimento = "Automatico"
        self.workflow.save(update_fields=["tipo_atendimento"])
        rows = [self._row(idade_offset=timedelta(minutes=10))]
        stats = evaluate_and_persist(rows, update_gaps=False, thresholds=FarolThresholds(alerta=1))
        self.assertEqual(stats["breaches"], 0)
        self.assertEqual(stats["skip_nao_manual"], 1)
        self.assertEqual(SlaBreach.objects.count(), 0)

    def test_uses_sla_segundos_not_ajuste(self):
        # SLA=120s; ajuste=9999s — idade 200s deve estourar o SLA (não o ajuste)
        rows = [self._row(idade_offset=timedelta(seconds=200))]
        evaluate_and_persist(rows, update_gaps=False)
        breach = SlaBreach.objects.get()
        self.assertEqual(breach.sla_limite_segundos, 120)
        self.assertGreaterEqual(breach.pct_sla, 100)

    def test_medio_and_alto_bands(self):
        # 90% of 120 = 108s → médio
        evaluate_and_persist([self._row(idade_offset=timedelta(seconds=110))], update_gaps=False)
        self.assertEqual(SlaBreach.objects.get().criticidade, SlaBreach.CRIT_MEDIO)

        SlaBreach.objects.all().delete()
        # 95% of 120 = 114s → alto
        evaluate_and_persist([self._row(idade_offset=timedelta(seconds=116))], update_gaps=False)
        self.assertEqual(SlaBreach.objects.get().criticidade, SlaBreach.CRIT_ALTO)

    def test_historico_persists_without_duplicating_same_episode(self):
        from apps.controle_sla.models import SlaBreachHistorico

        rows = [self._row(idade_offset=timedelta(minutes=10))]
        evaluate_and_persist(rows, update_gaps=False)
        self.assertEqual(SlaBreachHistorico.objects.count(), 1)
        evaluate_and_persist(rows, update_gaps=False)
        self.assertEqual(SlaBreachHistorico.objects.count(), 1)
        hist = SlaBreachHistorico.objects.get()
        self.assertEqual(hist.cod_cliente, 186)
        self.assertEqual(hist.id_workflow, self.workflow.id_workflow)

    def test_historico_survives_live_breach_delete(self):
        from apps.controle_sla.models import SlaBreachHistorico

        evaluate_and_persist([self._row(idade_offset=timedelta(minutes=10))], update_gaps=False)
        self.assertEqual(SlaBreach.objects.count(), 1)
        self.assertEqual(SlaBreachHistorico.objects.count(), 1)
        # Abaixo do alerta → remove snapshot ao vivo, mantém histórico
        evaluate_and_persist([self._row(idade_offset=timedelta(seconds=10))], update_gaps=False)
        self.assertEqual(SlaBreach.objects.count(), 0)
        self.assertEqual(SlaBreachHistorico.objects.count(), 1)

    def test_gap_does_not_recreate_ignored_and_increments_ocorrencias(self):
        rows = [
            {
                "codCliente": 186,
                "nomCliente": "Claro",
                "nomWorkflow": "Claro",
                "codNivelHierarquico": 99,
                "nomFluxo": "Etapa Nova Nao Cadastrada",
                "qtdFila": 2,
                "datRegistroAntigo": (timezone.now().astimezone(TZ) - timedelta(minutes=10)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        ]
        evaluate_and_persist(rows, update_gaps=True)
        gap = EtapaGap.objects.get()
        self.assertEqual(gap.situacao, EtapaGap.SIT_PENDENTE)
        self.assertEqual(gap.ocorrencias, 1)
        gap.situacao = EtapaGap.SIT_IGNORADA
        gap.save(update_fields=["situacao"])

        evaluate_and_persist(rows, update_gaps=True)
        self.assertEqual(EtapaGap.objects.count(), 1)
        gap.refresh_from_db()
        self.assertEqual(gap.situacao, EtapaGap.SIT_IGNORADA)
        self.assertEqual(gap.ocorrencias, 2)

    def test_gap_not_created_when_etapa_already_in_catalog(self):
        rows = [self._row(idade_offset=timedelta(minutes=10))]
        evaluate_and_persist(rows, update_gaps=True)
        self.assertEqual(EtapaGap.objects.count(), 0)

    def test_protocolo_atuais_vs_antigos(self):
        from apps.controle_sla.services.sla_eval import is_protocolo_atual, normalize_protocolos_dias

        self.assertEqual(normalize_protocolos_dias(-3), 0)
        self.assertEqual(normalize_protocolos_dias("x"), 5)
        now = timezone.now().astimezone(TZ)
        recente = now - timedelta(days=2)
        antigo = now - timedelta(days=10)
        self.assertTrue(is_protocolo_atual(recente, dias=5, now=now))
        self.assertFalse(is_protocolo_atual(antigo, dias=5, now=now))
        self.assertTrue(is_protocolo_atual(now - timedelta(days=5), dias=5, now=now))

