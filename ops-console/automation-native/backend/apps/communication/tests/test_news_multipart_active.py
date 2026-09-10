"""Multipart create must not force active=False (HTML boolean quirk)."""

from datetime import date
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.communication.models import News


User = get_user_model()


def _jpeg_file(name: str = "probe.jpg") -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (32, 32), color=(10, 20, 30)).save(buf, format="JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


@pytest.mark.django_db
@override_settings(ACCESS_ENFORCEMENT=False)
def test_multipart_create_keeps_active_true():
    user = User.objects.create_user(username="news_editor", password="x")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/v1/news/",
        {
            "title": "Multipart active probe",
            "category": "Geral",
            "author": "QA",
            "summary": "resumo",
            "content": "conteudo",
            "published_at": date.today().isoformat(),
            "source_url": "",
            "is_critical": "true",
            "images": _jpeg_file(),
        },
        format="multipart",
    )

    assert response.status_code == 201, response.data
    news = News.objects.get(pk=response.data["id"])
    assert news.active is True
    assert news.is_critical is True
    assert news.images.count() == 1
