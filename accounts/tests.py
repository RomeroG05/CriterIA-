from django.test import TestCase
from django.urls import reverse

from .models import User


class AccountTests(TestCase):
    data = {"first_name": "Ana", "last_name": "Ruiz", "email": "Ana@Example.com", "institution": "UTPL", "role": "student",
            "preferred_language": "pt", "password1": "clave-Segura-913", "password2": "clave-Segura-913"}

    def test_register_logs_in_and_stores_profile(self):
        response = self.client.post(reverse("accounts:register"), self.data)
        self.assertRedirects(response, reverse("chat:home"))
        user = User.objects.get(email="ana@example.com")
        self.assertEqual((user.institution, user.preferred_language), ("UTPL", "pt"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertEqual(response.cookies["django_language"].value, "pt")

    def test_duplicate_email_rejected_regardless_of_case(self):
        User.objects.create_user("ana@example.com", "x")
        response = self.client.post(reverse("accounts:register"), self.data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_login_is_case_insensitive(self):
        User.objects.create_user("ana@example.com", "clave-Segura-913")
        response = self.client.post(reverse("accounts:login"), {"username": "ANA@example.com", "password": "clave-Segura-913"})
        self.assertRedirects(response, reverse("chat:home"))

    def test_profile_requires_login(self):
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 302)
