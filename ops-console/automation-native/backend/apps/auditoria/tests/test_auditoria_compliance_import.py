from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.auditoria_compliance_import import sample_compliance_import
from apps.auditoria.services.qualidade_promocao import (
    promover_pendente_auditoria_compliance,
)

User = get_user_model()

SAMPLE_CSV = """Protocolo;Tipo/Status conferencia;Matricula Inspetor;Nome Inspetor;Data/Hora da Conferência
1001;Reclassificação;C11111Q;Agente Um;27/07/2026 14:35:20
1002;Reclassificação;C11111Q;Agente Um;27/07/2026 14:35:20
1003;Reclassificação;C11111Q;Agente Um;27/07/2026 14:35:20
2001;Reclassificação;C22222A;Agente Dois;27/07/2026 14:35:20
2002;Reclassificação;C22222A;Agente Dois;27/07/2026 14:35:20
2003;Reclassificação;C22222A;Agente Dois;27/07/2026 14:35:20
3001;Reclassificação;C33333Q;Agente Tres;27/07/2026 14:35:20
3002;Reclassificação;C33333Q;Agente Tres;27/07/2026 14:35:20
3003;Reclassificação;C33333Q;Agente Tres;27/07/2026 14:35:20
4001;Reclassificação;C44444A;Agente Quatro;27/07/2026 14:35:20
4002;Reclassificação;C44444A;Agente Quatro;27/07/2026 14:35:20
"""


@override_settings(ACCESS_ENFORCEMENT=False)
class AuditoriaComplianceImportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(
            username="comp_import",
            email="comp_import@test.local",
            password="test12345",
        )
        self.client.force_authenticate(user=self.user)

    def test_sample_a_times_distinct_agents(self):
        preview = sample_compliance_import(
            file_bytes=SAMPLE_CSV.encode("utf-8"),
            filename="relatorio.csv",
            protocolos_por_agente=2,
            rng=random.Random(7),
        )
        self.assertEqual(preview.errors, [])
        # 4 matrículas distintas × 2 protocolos
        self.assertEqual(preview.matriculas_disponiveis, 4)
        self.assertEqual(preview.total_selecionado, 8)
        self.assertEqual(len(preview.agentes), 4)
        self.assertEqual(preview.warnings, [])
        for agent in preview.agentes:
            self.assertEqual(agent.quantidade_protocolos, 2)
            self.assertTrue(agent.nome_agente)

    def test_sample_uses_all_when_agent_has_too_few_protocols(self):
        preview = sample_compliance_import(
            file_bytes=SAMPLE_CSV.encode("utf-8"),
            filename="relatorio.csv",
            protocolos_por_agente=3,
            rng=random.Random(1),
        )
        # C44444A só tem 2 protocolos — entra com 2 e gera aviso
        self.assertEqual(preview.errors, [])
        self.assertEqual(preview.total_selecionado, 11)  # 3+3+3+2
        self.assertEqual(len(preview.agentes), 4)
        self.assertTrue(preview.warnings)
        self.assertIn("abaixo do mínimo", preview.warnings[0].lower())
        self.assertNotIn("Agente Quatro", preview.warnings[0])
        by_mat = {a.matricula: a.quantidade_protocolos for a in preview.agentes}
        self.assertEqual(by_mat["c44444a"], 2)

    def test_validate_and_confirm_api(self):
        upload = SimpleUploadedFile(
            "relatorio.csv",
            SAMPLE_CSV.encode("utf-8"),
            content_type="text/csv",
        )
        validate = self.client.post(
            "/api/v1/qualidade/auditoria/compliance/importacao/validar/",
            {
                "file": upload,
                "protocolos_por_agente": 2,
                "seed": 11,
            },
            format="multipart",
        )
        self.assertEqual(validate.status_code, 200, validate.content)
        body = validate.json()
        self.assertIn("import_token", body)
        self.assertEqual(body["preview"]["total_selecionado"], 8)
        self.assertEqual(body["preview"]["matriculas_disponiveis"], 4)
        self.assertEqual(len(body["preview"]["agentes"]), 4)

        confirm = self.client.post(
            "/api/v1/qualidade/auditoria/compliance/importacao/confirmar/",
            {"import_token": body["import_token"]},
            format="json",
        )
        self.assertEqual(confirm.status_code, 201, confirm.content)
        self.assertEqual(confirm.json()["created"], 8)
        self.assertEqual(
            QualidadePendenteAuditoriaCompliance.objects.count(),
            8,
        )
        self.assertFalse(
            QualidadePendenteReinspecao.objects.filter(
                contexto="auditoria_compliance"
            ).exists()
        )
        pendente = QualidadePendenteAuditoriaCompliance.objects.first()
        self.assertIsNotNone(pendente)
        self.assertIsNone(pendente.data_contestacao)
        self.assertEqual(
            pendente.data_analise,
            timezone.make_aware(datetime(2026, 7, 27, 14, 35, 20)),
        )

        tratado = promover_pendente_auditoria_compliance(
            pendente,
            finalizador=self.user,
        )
        self.assertIsNone(tratado.data_contestacao)
        self.assertEqual(
            tratado.data_analise,
            timezone.make_aware(datetime(2026, 7, 27, 14, 35, 20)),
        )
        self.assertEqual(tratado.codigo_irregularidade, "")
        self.assertEqual(tratado.mapping_scenario_status, "")
        self.assertEqual(tratado.mapping_stage_status, "")
        self.assertEqual(tratado.mapping_version, "")
        self.assertEqual(tratado.mapping_source_hash, "")
        self.assertIsNone(tratado.mapping_applied_at)

    def test_real_relatorio_file_if_present(self):
        path = Path(r"c:\Users\c91763a\Downloads\relatorio1402.csv")
        if not path.exists():
            self.skipTest("Arquivo de referência não disponível")
        preview = sample_compliance_import(
            file_bytes=path.read_bytes(),
            filename=path.name,
            protocolos_por_agente=3,
            rng=random.Random(99),
        )
        self.assertEqual(preview.errors, [])
        self.assertGreater(preview.matriculas_disponiveis, 0)
        self.assertEqual(
            preview.total_selecionado,
            preview.matriculas_disponiveis * 3,
        )
        self.assertEqual(len(preview.agentes), preview.matriculas_disponiveis)
