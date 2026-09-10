from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import ROLE_OP_AGENTE, ROLE_PROC_USUARIO, role_group_name
from apps.communication.models import News


User = get_user_model()


def _user_with_role(username: str, role: str):
    user = User.objects.create_user(username=username, password="x")
    group, _ = Group.objects.get_or_create(name=role_group_name(role))
    user.groups.add(group)
    return user


def _payload(**overrides):
    payload = {
        "title": "Atualização do suporte",
        "category": "Geral",
        "author": "Ignorado pelo serializer",
        "summary": "Orientação operacional atualizada.",
        "content": "Consulte o procedimento antes de encaminhar a solicitação.",
        "published_at": (timezone.now() + timedelta(days=2)).isoformat(),
        "source_url": "",
        "is_critical": True,
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
@override_settings(ACCESS_ENFORCEMENT=True)
def test_operational_support_agent_can_publish_only_immediate_operation_news():
    user = _user_with_role("support_news_agent", ROLE_OP_AGENTE)
    client = APIClient()
    client.force_authenticate(user=user)
    before = timezone.now()

    response = client.post("/api/v1/news/", _payload(), format="json")

    assert response.status_code == 201, response.data
    news = News.objects.get(pk=response.data["id"])
    assert news.category == News.Category.OPERACAO
    assert news.is_critical is False
    assert before <= news.published_at <= timezone.now()
    assert news.created_by_id == user.id


@pytest.mark.django_db
@override_settings(ACCESS_ENFORCEMENT=True)
def test_user_outside_operational_support_cannot_publish_news():
    user = _user_with_role("non_support_news_user", ROLE_PROC_USUARIO)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post("/api/v1/news/", _payload(), format="json")

    assert response.status_code == 403
