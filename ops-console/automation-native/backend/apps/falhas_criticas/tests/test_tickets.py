# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings

from apps.falhas_criticas.constants import GROUP_GLOBAL
from apps.falhas_criticas.models import PortalTicket

User = get_user_model()


class FalhasTicketAPITests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name=GROUP_GLOBAL)
        self.user = User.objects.create_user("ticket_user", password="test123")
        self.user.groups.add(Group.objects.get(name=GROUP_GLOBAL))
        self.client = Client()
        self.client.force_login(self.user)

    def test_create_and_list_tickets(self):
        create = self.client.post(
            "/api/v1/falhas/tickets/",
            data={"title": "Bug no dashboard", "description": "Detalhe"},
            content_type="application/json",
        )
        self.assertEqual(create.status_code, 201)
        ticket_id = create.json()["ticket"]["id"]

        listing = self.client.get("/api/v1/falhas/tickets/")
        self.assertEqual(listing.status_code, 200)
        data = listing.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["title"], "Bug no dashboard")

        detail = self.client.get(f"/api/v1/falhas/tickets/{ticket_id}/")
        self.assertEqual(detail.status_code, 200)

        comment = self.client.post(
            f"/api/v1/falhas/tickets/{ticket_id}/comments/",
            data={"body": "Comentário teste"},
            content_type="application/json",
        )
        self.assertEqual(comment.status_code, 201)
        self.assertEqual(len(comment.json()["comments"]), 1)

    def test_ticket_isolated_per_user(self):
        other = User.objects.create_user("other_user", "other@test.com", "test123")
        other.groups.add(Group.objects.get(name=GROUP_GLOBAL))
        PortalTicket.objects.create(title="Privado", created_by=other)

        response = self.client.get("/api/v1/falhas/tickets/")
        self.assertEqual(response.json()["total"], 0)
