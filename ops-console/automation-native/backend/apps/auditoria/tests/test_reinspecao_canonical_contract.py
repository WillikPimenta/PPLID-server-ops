from __future__ import annotations

from datetime import datetime, timedelta, timezone as datetime_timezone

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.auditoria.services.reinspecao_ged_finalizados import (
    ocorrencias_finalizadas_ged,
    protocolos_finalizados_ged,
)
from apps.auditoria.services.reinspecao_import import (
    ParsedReinspecaoRow,
    avaliar_elegibilidade_reinspecao,
    parse_reinspecao_csv,
)
from apps.auditoria.services.reinspecao_import_dedupe import (
    MOTIVO_JA_ANALISADO,
    apply_reinspecao_import_dedupe,
    build_reinspecao_dedupe_key,
    build_reinspecao_occurrence_key,
    partition_rows_for_persist,
    reinspecao_occurrence_key_hash,
)
from apps.rotina_bruto.models import RotinaGedIrregularidadeTratadoRecord


IRREGULARIDADE_COMUM = "IC - 175 - CPF ausente"
IRREGULARIDADE_668 = (
    "IC - 668 - Plano no GED diverge do Plano no Contrato de Habilitação"
)


class ReinspecaoCanonicalContractUnitTests(SimpleTestCase):
    def test_datas_sao_cabecalhos_obrigatorios(self):
        for header in (
            "Protocolo;Data de resposta;Matricula do Inspetor;Descrição das Irregularidades\n",
            "Protocolo;Data da contestação;Matricula do Inspetor;Descrição das Irregularidades\n",
        ):
            with self.subTest(header=header):
                preview = parse_reinspecao_csv(
                    (header + "1;20/08/2026 10:00;C19131Q;IC - 175 - CPF ausente\n").encode()
                )
                self.assertTrue(preview.errors)
                self.assertEqual(preview.total_falhas, 0)

    def test_resposta_vazia_exige_data_contestacao_valida(self):
        self.assertEqual(
            avaliar_elegibilidade_reinspecao(
                descricao_irregularidades=IRREGULARIDADE_COMUM,
                data_contestacao="",
                data_resposta="",
            ),
            (False, "data_invalida"),
        )

    def test_contrato_de_elegibilidade_preserva_excecao_espelho(self):
        self.assertEqual(
            avaliar_elegibilidade_reinspecao(
                descricao_irregularidades=IRREGULARIDADE_COMUM,
                data_contestacao="20/08/2026 10:00",
                data_resposta="",
            ),
            (True, "data_resposta_vazia"),
        )
        self.assertEqual(
            avaliar_elegibilidade_reinspecao(
                descricao_irregularidades=IRREGULARIDADE_668,
                data_contestacao="20/08/2026 10:00",
                data_resposta="20/08/2026 10:00",
            ),
            (True, "data_resposta_espelho"),
        )
        self.assertEqual(
            avaliar_elegibilidade_reinspecao(
                descricao_irregularidades=IRREGULARIDADE_668,
                data_contestacao="20/08/2026 10:00",
                data_resposta="20/08/2026 10:01",
            ),
            (False, "respondida"),
        )

    def test_chave_canonica_ignora_inspetor_e_preserva_instante_completo(self):
        dt = timezone.make_aware(datetime(2026, 8, 20, 10, 0, 15))
        key_a = build_reinspecao_dedupe_key(
            contexto="reinspecao",
            protocolo="0018426529",
            matricula_inspetor="C19131Q",
            descricao_irregularidades="IC – 175 – CPF ausente",
            data_contestacao=dt,
        )
        key_b = build_reinspecao_dedupe_key(
            contexto="REINSPECAO",
            protocolo="18426529",
            matricula_inspetor="C99999A",
            descricao_irregularidades="ic - 175 - cpf ausente",
            data_contestacao=dt.astimezone(datetime_timezone.utc),
        )
        key_outro_instante = build_reinspecao_occurrence_key(
            contexto="reinspecao",
            protocolo="18426529",
            descricao_irregularidades=IRREGULARIDADE_COMUM,
            data_contestacao=dt + timedelta(seconds=1),
        )

        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_outro_instante)
        self.assertEqual(len(reinspecao_occurrence_key_hash(key_a)), 64)


class ReinspecaoCanonicalContractDatabaseTests(TestCase):
    def _row(self, *, when: datetime, matricula: str = "c19131q") -> ParsedReinspecaoRow:
        return ParsedReinspecaoRow(
            protocolo="18426529",
            matricula_inspetor=matricula,
            descricao_irregularidades=IRREGULARIDADE_COMUM,
            data_contestacao=when,
            excel_row=2,
        )

    def test_tratado_bloqueia_mesma_ocorrencia_mas_nao_novo_horario(self):
        first_at = timezone.make_aware(datetime(2026, 8, 20, 10, 0))
        AuditoriaFalhaCadastro.objects.create(
            protocolo="18426529",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_falha="reinspecao",
            usuario="c19131q",
            descricao_irregularidades=IRREGULARIDADE_COMUM,
            data_contestacao=first_at,
        )

        kept, _, ignored = apply_reinspecao_import_dedupe(
            [
                self._row(when=first_at, matricula="c99999a"),
                self._row(when=first_at + timedelta(minutes=1)),
            ],
            contexto="reinspecao",
        )

        self.assertEqual([row.data_contestacao for row in kept], [first_at + timedelta(minutes=1)])
        self.assertEqual(len(ignored), 1)
        self.assertEqual(ignored[0].motivo, MOTIVO_JA_ANALISADO)

    def test_snapshot_legado_por_dia_e_ambiguo_e_nao_bloqueia(self):
        when = timezone.make_aware(datetime(2026, 8, 20, 10, 0))
        RotinaGedIrregularidadeTratadoRecord.objects.create(
            report_date=when.date(),
            periodo=2,
            protocolo=18426529,
            data_contestacao=when.date(),
            data_resposta=when.date(),
            descricao_irregularidades=IRREGULARIDADE_COMUM,
        )
        row = self._row(when=when)

        self.assertEqual(protocolos_finalizados_ged([row.protocolo]), {row.protocolo})
        self.assertEqual(
            ocorrencias_finalizadas_ged([row], contexto="reinspecao"),
            set(),
        )
        kept, _, skipped_finalized = partition_rows_for_persist(
            [row],
            contexto="reinspecao",
            skip_finalized_ged={row.protocolo},
            portal_index={},
        )
        self.assertEqual(kept, [row])
        self.assertEqual(skipped_finalized, 0)
