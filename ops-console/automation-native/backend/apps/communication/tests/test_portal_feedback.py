import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from apps.communication.models import PortalFeedback


User = get_user_model()


@pytest.mark.django_db
def test_authenticated_user_can_create_portal_feedback():
    user = User.objects.create_user(
        username="colaborador",
        email="colaborador.feedback@example.com",
        password="x",
    )
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/v1/feedbacks-onboarding/",
        {
            "kind": "sugestao",
            "subject": "Melhorar a busca",
            "description": "Seria útil pesquisar por palavras relacionadas.",
            "allow_contact": "true",
            "page_path": "/home",
            "module_id": "portal-geral",
        },
        format="multipart",
    )

    assert response.status_code == 201, response.data
    assert response.data["protocol"].startswith("ONBP-")
    feedback = PortalFeedback.objects.get(pk=response.data["id"])
    assert feedback.created_by == user
    assert feedback.status == PortalFeedback.Status.NEW


@pytest.mark.django_db
def test_anonymous_user_cannot_create_portal_feedback():
    response = APIClient().post(
        "/api/v1/feedbacks-onboarding/",
        {"kind": "duvida", "subject": "Ajuda", "description": "Como utilizar o portal?"},
        format="json",
    )
    assert response.status_code in {401, 403}
    assert PortalFeedback.objects.count() == 0


@pytest.mark.django_db
@override_settings(ACCESS_ENFORCEMENT=True)
def test_regular_user_cannot_list_feedbacks():
    user = User.objects.create_user(username="sem_acesso", email="sem-acesso@example.com", password="x")
    client = APIClient()
    client.force_authenticate(user=user)
    assert client.get("/api/v1/feedbacks-onboarding/").status_code == 403


@pytest.mark.django_db
@override_settings(ACCESS_ENFORCEMENT=True)
def test_admin_can_list_summarize_and_update_feedback():
    author = User.objects.create_user(username="autor", email="autor@example.com", password="x")
    admin = User.objects.create_superuser(username="admin_feedback", email="admin@example.com", password="x")
    feedback = PortalFeedback.objects.create(
        created_by=author,
        kind=PortalFeedback.Kind.PROBLEM,
        subject="Erro ao abrir página",
        description="A página não carregou.",
        page_path="/operacao",
    )
    client = APIClient()
    client.force_authenticate(user=admin)

    listed = client.get("/api/v1/feedbacks-onboarding/")
    summarized = client.get("/api/v1/feedbacks-onboarding/summary/")
    updated = client.patch(
        f"/api/v1/feedbacks-onboarding/{feedback.id}/",
        {"status": "em_analise", "admin_note": "Validação iniciada."},
        format="json",
    )

    assert listed.status_code == 200, listed.data
    assert listed.data["count"] == 1
    assert summarized.status_code == 200, summarized.data
    assert summarized.data == {"total": 1, "new": 1, "in_review": 0, "answered": 0, "completed": 0}
    assert updated.status_code == 200, updated.data
    feedback.refresh_from_db()
    assert feedback.status == PortalFeedback.Status.IN_REVIEW
    assert feedback.handled_by == admin
    assert feedback.handled_at is not None
