"""Endpoint /news/scheduled/ — só criador ou admin vê agendamentos."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.communication.models import News


User = get_user_model()


def _make_news(*, title: str, created_by, published_at, active=True):
    return News.objects.create(
        title=title,
        category="Geral",
        author="QA",
        summary="resumo",
        content="conteudo",
        published_at=published_at,
        active=active,
        created_by=created_by,
    )


@pytest.mark.django_db
@override_settings(ACCESS_ENFORCEMENT=False)
def test_scheduled_lists_only_future_of_creator():
    creator = User.objects.create_user(
        username="sched_creator", email="sched_creator@example.com", password="x"
    )
    other = User.objects.create_user(
        username="sched_other", email="sched_other@example.com", password="x"
    )
    now = timezone.now()

    mine = _make_news(title="Minha futura", created_by=creator, published_at=now + timedelta(days=1))
    _make_news(title="De outro", created_by=other, published_at=now + timedelta(days=2))
    _make_news(title="Já publicada", created_by=creator, published_at=now - timedelta(hours=1))

    client = APIClient()
    client.force_authenticate(user=creator)
    response = client.get("/api/v1/news/scheduled/")

    assert response.status_code == 200, response.data
    results = response.data["results"] if isinstance(response.data, dict) else response.data
    ids = {str(row["id"]) for row in results}
    assert ids == {str(mine.id)}
    assert str(results[0]["created_by_id"]) == str(creator.id)


@pytest.mark.django_db
@override_settings(ACCESS_ENFORCEMENT=False)
def test_scheduled_admin_sees_all_future():
    admin = User.objects.create_user(
        username="sched_adm", email="sched_adm@example.com", password="x"
    )
    group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
    admin.groups.add(group)

    creator = User.objects.create_user(
        username="sched_creator2", email="sched_creator2@example.com", password="x"
    )
    now = timezone.now()
    a = _make_news(title="A", created_by=creator, published_at=now + timedelta(days=1))
    b = _make_news(title="B", created_by=admin, published_at=now + timedelta(days=3))

    client = APIClient()
    client.force_authenticate(user=admin)
    response = client.get("/api/v1/news/scheduled/")

    assert response.status_code == 200, response.data
    results = response.data["results"] if isinstance(response.data, dict) else response.data
    ids = {str(row["id"]) for row in results}
    assert {str(a.id), str(b.id)}.issubset(ids)
