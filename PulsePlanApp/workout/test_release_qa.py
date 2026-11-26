import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connections
from django.templatetags.static import static
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import DailyCalories, Exercise, Workout


User = get_user_model()


class RepeatedCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="repeat_qa")
        cls.day = timezone.localdate() - timedelta(days=2)

    def setUp(self):
        self.client.force_login(self.user)
        self.dashboard_url = f"{reverse('dashboard')}?date={self.day.isoformat()}&mode=month&day={self.day.day}"

    def test_workout_and_manual_calories_create_once_per_dashboard_form(self):
        for route, token_name, fields, records, list_key in (
            ("workout_create", "workout_submission_token", {"title": "Круговая", "time": "10:15"},
             self.user.workouts.all(), "selected_day_workouts"),
            ("calorie_create", "calorie_submission_token", {"calories_burned": 180, "activity_note": "Прогулка"},
             self.user.calorie_logs.filter(workout=None), "activity_calorie_logs"),
        ):
            with self.subTest(route=route):
                dashboard = self.client.get(self.dashboard_url)
                payload = {
                    **fields,
                    "date": self.day.isoformat(),
                    "mode": "month",
                    "submission_token": str(dashboard.context[token_name]),
                }
                url = reverse(route)
                for _ in range(2):
                    self.assertRedirects(self.client.post(url, payload), self.dashboard_url)

                refreshed = self.client.get(self.dashboard_url)
                record = records.get()
                self.assertEqual(refreshed.context[list_key], [record])

                # A fresh form is a separate submission, even when the values match.
                new_token = str(refreshed.context[token_name])
                self.assertNotEqual(new_token, payload["submission_token"])
                self.assertRedirects(
                    self.client.post(url, {**payload, "submission_token": new_token}),
                    self.dashboard_url,
                )
                self.assertEqual(records.count(), 2)

    def test_correcting_invalid_calories_can_reuse_the_original_token(self):
        token = str(self.client.get(self.dashboard_url).context["calorie_submission_token"])
        url = reverse("calorie_create")
        payload = {"date": self.day.isoformat(), "mode": "month", "submission_token": token}

        invalid = self.client.post(url, {**payload, "calories_burned": 0})
        self.assertContains(invalid, "Количество калорий должно быть больше 0.")
        self.assertEqual(str(invalid.context["submission_token"]), token)
        self.assertFalse(DailyCalories.objects.filter(user=self.user).exists())

        payload["calories_burned"] = 220
        for _ in range(2):
            self.assertRedirects(self.client.post(url, payload), self.dashboard_url)
        self.assertEqual(list(self.user.calorie_logs.values_list("calories_burned", flat=True)), [220])

    def test_repeating_an_old_linked_calorie_post_preserves_the_newer_value(self):
        workout = Workout.objects.create(
            user=self.user,
            title="Силовая",
            scheduled_at=timezone.make_aware(datetime.combine(self.day, time(12))),
            status=Workout.Status.COMPLETED,
        )
        url = reverse("calorie_create")
        old_payload = {
            "date": self.day.isoformat(),
            "mode": "month",
            "workout_id": workout.pk,
            "calories_burned": 310,
            "submission_token": str(self.client.get(self.dashboard_url).context["calorie_submission_token"]),
        }
        self.assertRedirects(self.client.post(url, old_payload), self.dashboard_url)
        entry = DailyCalories.objects.get(workout=workout)
        new_payload = {
            **old_payload,
            "submission_token": str(self.client.get(self.dashboard_url).context["calorie_submission_token"]),
            "calories_burned": 350,
        }
        self.assertRedirects(self.client.post(url, new_payload), self.dashboard_url)
        self.assertRedirects(self.client.post(url, old_payload), self.dashboard_url)
        self.assertEqual(workout.calorie_entries.get().pk, entry.pk)
        entry.refresh_from_db()
        self.assertEqual(entry.calories_burned, 350)


class ConcurrentCreationTests(TransactionTestCase):
    def test_simultaneous_workout_and_manual_calorie_retries_create_one_record_each(self):
        user = User.objects.create_user(username="concurrent_qa")
        self.client.force_login(user)
        session_cookie = settings.SESSION_COOKIE_NAME
        session = self.client.cookies[session_cookie].value
        dashboard = self.client.get(reverse("dashboard"))
        for route, token_name, fields, records in (
            ("workout_create", "workout_submission_token", {"title": "Бег"}, user.workouts.all()),
            ("calorie_create", "calorie_submission_token", {"calories_burned": 250}, user.calorie_logs.all()),
        ):
            with self.subTest(route=route):
                url = reverse(route)
                payload = {
                    **fields,
                    "date": dashboard.context["selected_date"].isoformat(),
                    "submission_token": str(dashboard.context[token_name]),
                }
                ready = Barrier(2, timeout=10)

                def post_duplicate(_):
                    client = Client()
                    client.cookies[session_cookie] = session
                    try:
                        ready.wait()
                        response = client.post(url, payload)
                        return response.status_code, response.headers.get("Location")
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    responses = list(executor.map(post_duplicate, range(2)))

                self.assertEqual([status for status, _ in responses], [302, 302])
                self.assertEqual(responses[0][1], responses[1][1])
                self.assertEqual(records.count(), 1)


class UpdatedFormFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="forms_qa")
        cls.day = timezone.localdate() - timedelta(days=3)
        cls.workout = Workout.objects.create(
            user=cls.user,
            title="Исходная тренировка",
            scheduled_at=timezone.make_aware(datetime.combine(cls.day, time(12))),
            status=Workout.Status.COMPLETED,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_workout_edit_preserves_form_input_when_date_and_time_are_invalid(self):
        url = reverse("workout_update", args=[self.workout.pk])
        payload = {"title": "Новое название", "date": "2026-02-30", "time": "25:75", "notes": "Новая заметка"}
        response = self.client.post(url, payload)

        self.assertTemplateUsed(response, "workout/entry_form.html")
        self.assertFormError(response.context["form"], "date", "Некорректный формат даты.")
        self.assertFormError(response.context["form"], "time", "Некорректный формат времени (используйте ЧЧ:ММ).")
        for value in payload.values():
            self.assertContains(response, value)
        self.workout.refresh_from_db()
        self.assertEqual(self.workout.title, "Исходная тренировка")
        self.assertEqual(timezone.localtime(self.workout.scheduled_at).date(), self.day)
        self.assertEqual(self.workout.notes, "")

    def test_exercise_create_and_edit_keep_values_and_show_field_errors(self):
        exercise = Exercise.objects.create(workout=self.workout, name="Отжимания", sets=3)
        payload = {"name": "Подтягивания", "sets": "много", "reps": 14, "weight_kg": "12.5"}
        for url in (
            reverse("exercise_create", args=[self.workout.pk]),
            reverse("exercise_update", args=[exercise.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.post(url, payload)
                self.assertTemplateUsed(response, "workout/entry_form.html")
                self.assertFormError(response.context["form"], "sets", "Количество подходов должно быть целым числом.")
                for value in payload.values():
                    self.assertContains(response, f'value="{value}"')
        self.assertEqual(list(self.workout.exercises.values_list("name", "sets")), [("Отжимания", 3)])

    def test_invalid_calorie_edit_keeps_submitted_fields_and_the_saved_entry(self):
        entry = DailyCalories.objects.create(user=self.user, date=self.day, calories_burned=180)
        response = self.client.post(reverse("calorie_update", args=[entry.pk]), {
            "date": self.day.isoformat(),
            "calories_burned": "много",
            "activity_note": "Длинная прогулка",
        })

        self.assertTemplateUsed(response, "workout/entry_form.html")
        self.assertFormError(response.context["form"], "calories_burned", "Количество калорий должно быть целым числом.")
        self.assertContains(response, 'value="много"')
        self.assertContains(response, "Длинная прогулка")
        entry.refresh_from_db()
        self.assertEqual((entry.date, entry.calories_burned, entry.activity_note), (self.day, 180, ""))

    def test_calendar_moves_only_workout_calories_between_days(self):
        new_day = self.day + timedelta(days=1)
        linked = DailyCalories.objects.create(user=self.user, workout=self.workout, calories_burned=320)
        DailyCalories.objects.create(user=self.user, date=self.day, calories_burned=140)
        self.assertRedirects(
            self.client.post(reverse("workout_update", args=[self.workout.pk]), {
                "title": self.workout.title, "date": new_day.isoformat(), "time": "00:00", "mode": "month",
            }),
            f"{reverse('dashboard')}?date={new_day.isoformat()}&mode=month&day={new_day.day}",
        )

        linked.refresh_from_db()
        self.assertEqual(linked.date, new_day)
        for mode in ("week", "month"):
            with self.subTest(mode=mode):
                old = self.client.get(reverse("dashboard"), {"date": self.day.isoformat(), "mode": mode})
                new = self.client.get(reverse("dashboard"), {"date": new_day.isoformat(), "mode": mode})
                self.assertEqual(old.context["selected_day_workouts"], [])
                self.assertEqual(old.context["workout_calorie_logs"], [])
                self.assertEqual(old.context["selected_day_total_calories"], 140)
                self.assertEqual(new.context["selected_day_workouts"], [self.workout])
                self.assertEqual(new.context["workout_calorie_logs"], [linked])
                self.assertEqual(new.context["activity_calorie_logs"], [])
                self.assertEqual(new.context["selected_day_total_calories"], 320)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RegistrationAndPasswordRecoveryTests(TestCase):
    def test_register_then_reset_password_with_the_emailed_link(self):
        email = "register-qa@example.com"
        original_password = "Steam-Lantern-83!"
        new_password = "Cycling-Bridge-402!"
        register = self.client.get(reverse("register"))
        self.assertContains(register, '<h1 class="auth-title">Создать аккаунт</h1>', html=True)
        for asset in ("workout/vendor/bootstrap.min.css", "workout/vendor/bootstrap.bundle.min.js", "workout/js/app.js"):
            self.assertContains(register, static(asset), count=1)
        registration_data = {"username": "registered_qa", "email": email, "password1": original_password}
        invalid = self.client.post(reverse("register"), {**registration_data, "password2": "different"})
        self.assertContains(invalid, f'value="{email}"')
        self.assertIn("password2", invalid.context["form"].errors)
        self.assertFalse(User.objects.filter(email=email).exists())
        self.assertRedirects(
            self.client.post(reverse("register"), {**registration_data, "password2": original_password}),
            reverse("dashboard"),
        )
        self.assertEqual(self.client.session["_auth_user_id"], str(User.objects.get(email=email).pk))
        self.assertRedirects(self.client.post(reverse("logout")), reverse("login"))

        self.assertRedirects(
            self.client.post(reverse("password_reset"), {"email": email}, secure=True),
            reverse("password_reset_done"),
        )
        message, = mail.outbox
        self.assertEqual(message.to, [email])
        reset_path = urlsplit(re.search(r"https://[^\s]+", message.body).group()).path
        confirmation = self.client.get(reset_path, follow=True)
        self.assertTemplateUsed(confirmation, "workout/password_reset_confirm.html")
        self.assertTrue(confirmation.context["validlink"])
        sanitized_path = confirmation.request["PATH_INFO"]
        self.assertNotEqual(sanitized_path, reset_path)

        self.assertRedirects(
            self.client.post(sanitized_path, {"new_password1": new_password, "new_password2": new_password}),
            reverse("password_reset_complete"),
        )
        self.assertFalse(self.client.get(reset_path).context["validlink"])
        self.assertFalse(self.client.login(username="registered_qa", password=original_password))
        self.assertTrue(self.client.login(username="registered_qa", password=new_password))

    def test_unknown_email_shows_the_same_confirmation_without_sending_a_message(self):
        self.assertRedirects(
            self.client.post(reverse("password_reset"), {"email": "unknown@example.com"}),
            reverse("password_reset_done"),
        )
        self.assertEqual(mail.outbox, [])
