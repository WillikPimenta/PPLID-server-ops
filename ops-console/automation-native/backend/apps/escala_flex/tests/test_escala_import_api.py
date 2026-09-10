"""Testes da API de importação de escala."""



from io import BytesIO

from unittest.mock import patch



import pandas as pd

from django.contrib.auth import get_user_model

from django.core.files.uploadedfile import SimpleUploadedFile

from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_GERENCIA, role_group_name
from django.contrib.auth.models import Group



from apps.escala_flex.models import EscalaImportBatch

from apps.escala_flex.services.escala_excel_importer import _run_import_job

from apps.workforce.models import Agent



User = get_user_model()





def minimal_xlsx(lan: str = "agent001") -> bytes:

    df = pd.DataFrame(

        [

            {

                "BLOCO": 1,

                "COLABORADOR": "Agente Teste",

                "MATRÍCULA": lan,

                "LIDERANCA": "0",

                "HORÁRIO": "08:00 - 14:00",

                "ATIVIDADE": "Teste",

                "UF": "Brasília",

                "EQUIPE": "CONFER",

                "01/06/2026": "",

                "02/06/2026": "FOLGA",

            }

        ]

    )

    buf = BytesIO()

    with pd.ExcelWriter(buf, engine="openpyxl") as writer:

        df.to_excel(writer, sheet_name="__JUNHO26", index=False)

    return buf.getvalue()





@override_settings(ACCESS_ENFORCEMENT=True)
class EscalaImportApiTests(TestCase):

    def setUp(self):

        self.client = APIClient()
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_GERENCIA))

        self.planner = User.objects.create_user(

            username="planning.staff",

            email="planning@test.local",

            password="test1234",

        )
        self.planner.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_GERENCIA)))

        self.regular = User.objects.create_user(

            username="operador.reg",

            email="operador@test.local",

            password="test1234",

        )

        Agent.objects.create(

            user_lan_id="agent001",

            full_name="Agente Teste",

            active=True,

        )



    def _upload(self, content: bytes, user):

        self.client.force_authenticate(user=user)

        upload = SimpleUploadedFile(

            "test.xlsx",

            content,

            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",

        )

        return self.client.post(

            "/api/v1/escala-flex/planejamento/import/",

            {"file": upload},

            format="multipart",

        )



    def test_import_requires_auth(self):

        content = minimal_xlsx()

        upload = SimpleUploadedFile("test.xlsx", content)

        response = self.client.post(

            "/api/v1/escala-flex/planejamento/import/",

            {"file": upload},

            format="multipart",

        )

        self.assertIn(response.status_code, (401, 403))



    def test_import_forbidden_for_regular_user(self):

        response = self._upload(minimal_xlsx(), self.regular)

        self.assertEqual(response.status_code, 403)



    @override_settings(ESCALA_IMPORT_ASYNC=False)

    def test_import_success_for_staff(self):

        response = self._upload(minimal_xlsx(), self.planner)

        self.assertEqual(response.status_code, 201)

        self.assertIn("__JUNHO26", response.data["sheets_processed"])

        self.assertEqual(response.data["rows_upserted"], 2)



    @override_settings(ESCALA_IMPORT_ASYNC=False)

    def test_imports_list(self):

        self._upload(minimal_xlsx(), self.planner)

        self.client.force_authenticate(user=self.planner)

        response = self.client.get("/api/v1/escala-flex/planejamento/imports/")

        self.assertEqual(response.status_code, 200)

        self.assertGreaterEqual(len(response.data), 1)

        self.assertEqual(response.data[0]["status"], "completed")



    @override_settings(ESCALA_IMPORT_ASYNC=True)

    @patch("apps.escala_flex.planning_views.schedule_import_job")

    def test_import_async_returns_202(self, mock_schedule):

        def run_sync(batch_id, content, filename, user_id):

            _run_import_job(batch_id, content, filename, user_id)



        mock_schedule.side_effect = run_sync

        response = self._upload(minimal_xlsx(), self.planner)

        self.assertEqual(response.status_code, 202)

        self.assertEqual(response.data["status"], "processing")

        batch_id = response.data["batch_id"]



        self.client.force_authenticate(user=self.planner)

        detail = self.client.get(f"/api/v1/escala-flex/planejamento/imports/{batch_id}/")

        self.assertEqual(detail.status_code, 200)

        self.assertEqual(detail.data["status"], "completed")

        self.assertEqual(detail.data["rows_upserted"], 2)



    @override_settings(ESCALA_IMPORT_ASYNC=True)

    def test_import_conflict_when_already_processing(self):

        EscalaImportBatch.objects.create(

            filename="pending.xlsx",

            uploaded_by=self.planner,

            status=EscalaImportBatch.STATUS_PROCESSING,

        )

        response = self._upload(minimal_xlsx(), self.planner)

        self.assertEqual(response.status_code, 409)

