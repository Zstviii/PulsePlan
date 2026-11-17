from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import DailyCalories, Workout

User = get_user_model()


class WorkoutOwnershipTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="Password123!")
        self.bob = User.objects.create_user(username="bob", password="Password123!")
        self.alice_workout = Workout.objects.create(
            user=self.alice,
            title="Тренировка Алисы",
        )

    def test_other_user_cannot_toggle_status(self):
        self.client.force_login(self.bob)
        url = reverse("workout_toggle_status", args=[self.alice_workout.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    def test_other_user_cannot_add_exercise(self):
        self.client.force_login(self.bob)
        url = reverse("exercise_create", args=[self.alice_workout.id])
        response = self.client.post(url, {"name": "Жим лёжа", "sets": 3, "reps": 10})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.alice_workout.exercises.count(), 0)

    def test_other_user_cannot_link_calories_to_foreign_workout(self):
        self.client.force_login(self.bob)
        url = reverse("calorie_create")
        response = self.client.post(
            url,
            {
                "calories_burned": 300,
                "workout_id": self.alice_workout.id,
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(DailyCalories.objects.count(), 0)


class AuthAndDashboardTests(TestCase):
    def setUp(self):
        self.user_data = {
            "username": "testuser",
            "password": "StrongPassword123!",
        }

    def test_user_registration(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "newuser",
                "password": "StrongPassword123!",
                "password1": "StrongPassword123!",
                "password2": "StrongPassword123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="newuser").exists())

    def test_user_login(self):
        User.objects.create_user(**self.user_data)
        response = self.client.post(reverse("login"), self.user_data)
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_dashboard_access(self):
        url = reverse("dashboard")
        response_anon = self.client.get(url)
        self.assertEqual(response_anon.status_code, 302)

        user = User.objects.create_user(**self.user_data)
        self.client.force_login(user)
        response_auth = self.client.get(url)
        self.assertEqual(response_auth.status_code, 200)


class WorkoutFunctionalityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="athlete", password="Password123!")
        self.client.force_login(self.user)
        self.workout = Workout.objects.create(
            user=self.user,
            title="Утренняя пробежка",
            status=Workout.Status.PLANNED,
        )

    def test_create_workout(self):
        response = self.client.post(
            reverse("workout_create"),
            {
                "title": "Силовая тренировка",
                "date": "2026-11-20",
                "time": "18:30",
                "notes": "День ног",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Workout.objects.filter(title="Силовая тренировка", user=self.user).exists()
        )

    def test_toggle_workout_status(self):
        initial_status = self.workout.status
        url = reverse("workout_toggle_status", args=[self.workout.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

        self.workout.refresh_from_db()
        self.assertNotEqual(self.workout.status, initial_status)

    def test_add_exercise_to_workout(self):
        url = reverse("exercise_create", args=[self.workout.id])
        response = self.client.post(
            url,
            {
                "name": "Приседания",
                "sets": 4,
                "reps": 12,
                "weight_kg": 60.5,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.workout.exercises.count(), 1)

    def test_add_calories(self):
        response = self.client.post(
            reverse("calorie_create"),
            {
                "calories_burned": 450,
                "workout_id": self.workout.id,
                "activity_note": "Кардио после силовой",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DailyCalories.objects.count(), 1)

    def test_invalid_data_handling(self):
        self.client.post(reverse("workout_create"), {"title": "   "})
        self.assertEqual(Workout.objects.count(), 1)

        url_ex = reverse("exercise_create", args=[self.workout.id])
        self.client.post(
            url_ex,
            {"name": "Планка", "sets": 0, "reps": -5, "weight_kg": -10},
        )
        self.assertEqual(self.workout.exercises.count(), 0)

        self.client.post(reverse("calorie_create"), {"calories_burned": 0})
        self.client.post(reverse("calorie_create"), {"calories_burned": 25000})
        self.assertEqual(DailyCalories.objects.count(), 0)
