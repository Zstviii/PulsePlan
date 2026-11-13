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
