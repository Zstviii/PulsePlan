import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse


DOMAIN = "pulseplan.treidendev.ru"
DOMAIN_ORIGIN = f"https://{DOMAIN}"


@override_settings(
    ALLOWED_HOSTS=[DOMAIN, "localhost"],
    CSRF_TRUSTED_ORIGINS=[DOMAIN_ORIGIN],
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    SECURE_SSL_REDIRECT=True,
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
)
class ProductionHTTPSBehaviorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="https-user",
            password="StrongPassword123!",
        )

    def test_http_request_redirects_to_https(self):
        response = self.client.get(
            reverse("login"),
            HTTP_HOST=DOMAIN,
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], f"{DOMAIN_ORIGIN}{reverse('login')}")

    def test_proxy_https_login_sets_secure_session_cookie(self):
        response = self.client.post(
            reverse("login"),
            {"username": "https-user", "password": "StrongPassword123!"},
            HTTP_HOST=DOMAIN,
            HTTP_X_FORWARDED_PROTO="https",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertTrue(response.cookies["sessionid"]["secure"])

    def test_trusted_origin_passes_csrf_and_untrusted_origin_is_rejected(self):
        trusted_client = Client(enforce_csrf_checks=True)
        trusted_response = trusted_client.get(
            reverse("login"),
            HTTP_HOST="localhost",
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertTrue(trusted_response.cookies["csrftoken"]["secure"])
        csrf_token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            trusted_response.content.decode(),
        ).group(1)

        post_data = {
            "username": "https-user",
            "password": "StrongPassword123!",
            "csrfmiddlewaretoken": csrf_token,
        }
        accepted = trusted_client.post(
            reverse("login"),
            post_data,
            HTTP_HOST="localhost",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_ORIGIN=DOMAIN_ORIGIN,
        )
        self.assertEqual(accepted.status_code, 302)

        untrusted_client = Client(enforce_csrf_checks=True)
        untrusted_response = untrusted_client.get(
            reverse("login"),
            HTTP_HOST="localhost",
            HTTP_X_FORWARDED_PROTO="https",
        )
        csrf_token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            untrusted_response.content.decode(),
        ).group(1)
        rejected = untrusted_client.post(
            reverse("login"),
            {
                **post_data,
                "csrfmiddlewaretoken": csrf_token,
            },
            HTTP_HOST="localhost",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_ORIGIN="https://attacker.example",
        )
        self.assertEqual(rejected.status_code, 403)
