from django.test import TestCase

from apps.accounts.models import User
from apps.accounts.serializers import AuthUserSerializer


class AuthUserSerializerTests(TestCase):
    def test_display_name_uses_first_name_only(self):
        user = User(
            username="c19011q",
            first_name="Abel",
            last_name="Martins Dos Santos Neto",
        )
        data = AuthUserSerializer(user).data
        self.assertEqual(data["display_name"], "Abel")

    def test_display_name_falls_back_to_last_name(self):
        user = User(username="agent001", first_name="", last_name="Silva")
        data = AuthUserSerializer(user).data
        self.assertEqual(data["display_name"], "Silva")

    def test_display_name_falls_back_to_username(self):
        user = User(username="c91763a", first_name="", last_name="")
        data = AuthUserSerializer(user).data
        self.assertEqual(data["display_name"], "c91763a")

    def test_display_name_uses_first_token_of_first_name(self):
        user = User(username="user01", first_name="Abel Martins", last_name="")
        data = AuthUserSerializer(user).data
        self.assertEqual(data["display_name"], "Abel")
