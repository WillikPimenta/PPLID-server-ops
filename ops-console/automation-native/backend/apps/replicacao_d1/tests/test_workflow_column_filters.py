from apps.replicacao_d1.models import ReplicacaoD1Cliente, ReplicacaoD1Workflow
from apps.replicacao_d1.services.workflow_column_filters import build_workflow_column_filter_options
from django.test import TestCase


class WorkflowColumnFilterOptionsTests(TestCase):
    def test_build_options_includes_clients_beyond_first_page(self):
        cliente = ReplicacaoD1Cliente.objects.create(nome="SEGURANÇA CORPORATIVA - GRUPO BRADESCO")
        for idx in range(60):
            ReplicacaoD1Workflow.objects.create(
                nome_canonico=f"WF {idx}",
                cliente=cliente,
                fila="Bio",
            )

        options = build_workflow_column_filter_options()

        self.assertIn("SEGURANÇA CORPORATIVA - GRUPO BRADESCO", options["cliente_nome"])
        self.assertEqual(len(options["nome_canonico"]), 60)
