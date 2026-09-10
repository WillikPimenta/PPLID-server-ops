from django.contrib.auth import get_user_model
from django.test import Client, TestCase


User = get_user_model()


class AuthenticationFlowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="c90001a",
            password="correct-password",
            first_name="Pessoa",
            last_name="Teste",
            is_active=True,
        )

    def login(self, username="c90001a", password="correct-password"):
        return self.client.post(
            "/api/v1/auth/login/",
            {"username": username, "password": password},
            content_type="application/json",
        )

    def test_login_success_creates_session_and_returns_user_payload(self):
        response = self.login(username=" C90001A ")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["username"], "c90001a")
        self.assertEqual(data["display_name"], "Pessoa")
        self.assertTrue(data["csrfToken"])
        self.assertIn("access", data)

        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.json()["authenticated"])
        self.assertEqual(me.json()["user"]["username"], "c90001a")

    def test_invalid_credentials_do_not_create_session(self):
        response = self.login(password="wrong-password")

        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())

        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertFalse(me.json()["authenticated"])
        self.assertIsNone(me.json()["user"])

    def test_me_reports_anonymous_session_before_login(self):
        response = self.client.get("/api/v1/auth/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"authenticated": False, "user": None})

    def test_logout_ends_current_session(self):
        self.client.force_login(self.user)

        response = self.client.post("/api/v1/auth/logout/", content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"detail": "ok"})
        me = self.client.get("/api/v1/auth/me/")
        self.assertFalse(me.json()["authenticated"])
        self.assertIsNone(me.json()["user"])
