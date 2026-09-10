from __future__ import annotations

from datetime import date, datetime
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaAtividadeProtocoloEtapa,
    AuditoriaFalhaCadastro,
    QualidadeAnaliseOrigem,
    QualidadePendenteAuditoria,
    QualidadePendenteAuditoriaFalha,
    QualidadePendenteContestacao,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.analise_origem import get_or_create_analise_origem
from apps.auditoria.services.atividade_falhas import finalizar_atividade_auditoria
from apps.auditoria.services.atividade_import import confirm_import, create_import_staging, preview_import
from apps.auditoria.services.qualidade_promocao import (
    promover_pendente_reinspecao,
    promover_protocolo_contestacao,
    selar_tratados_auditoria,
    tratados_qs,
)
from apps.auditoria.tests.test_atividade_import import build_sample_workbook

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=False)
class QualidadeTratadosPromocaoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="tratados_user",
            email="tratados@test.local",
            password="test12345",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_promover_protocolo_contestacao_cria_tratado_e_preserva_historico(self):
        finalizador = User.objects.create_user(
            username="contestacao_finalizador",
            email="contestacao_finalizador@test.local",
            password="x",
        )
        preview = preview_import(build_sample_workbook(), "contestacao.xlsx")
        staging = create_import_staging(
            user=self.user,
            file_bytes=build_sample_workbook(),
            filename="contestacao.xlsx",
            preview=preview,
        )
        atividade = confirm_import(
            staging=staging,
            user=self.user,
            nome="Lote promoção",
            data_recepcao=date(2026, 7, 1),
        )
        protocolo = atividade.protocolos.first()
        protocolo.status = AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
        protocolo.situacao = AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE
        protocolo.analisado_por = self.user
        protocolo.save()
        etapa = AuditoriaAtividadeProtocoloEtapa.objects.create(
            protocolo=protocolo,
            ordem=0,
            resultado_correto="APROVADO",
            agente="c12345a",
            tipo_falha="Processual",
            etapa_falha="AUDITORIA",
            situacao=AuditoriaAtividadeProtocoloEtapa.SITUACAO_PROCEDENTE,
            motivo_falha="Motivo teste",
        )
        QualidadePendenteContestacao.objects.create(
            atividade=atividade,
            nome=atividade.nome,
            cliente=atividade.cliente,
            status=QualidadePendenteContestacao.STATUS_EM_ANDAMENTO,
            total_protocolos=atividade.total_protocolos,
            created_by=self.user,
        )

        falha = promover_protocolo_contestacao(protocolo, user=finalizador)

        self.assertEqual(falha.origem, AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO)
        self.assertEqual(falha.analise_status, AuditoriaFalhaCadastro.ANALISE_CONCLUIDO)
        self.assertIsNotNone(falha.analise_origem_id)
        self.assertIsNotNone(falha.data_analise_intranet)
        atividade.refresh_from_db()
        self.assertEqual(falha.data_recepcao_contestacao, atividade.data_recepcao)
        self.assertNotIn("etapas", falha.analise_origem.trilha_parsed)
        self.assertEqual(falha.brflow_parsed, {})
        self.assertEqual(falha.etapa_origem_id, etapa.id)
        self.assertEqual(falha.etapa_chave, f"contestacao:etapa:{etapa.id}")
        self.assertEqual(falha.auditor_responsavel_id, finalizador.id)
        self.assertTrue(falha.id)
        protocolo.refresh_from_db()
        self.assertEqual(protocolo.tratado_id, falha.id)
        self.assertTrue(AuditoriaAtividadeProtocolo.objects.filter(pk=protocolo.pk).exists())
        self.assertEqual(
            tratados_qs(origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO)
            .filter(protocolo=falha.protocolo)
            .count(),
            1,
        )

    def test_promover_contestacao_cria_um_tratado_por_etapa_e_atribui_falha_ao_agente_correto(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_CONTESTACAO,
            nome="Lote quatro etapas",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            cliente="BANCO BRASIL",
            created_by=self.user,
        )
        protocolo = AuditoriaAtividadeProtocolo.objects.create(
            atividade=atividade,
            protocolo="1278358",
            status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
            situacao=AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE,
            excel_row=2,
            analisado_por=self.user,
            analisado_em=timezone.now(),
            finalizado_em=timezone.now(),
        )
        etapas = [
            ("c21926q", "Reclassificação", "improcedente"),
            ("c22811b", "Sobreposição", "improcedente"),
            ("c23236q", "Comparação de Selfie", "procedente"),
            ("c92855a", "Análise Visual", "improcedente"),
        ]
        for ordem, (agente, etapa_falha, situacao) in enumerate(etapas):
            AuditoriaAtividadeProtocoloEtapa.objects.create(
                protocolo=protocolo,
                ordem=ordem,
                resultado_correto="Não aplicável",
                agente=agente,
                tipo_falha="Colaborador",
                etapa_falha=etapa_falha,
                situacao=situacao,
                motivo_falha="Com risco" if situacao == "procedente" else "",
            )

        canonical = promover_protocolo_contestacao(protocolo, user=self.user)

        tratados = AuditoriaFalhaCadastro.objects.filter(protocolo_origem=protocolo).order_by(
            "ordem_etapa"
        )
        self.assertEqual(tratados.count(), 4)
        self.assertEqual(list(tratados.values_list("usuario", flat=True)), [item[0] for item in etapas])
        falha = tratados.get(resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_COM_FALHA)
        self.assertEqual(falha.usuario, "c23236q")
        self.assertEqual(falha.etapa_falha, "Comparação de Selfie")
        self.assertEqual(canonical.id, falha.id)
        self.assertFalse(any("etapas" in (item.brflow_parsed or {}) for item in tratados))

        analise_origem_ids = set(tratados.values_list("analise_origem_id", flat=True))
        outro_finalizador = User.objects.create_user(
            username="outro_finalizador",
            email="outro_finalizador@test.local",
            password="x",
        )
        promover_protocolo_contestacao(protocolo, user=outro_finalizador)
        self.assertEqual(AuditoriaFalhaCadastro.objects.filter(protocolo_origem=protocolo).count(), 4)
        self.assertEqual(
            set(
                AuditoriaFalhaCadastro.objects.filter(protocolo_origem=protocolo).values_list(
                    "analise_origem_id", flat=True
                )
            ),
            analise_origem_ids,
        )
        self.assertFalse(
            AuditoriaFalhaCadastro.objects.filter(
                protocolo_origem=protocolo,
                auditor_responsavel=outro_finalizador,
            ).exists()
        )

    def test_api_finalizar_protocolo_promove_para_tratado(self):
        preview = preview_import(build_sample_workbook(), "contestacao.xlsx")
        staging = create_import_staging(
            user=self.user,
            file_bytes=build_sample_workbook(),
            filename="contestacao.xlsx",
            preview=preview,
        )
        atividade = confirm_import(
            staging=staging,
            user=self.user,
            nome="Lote API",
            data_recepcao=date(2026, 7, 1),
        )
        protocolo = atividade.protocolos.first()
        response = self.client.patch(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/protocolos/{protocolo.id}/analise/",
            {
                "finalizar": True,
                "etapas": [
                    {
                        "resultado_correto": "APROVADO",
                        "tipo_falha": "Processual",
                        "etapa_falha": "AUDITORIA",
                        "agente": "c12345a",
                        "situacao": "improcedente",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data.get("promovido"))
        self.assertEqual(response.data.get("origem"), "contestacao")
        protocolo.refresh_from_db()
        self.assertIsNotNone(protocolo.tratado_id)
        self.assertTrue(
            AuditoriaFalhaCadastro.objects.filter(
                origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
                protocolo=protocolo.protocolo,
            ).exists()
        )

    def test_selar_auditoria_marca_concluido_e_remove_pendente(self):
        iniciador = User.objects.create_user(
            username="auditoria_iniciador",
            email="auditoria_iniciador@test.local",
            password="x",
        )
        finalizador = User.objects.create_user(
            username="auditoria_finalizador",
            email="auditoria_finalizador@test.local",
            password="x",
        )
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Aud selar",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            created_by=self.user,
            brflow_parsed={"trilha_raw": "trilha preenchida"},
            observacao="Divergencia no resultado do cliente",
        )
        rascunho = QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            protocolo="999",
            usuario="c12345a",
            tipo_falha="Colaborador",
            modulo="Risk Manager",
            resultado_cliente="240",
            novo_resultado="241",
            sinalizacao="Sim",
            motivo_falha="Documento ilegivel",
            etapa_falha="Analise Visual",
            tempo_analise="00:01:23",
            nivel_dificuldade="Alto",
            tipo_documento="RG",
            uf_documento="SP",
            qualidade_imagem="Ruim",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            created_by=self.user,
        )
        QualidadePendenteAuditoria.objects.create(
            atividade=atividade,
            protocolo="999",
            status=QualidadePendenteAuditoria.STATUS_EM_ANDAMENTO,
            created_by=self.user,
        )
        AuditoriaAtividadeProtocolo.objects.create(
            atividade=atividade,
            protocolo="999",
            excel_row=1,
            analisado_por=iniciador,
        )

        self.assertEqual(AuditoriaFalhaCadastro.objects.filter(atividade=atividade).count(), 0)
        finalizar_atividade_auditoria(atividade, finalizador=finalizador)
        atividade.refresh_from_db()
        falha = AuditoriaFalhaCadastro.objects.get(atividade=atividade)

        self.assertEqual(atividade.status, AuditoriaAtividade.STATUS_CONCLUIDA)
        self.assertEqual(falha.analise_status, AuditoriaFalhaCadastro.ANALISE_CONCLUIDO)
        self.assertEqual(falha.tempo_analise, "00:01:23")
        self.assertEqual(falha.origem, AuditoriaFalhaCadastro.ORIGEM_AUDITORIA)
        self.assertEqual(falha.created_by_id, self.user.id)
        self.assertEqual(falha.auditor_responsavel_id, finalizador.id)
        self.assertEqual(falha.observacao, "Divergencia no resultado do cliente")
        self.assertNotEqual(falha.auditor_responsavel_id, iniciador.id)
        self.assertIsNotNone(falha.analise_origem_id)
        self.assertFalse(QualidadePendenteAuditoria.objects.filter(atividade=atividade).exists())
        self.assertFalse(QualidadePendenteAuditoriaFalha.objects.filter(pk=rascunho.pk).exists())
        self.assertFalse(atividade.protocolos.exists())
        self.assertEqual(falha.analise_status, AuditoriaFalhaCadastro.ANALISE_CONCLUIDO)

        listed_falhas = self.client.get(
            f"/api/v1/qualidade/auditoria/atividades/{atividade.id}/falhas/",
        )
        self.assertEqual(listed_falhas.status_code, 200, listed_falhas.data)
        self.assertEqual(len(listed_falhas.data["results"]), 1)
        listed = listed_falhas.data["results"][0]
        expected_grid_fields = {
            "modulo": "Risk Manager",
            "resultado_cliente": "240",
            "novo_resultado": "241",
            "sinalizacao": "Sim",
            "motivo_falha": "Documento ilegivel",
            "etapa_falha": "Analise Visual",
            "nivel_dificuldade": "Alto",
            "tipo_documento": "RG",
            "uf_documento": "SP",
            "qualidade_imagem": "Ruim",
        }
        self.assertEqual(
            {field: listed[field] for field in expected_grid_fields},
            expected_grid_fields,
        )
        # Idempotente: sem rascunhos, não cria registros extras.
        again = selar_tratados_auditoria(atividade, finalizador=finalizador)
        self.assertEqual(len(again), 0)
        self.assertEqual(
            AuditoriaFalhaCadastro.objects.filter(atividade=atividade).count(),
            1,
        )

    def test_finalizar_auditoria_rejeita_rascunho_incompleto_no_backend(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Auditoria incompleta",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            created_by=self.user,
            brflow_parsed={"trilha_raw": "trilha preenchida"},
        )
        QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            protocolo="INCOMPLETA-1",
            usuario="c12345a",
            tipo_falha="Colaborador",
            created_by=self.user,
        )

        with self.assertRaisesMessage(ValueError, "Não foi possível finalizar"):
            finalizar_atividade_auditoria(atividade, finalizador=self.user)

        atividade.refresh_from_db()
        self.assertEqual(atividade.status, AuditoriaAtividade.STATUS_EM_ANDAMENTO)
        self.assertEqual(atividade.tratados.count(), 0)

    def test_origem_separa_trilha_e_deduplica_conteudo_identico(self):
        payload = {
            "protocolo": "123",
            "cliente": "Cliente",
            "trilha_raw": "trilha original",
            "extracted_users": ["agente1"],
            "fila_contexto": "auditoria_compliance",
            "data_criacao": "2026-07-01T08:00:00",
            "data_analise": "2026-07-02T09:00:00",
            "data_conclusao": "2026-07-03T10:00:00",
        }

        primeira = get_or_create_analise_origem(
            protocolo="123",
            brflow_raw="brflow original",
            brflow_parsed=payload,
        )
        segunda = get_or_create_analise_origem(
            protocolo="123",
            brflow_raw="brflow original",
            brflow_parsed=payload,
        )

        self.assertEqual(primeira.pk, segunda.pk)
        self.assertEqual(QualidadeAnaliseOrigem.objects.count(), 1)
        self.assertEqual(
            primeira.brflow_parsed,
            {
                "protocolo": "123",
                "cliente": "Cliente",
                "data_criacao": "2026-07-01T08:00:00",
                "data_analise": "2026-07-02T09:00:00",
                "data_conclusao": "2026-07-03T10:00:00",
            },
        )
        self.assertEqual(primeira.protocolo_criado_em.date(), date(2026, 7, 1))
        self.assertEqual(primeira.protocolo_analisado_em.date(), date(2026, 7, 2))
        self.assertEqual(primeira.protocolo_concluido_em.date(), date(2026, 7, 3))
        self.assertEqual(primeira.trilha_raw, "trilha original")
        self.assertEqual(primeira.trilha_parsed, {"extracted_users": ["agente1"]})
        self.assertEqual(primeira.contexto, {"fila_contexto": "auditoria_compliance"})

    def test_origem_legada_sem_datas_estruturadas_continua_compativel(self):
        payload = {"data_analise": "2026-07-02", "cliente": "Cliente"}
        criada = get_or_create_analise_origem(
            protocolo="LEG-DATA",
            brflow_raw="raw",
            brflow_parsed=payload,
        )
        QualidadeAnaliseOrigem.objects.filter(pk=criada.pk).update(
            protocolo_criado_em=None,
            protocolo_analisado_em=None,
            protocolo_concluido_em=None,
        )

        mesma = get_or_create_analise_origem(
            protocolo="LEG-DATA",
            brflow_raw="raw",
            brflow_parsed=payload,
        )

        self.assertEqual(mesma.pk, criada.pk)
        self.assertIsNone(mesma.protocolo_analisado_em)

    def test_promover_reinspecao_vincula_origem_e_remove_pendente(self):
        data_contestacao = timezone.make_aware(datetime(2026, 7, 15, 17, 34, 54))
        pendente = QualidadePendenteReinspecao.objects.create(
            protocolo="RE-123",
            usuario="c12345a",
            tipo_falha="reinspecao",
            data_contestacao=data_contestacao,
            brflow_parsed={
                "situacao": "procedente",
                "fila_contexto": "reinspecao",
            },
            analise_status=QualidadePendenteReinspecao.ANALISE_EM_ANALISE,
            created_by=self.user,
        )
        pendente_id = pendente.pk

        tratado = promover_pendente_reinspecao(pendente, finalizador=self.user)

        self.assertFalse(QualidadePendenteReinspecao.objects.filter(pk=pendente_id).exists())
        self.assertIsNotNone(tratado.analise_origem_id)
        self.assertEqual(tratado.auditor_responsavel_id, self.user.id)
        self.assertEqual(tratado.data_contestacao, data_contestacao)
        self.assertIsNone(tratado.data_analise)
        self.assertEqual(tratado.analise_origem.trilha_parsed, {"situacao": "procedente"})
        self.assertEqual(
            tratado.analise_origem.contexto,
            {"fila_contexto": "reinspecao", "pendente_reinspecao_id": pendente_id},
        )

    def test_migration_expande_contestacao_legada_preservando_id_na_etapa_procedente(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_CONTESTACAO,
            nome="Lote legado",
            status=AuditoriaAtividade.STATUS_CONCLUIDA,
            created_by=self.user,
        )
        protocolo = AuditoriaAtividadeProtocolo.objects.create(
            atividade=atividade,
            protocolo="LEG-4",
            status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
            situacao=AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE,
            excel_row=2,
            analisado_por=self.user,
        )
        etapas = []
        for ordem, agente, situacao in (
            (0, "agente_errado", "improcedente"),
            (1, "agente_2", "improcedente"),
            (2, "agente_correto", "procedente"),
            (3, "agente_4", "improcedente"),
        ):
            etapas.append(
                AuditoriaAtividadeProtocoloEtapa.objects.create(
                    protocolo=protocolo,
                    ordem=ordem,
                    agente=agente,
                    tipo_falha="Colaborador",
                    etapa_falha=f"Etapa {ordem + 1}",
                    resultado_correto="Resultado",
                    situacao=situacao,
                )
            )
        legado = AuditoriaFalhaCadastro.objects.create(
            atividade=atividade,
            protocolo="LEG-4",
            usuario="agente_errado",
            tipo_falha="Colaborador",
            status="procedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
            brflow_parsed={"etapas": [{"agente": item.agente} for item in etapas]},
            created_by=self.user,
        )
        protocolo.tratado = legado
        protocolo.save(update_fields=["tratado", "updated_at"])

        migration = import_module(
            "apps.auditoria.migrations.0050_normalizar_tratados_por_etapa"
        )
        migration.normalizar_tratados(django_apps, None)

        tratados = AuditoriaFalhaCadastro.objects.filter(protocolo_origem=protocolo)
        self.assertEqual(tratados.count(), 4)
        legado.refresh_from_db()
        self.assertEqual(legado.id, protocolo.tratado_id)
        self.assertEqual(legado.etapa_origem_id, etapas[2].id)
        self.assertEqual(legado.usuario, "agente_correto")
        self.assertEqual(legado.resultado_qualidade, AuditoriaFalhaCadastro.RESULTADO_COM_FALHA)
        self.assertNotIn("etapas", legado.brflow_parsed)
        self.assertEqual(
            tratados.filter(resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_COM_FALHA).count(),
            1,
        )

    def test_migration_separa_datas_existentes_por_origem(self):
        data_importada = timezone.make_aware(datetime(2026, 7, 27, 14, 35, 20))
        pendente = QualidadePendenteReinspecao.objects.create(
            protocolo="COMP-PENDENTE",
            contexto="auditoria_compliance",
            data_contestacao=data_importada,
        )
        tratado = AuditoriaFalhaCadastro.objects.create(
            protocolo="COMP-TRATADO",
            data_contestacao=data_importada,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
        )

        migration = import_module(
            "apps.auditoria.migrations.0048_separate_origin_dates"
        )
        migration._separate_origin_dates(django_apps, None)

        pendente.refresh_from_db()
        tratado.refresh_from_db()
        self.assertIsNone(pendente.data_contestacao)
        self.assertEqual(pendente.data_analise, data_importada)
        self.assertIsNone(tratado.data_contestacao)
        self.assertEqual(tratado.data_analise, data_importada)

    def test_backfill_migration_vincula_e_deduplica_tratados_existentes(self):
        common = {
            "protocolo": "LEG-123",
            "brflow_raw": "brflow legado",
            "brflow_parsed": {
                "protocolo": "LEG-123",
                "trilha_raw": "trilha legada",
                "extracted_users": ["agente1"],
            },
            "tipo_falha": "Colaborador",
            "usuario": "c12345a",
            "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            "created_by": self.user,
        }
        primeiro = AuditoriaFalhaCadastro.objects.create(**common)
        segundo = AuditoriaFalhaCadastro.objects.create(**common)
        migration = import_module(
            "apps.auditoria.migrations.0045_qualidade_analise_origem"
        )

        migration.backfill_analise_origem(
            django_apps,
            SimpleNamespace(connection=SimpleNamespace(alias="default")),
        )
        migration.backfill_analise_origem(
            django_apps,
            SimpleNamespace(connection=SimpleNamespace(alias="default")),
        )

        primeiro.refresh_from_db()
        segundo.refresh_from_db()
        self.assertEqual(QualidadeAnaliseOrigem.objects.count(), 1)
        self.assertIsNotNone(primeiro.analise_origem_id)
        self.assertEqual(primeiro.analise_origem_id, segundo.analise_origem_id)
        self.assertEqual(primeiro.analise_origem.trilha_raw, "trilha legada")
        self.assertEqual(
            primeiro.analise_origem.trilha_parsed,
            {"extracted_users": ["agente1"]},
        )

    def test_resultado_qualidade_e_preenchido_e_atualizado_automaticamente(self):
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="RES-001",
            tipo_falha="Processual",
            usuario="c12345a",
            status="Procedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
            created_by=self.user,
        )
        self.assertEqual(
            falha.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
        )

        falha.status = "Improcedente"
        falha.save(update_fields=["status"])
        falha.refresh_from_db()
        self.assertEqual(
            falha.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
        )
        self.assertEqual(falha.analise_status, AuditoriaFalhaCadastro.ANALISE_CONCLUIDO)

    def test_resultado_distingue_reinspecao_e_contestacao_operacional(self):
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="RES-REI-1",
            tipo_falha="Processual",
            usuario="c12345a",
            status="Procedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            created_by=self.user,
        )
        self.assertEqual(falha.resultado_qualidade, "sem_falha")

        falha.status = "Improcedente"
        falha.save(update_fields=["status"])
        self.assertEqual(falha.resultado_qualidade, "com_falha")

        falha.status_falha = AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA
        falha.save(update_fields=["status_falha"])
        self.assertEqual(falha.resultado_qualidade, "sem_falha")

        falha.status_falha = AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA
        falha.save(update_fields=["status_falha"])
        self.assertEqual(falha.resultado_qualidade, "com_falha")

    def test_backfill_resultado_qualidade_classifica_legado_idempotentemente(self):
        registros = [
            AuditoriaFalhaCadastro(
                protocolo="RES-LEG-1",
                tipo_falha="Colaborador",
                usuario="agente1",
                status="",
                created_by=self.user,
            ),
            AuditoriaFalhaCadastro(
                protocolo="RES-LEG-2",
                tipo_falha="Sem Falha",
                usuario="agente2",
                status="",
                created_by=self.user,
            ),
            AuditoriaFalhaCadastro(
                protocolo="RES-LEG-3",
                tipo_falha="reinspecao",
                usuario="agente3",
                status="",
                created_by=self.user,
            ),
        ]
        AuditoriaFalhaCadastro.objects.bulk_create(registros)
        migration = import_module("apps.auditoria.migrations.0046_resultado_qualidade")
        editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))

        migration.backfill_resultado_qualidade(django_apps, editor)
        migration.backfill_resultado_qualidade(django_apps, editor)

        resultados = dict(
            AuditoriaFalhaCadastro.objects.filter(protocolo__startswith="RES-LEG-")
            .values_list("protocolo", "resultado_qualidade")
        )
        self.assertEqual(resultados["RES-LEG-1"], "com_falha")
        self.assertEqual(resultados["RES-LEG-2"], "sem_falha")
        self.assertEqual(resultados["RES-LEG-3"], "nao_classificado")
