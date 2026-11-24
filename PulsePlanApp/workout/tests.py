import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from seed_pulseplan import DEMO_USERNAME, run_seed

from .forms import DailyCaloriesForm, ExerciseForm, WorkoutForm
from .models import (
    DailyCalories,
    Exercise,
    ExerciseResult,
    TemplateExercise,
    Workout,
    WorkoutTemplate,
)


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

    def test_owner_can_edit_workout_and_move_its_calorie_date(self):
        self.client.force_login(self.alice)
        self.alice_workout.status = Workout.Status.COMPLETED
        self.alice_workout.save(update_fields=["status"])
        calorie = DailyCalories.objects.create(
            user=self.alice,
            workout=self.alice_workout,
            calories_burned=300,
        )
        target_date = timezone.localdate() + timedelta(days=2)

        response = self.client.post(
            reverse("workout_update", args=[self.alice_workout.id]),
            {
                "title": "Измененная тренировка",
                "date": target_date.isoformat(),
                "time": "10:30",
                "notes": "Новая заметка",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.alice_workout.refresh_from_db()
        calorie.refresh_from_db()
        self.assertEqual(self.alice_workout.title, "Измененная тренировка")
        self.assertEqual(
            timezone.localtime(self.alice_workout.scheduled_at).date(), target_date
        )
        self.assertEqual(calorie.date, target_date)
        self.assertEqual(calorie.calories_burned, 300)
        self.assertEqual(Workout.objects.count(), 1)

    def test_other_user_cannot_edit_or_delete_workout(self):
        self.client.force_login(self.bob)
        edit_response = self.client.post(
            reverse("workout_update", args=[self.alice_workout.id]),
            {"title": "Подмена", "date": "2030-01-01", "time": "10:00"},
        )
        delete_response = self.client.post(
            reverse("workout_delete", args=[self.alice_workout.id])
        )

        self.assertEqual(edit_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.alice_workout.refresh_from_db()
        self.assertEqual(self.alice_workout.title, "Тренировка Алисы")

    def test_owner_can_create_workout(self):
        self.client.force_login(self.alice)

        response = self.client.post(
            reverse("workout_create"),
            {
                "title": "Новая тренировка",
                "date": timezone.localdate().isoformat(),
                "time": "18:30",
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Workout.objects.filter(user=self.alice).count(), 2)
        self.assertTrue(
            Workout.objects.filter(user=self.alice, title="Новая тренировка").exists()
        )

    def test_invalid_workout_edit_does_not_change_existing_data(self):
        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("workout_update", args=[self.alice_workout.id]),
            {"title": "   ", "date": "not-a-date", "time": "10:00"},
        )

        self.assertEqual(response.status_code, 302)
        self.alice_workout.refresh_from_db()
        self.assertEqual(self.alice_workout.title, "Тренировка Алисы")

    def test_workout_edit_form_cannot_change_status(self):
        self.client.force_login(self.alice)

        self.client.post(
            reverse("workout_update", args=[self.alice_workout.id]),
            {
                "title": self.alice_workout.title,
                "date": timezone.localdate().isoformat(),
                "time": "18:00",
                "status": Workout.Status.COMPLETED,
            },
        )

        self.alice_workout.refresh_from_db()
        self.assertEqual(self.alice_workout.status, Workout.Status.PLANNED)

    def test_completed_workout_calorie_post_is_idempotent(self):
        self.client.force_login(self.alice)
        self.alice_workout.status = Workout.Status.COMPLETED
        self.alice_workout.save(update_fields=["status"])
        url = reverse("calorie_create")
        data = {"calories_burned": 300, "workout_id": self.alice_workout.id}

        self.client.post(url, data)
        self.client.post(url, data)

        entries = DailyCalories.objects.filter(workout=self.alice_workout)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.get().calories_burned, 300)

    def test_calories_cannot_be_logged_for_uncompleted_workout(self):
        self.client.force_login(self.alice)

        self.client.post(
            reverse("calorie_create"),
            {"calories_burned": 300, "workout_id": self.alice_workout.id},
        )

        self.assertFalse(
            DailyCalories.objects.filter(workout=self.alice_workout).exists()
        )

    def test_status_transitions_are_limited_and_repeated_completion_is_safe(self):
        self.client.force_login(self.alice)
        url = reverse("workout_toggle_status", args=[self.alice_workout.id])

        completed = self.client.post(url, {"status": Workout.Status.COMPLETED})
        repeated = self.client.post(url, {"status": Workout.Status.COMPLETED})
        self.alice_workout.status = Workout.Status.SKIPPED
        self.alice_workout.save(update_fields=["status"])
        invalid = self.client.post(url, {"status": Workout.Status.COMPLETED})

        self.assertEqual(completed.status_code, 302)
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(invalid.status_code, 302)
        self.alice_workout.refresh_from_db()
        self.assertEqual(self.alice_workout.status, Workout.Status.SKIPPED)

    def test_direct_model_save_rejects_invalid_status_transition(self):
        self.alice_workout.status = Workout.Status.SKIPPED
        self.alice_workout.save(update_fields=["status"])
        self.alice_workout.status = Workout.Status.COMPLETED

        with self.assertRaises(ValidationError):
            self.alice_workout.save(update_fields=["status"])

    def test_uncompleted_workout_calories_are_not_counted(self):
        self.client.force_login(self.alice)
        self.alice_workout.status = Workout.Status.COMPLETED
        self.alice_workout.save(update_fields=["status"])
        DailyCalories.objects.create(
            user=self.alice,
            workout=self.alice_workout,
            calories_burned=300,
        )
        self.alice_workout.status = Workout.Status.PLANNED
        self.alice_workout.save(update_fields=["status"])
        DailyCalories.objects.create(user=self.alice, calories_burned=50)

        response = self.client.get(
            reverse("dashboard"),
            {"date": timezone.localdate().isoformat()},
        )

        self.assertEqual(response.context["selected_day_total_calories"], 50)

    def test_deleting_completed_workout_removes_linked_calories_and_exercises(self):
        self.client.force_login(self.alice)
        self.alice_workout.status = Workout.Status.COMPLETED
        self.alice_workout.save(update_fields=["status"])
        self.alice_workout.exercises.create(name="Присед", sets=3, reps=10)
        self.alice_workout.exercises.create(name="Жим", sets=3, reps=8)
        DailyCalories.objects.create(
            user=self.alice,
            workout=self.alice_workout,
            calories_burned=300,
        )
        standalone = DailyCalories.objects.create(
            user=self.alice,
            calories_burned=50,
        )

        response = self.client.post(
            reverse("workout_delete", args=[self.alice_workout.id])
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Workout.objects.filter(pk=self.alice_workout.pk).exists())
        self.assertEqual(Exercise.objects.count(), 0)
        self.assertEqual(DailyCalories.objects.count(), 1)
        self.assertTrue(DailyCalories.objects.filter(pk=standalone.pk).exists())
        dashboard = self.client.get(
            reverse("dashboard"),
            {"date": timezone.localdate().isoformat()},
        )
        self.assertEqual(dashboard.context["total_completed_workouts"], 0)
        self.assertEqual(dashboard.context["selected_day_total_calories"], 50)

    def test_calorie_entry_is_hidden_from_totals_when_status_is_reverted(self):
        self.client.force_login(self.alice)
        self.alice_workout.status = Workout.Status.COMPLETED
        self.alice_workout.save(update_fields=["status"])
        url = reverse("calorie_create")
        data = {"calories_burned": 300, "workout_id": self.alice_workout.id}
        self.client.post(url, data)
        status_url = reverse("workout_toggle_status", args=[self.alice_workout.id])

        self.client.post(status_url, {"status": Workout.Status.PLANNED})
        hidden_total = self.client.get(
            reverse("dashboard"),
            {"date": timezone.localdate().isoformat()},
        ).context["selected_day_total_calories"]
        self.client.post(status_url, {"status": Workout.Status.COMPLETED})
        restored_total = self.client.get(
            reverse("dashboard"),
            {"date": timezone.localdate().isoformat()},
        ).context["selected_day_total_calories"]

        self.assertEqual(hidden_total, 0)
        self.assertEqual(restored_total, 300)
        self.assertEqual(
            DailyCalories.objects.filter(workout=self.alice_workout).count(), 1
        )

    def test_owner_can_edit_existing_exercise_without_creating_another(self):
        self.client.force_login(self.alice)
        exercise = self.alice_workout.exercises.create(name="Присед", sets=3, reps=10)

        response = self.client.post(
            reverse("exercise_update", args=[exercise.id]),
            {"name": "Приседания", "sets": 4, "reps": 8, "weight_kg": "20.0"},
        )

        self.assertEqual(response.status_code, 302)
        exercise.refresh_from_db()
        self.assertEqual(exercise.name, "Приседания")
        self.assertEqual(exercise.sets, 4)
        self.assertEqual(self.alice_workout.exercises.count(), 1)

    def test_other_user_cannot_edit_or_delete_exercise(self):
        exercise = self.alice_workout.exercises.create(name="Присед")
        self.client.force_login(self.bob)

        edit_response = self.client.post(
            reverse("exercise_update", args=[exercise.id]),
            {"name": "Подмена", "sets": 3, "reps": 10, "weight_kg": 0},
        )
        delete_response = self.client.post(
            reverse("exercise_delete", args=[exercise.id])
        )

        self.assertEqual(edit_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.assertTrue(self.alice_workout.exercises.filter(pk=exercise.pk).exists())

    def test_owner_can_delete_exercise_without_deleting_workout(self):
        exercise = self.alice_workout.exercises.create(name="Присед")
        self.client.force_login(self.alice)

        response = self.client.post(reverse("exercise_delete", args=[exercise.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Exercise.objects.filter(pk=exercise.pk).exists())
        self.assertTrue(Workout.objects.filter(pk=self.alice_workout.pk).exists())

    def test_exercise_model_save_validates_numeric_values(self):
        exercise = self.alice_workout.exercises.model(
            workout=self.alice_workout,
            name="Присед",
            sets=0,
            reps=10,
        )

        with self.assertRaises(ValidationError):
            exercise.save()


class ParallelWorkoutCalorieTests(TransactionTestCase):
    def test_parallel_posts_create_only_one_linked_calorie_entry(self):
        user = User.objects.create_user(username="parallel", password="Password123!")
        workout = Workout.objects.create(
            user=user,
            title="Параллельная тренировка",
            status=Workout.Status.COMPLETED,
        )
        client = Client()
        client.force_login(user)
        session_key = client.cookies["sessionid"].value

        def post_calories(_):
            close_old_connections()
            client = Client()
            client.cookies["sessionid"] = session_key
            try:
                response = client.post(
                    reverse("calorie_create"),
                    {"calories_burned": 300, "workout_id": workout.pk},
                    HTTP_HOST="localhost",
                )
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(post_calories, range(4)))

        self.assertEqual(responses, [302] * 4)
        self.assertEqual(DailyCalories.objects.filter(workout=workout).count(), 1)


class CalendarAndSeedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="calendar_user", password="Password123!"
        )
        self.client.force_login(self.user)

    def test_month_navigation_preserves_anchor_day_across_consecutive_months(self):
        feb_response = self.client.get(
            reverse("dashboard"),
            {"date": "2026-01-31", "mode": "month"},
        )
        self.assertEqual(feb_response.context["next_date"], date(2026, 2, 28))
        self.assertEqual(feb_response.context["anchor_day"], 31)

        mar_response = self.client.get(
            reverse("dashboard"),
            {"date": "2026-02-28", "mode": "month", "day": 31},
        )
        self.assertEqual(mar_response.context["next_date"], date(2026, 3, 31))

    def test_year_transition_and_leap_year_february(self):
        jan_response = self.client.get(
            reverse("dashboard"),
            {"date": "2026-12-15", "mode": "month"},
        )
        self.assertEqual(jan_response.context["next_date"], date(2027, 1, 15))

        leap_response = self.client.get(
            reverse("dashboard"),
            {"date": "2024-01-31", "mode": "month"},
        )
        self.assertEqual(leap_response.context["next_date"], date(2024, 2, 29))

    def test_invalid_date_query_falls_back_to_today(self):
        response = self.client.get(
            reverse("dashboard"),
            {"date": "not-a-valid-date", "mode": "month"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_date"], timezone.localdate())

    def test_cannot_add_calories_for_future_date(self):
        future_date = timezone.localdate() + timedelta(days=2)
        response = self.client.post(
            reverse("calorie_create"),
            {"date": future_date.isoformat(), "calories_burned": 250},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DailyCalories.objects.filter(user=self.user).count(), 0)

    def test_week_period_bounds_and_empty_week_flag(self):
        response = self.client.get(
            reverse("dashboard"),
            {"date": "2020-05-06", "mode": "week"},
        )
        self.assertEqual(response.context["week_period_start"], date(2020, 5, 4))
        self.assertEqual(response.context["week_period_end"], date(2020, 5, 10))
        self.assertTrue(response.context["is_empty_week"])
        self.assertEqual(response.context["week_total_calories"], 0)


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
        self.user = User.objects.create_user(
            username="athlete", password="Password123!"
        )
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
        self.workout.status = Workout.Status.COMPLETED
        self.workout.save(update_fields=["status"])
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


class MidnightWorkoutTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="midnight_user", password="Password123!"
        )
        self.client.force_login(self.user)
        self.tz = timezone.get_current_timezone()

    def test_workouts_at_2359_and_0000_assigned_to_correct_days(self):
        day_one = date(2026, 11, 10)
        day_two = date(2026, 11, 11)

        late_workout = Workout.objects.create(
            user=self.user,
            title="Поздняя тренировка",
            scheduled_at=timezone.make_aware(
                datetime.combine(day_one, time(23, 59)), self.tz
            ),
            status=Workout.Status.COMPLETED,
        )
        early_workout = Workout.objects.create(
            user=self.user,
            title="Ночная тренировка",
            scheduled_at=timezone.make_aware(
                datetime.combine(day_two, time(0, 0)), self.tz
            ),
            status=Workout.Status.COMPLETED,
        )

        DailyCalories.objects.create(
            user=self.user, workout=late_workout, calories_burned=250
        )
        DailyCalories.objects.create(
            user=self.user, workout=early_workout, calories_burned=400
        )

        resp_day_one = self.client.get(
            reverse("dashboard"), {"date": day_one.isoformat()}
        )
        self.assertEqual(
            list(resp_day_one.context["selected_day_workouts"]), [late_workout]
        )
        self.assertEqual(resp_day_one.context["selected_day_total_calories"], 250)

        resp_day_two = self.client.get(
            reverse("dashboard"), {"date": day_two.isoformat()}
        )
        self.assertEqual(
            list(resp_day_two.context["selected_day_workouts"]), [early_workout]
        )
        self.assertEqual(resp_day_two.context["selected_day_total_calories"], 400)

    def test_midnight_workouts_on_month_boundary(self):
        end_of_month = date(2026, 10, 31)
        start_of_next = date(2026, 11, 1)

        w_oct = Workout.objects.create(
            user=self.user,
            title="Конец октября",
            scheduled_at=timezone.make_aware(
                datetime.combine(end_of_month, time(23, 59, 59)), self.tz
            ),
        )
        w_nov = Workout.objects.create(
            user=self.user,
            title="Начало ноября",
            scheduled_at=timezone.make_aware(
                datetime.combine(start_of_next, time(0, 0, 0)), self.tz
            ),
        )

        resp_oct = self.client.get(
            reverse("dashboard"), {"date": end_of_month.isoformat(), "mode": "month"}
        )
        self.assertEqual(list(resp_oct.context["selected_day_workouts"]), [w_oct])

        resp_nov = self.client.get(
            reverse("dashboard"), {"date": start_of_next.isoformat(), "mode": "month"}
        )
        self.assertEqual(list(resp_nov.context["selected_day_workouts"]), [w_nov])


class ExtendedFormTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="form_user", password="Password123!"
        )

    def test_workout_form_defaults_and_whitespace_trimming(self):
        form = WorkoutForm(
            data={"title": "  Вечернее кардио  ", "notes": "Лёгкий темп"}
        )
        self.assertTrue(form.is_valid())
        workout = form.save(user=self.user)

        local_dt = timezone.localtime(workout.scheduled_at)
        self.assertEqual(workout.title, "Вечернее кардио")
        self.assertEqual(local_dt.date(), timezone.localdate())
        self.assertEqual(local_dt.time(), time(18, 0))

    def test_workout_form_invalid_cases(self):
        form_long = WorkoutForm(data={"title": "А" * 121})
        self.assertFalse(form_long.is_valid())
        self.assertIn("title", form_long.errors)

        form_bad_date = WorkoutForm(data={"title": "Тренировка", "date": "20-11-2026"})
        self.assertFalse(form_bad_date.is_valid())
        self.assertIn("date", form_bad_date.errors)

        form_bad_time = WorkoutForm(data={"title": "Тренировка", "time": "25:99"})
        self.assertFalse(form_bad_time.is_valid())
        self.assertIn("time", form_bad_time.errors)

    def test_workout_form_preserves_existing_datetime_on_edit(self):
        original_dt = timezone.make_aware(datetime(2026, 11, 15, 14, 45))
        workout = Workout.objects.create(
            user=self.user, title="Старое название", scheduled_at=original_dt
        )
        form = WorkoutForm(data={"title": "Новое название"}, instance=workout)
        self.assertTrue(form.is_valid())
        updated = form.save()

        self.assertEqual(
            timezone.localtime(updated.scheduled_at), timezone.localtime(original_dt)
        )

    def test_exercise_form_defaults_and_boundaries(self):
        form_defaults = ExerciseForm(data={"name": "  Отжимания  "})
        self.assertTrue(form_defaults.is_valid())
        ex = form_defaults.save(commit=False)
        self.assertEqual(ex.name, "Отжимания")
        self.assertEqual(ex.sets, 3)
        self.assertEqual(ex.reps, 10)
        self.assertEqual(ex.weight_kg, 0)

        form_max = ExerciseForm(
            data={"name": "Тяга", "sets": 100, "reps": 1000, "weight_kg": "999.9"}
        )
        self.assertTrue(form_max.is_valid())

        form_over_max = ExerciseForm(
            data={"name": "Тяга", "sets": 101, "reps": 1001, "weight_kg": "1000.0"}
        )
        self.assertFalse(form_over_max.is_valid())
        self.assertIn("sets", form_over_max.errors)
        self.assertIn("reps", form_over_max.errors)
        self.assertIn("weight_kg", form_over_max.errors)

        form_blank_name = ExerciseForm(data={"name": "   "})
        self.assertFalse(form_blank_name.is_valid())
        self.assertIn("name", form_blank_name.errors)

        def test_daily_calories_form_validation(self):
            valid_form = DailyCaloriesForm(
                data={
                    "date": timezone.localdate().isoformat(),
                    "calories_burned": 20000,
                    "activity_note": "Марафон",
                }
            )
            self.assertTrue(valid_form.is_valid())

            future_form = DailyCaloriesForm(
                data={
                    "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                    "calories_burned": 300,
                }
            )
            self.assertFalse(future_form.is_valid())

            over_limit_form = DailyCaloriesForm(data={"calories_burned": 20001})
            self.assertFalse(over_limit_form.is_valid())
            self.assertIn("calories_burned", over_limit_form.errors)

    class WorkoutStatusTransitionTests(TestCase):

        def setUp(self):
            self.user = User.objects.create_user(
                username="status_user", password="Password123!"
            )
            self.client.force_login(self.user)
            self.workout = Workout.objects.create(
                user=self.user,
                title="Тест статусов",
                status=Workout.Status.PLANNED,
            )
            self.url = reverse("workout_toggle_status", args=[self.workout.id])

        def test_all_allowed_transitions_via_view(self):
            self.client.post(self.url, {"status": Workout.Status.COMPLETED})
            self.workout.refresh_from_db()
            self.assertEqual(self.workout.status, Workout.Status.COMPLETED)

            self.client.post(self.url, {"status": Workout.Status.SKIPPED})
            self.workout.refresh_from_db()
            self.assertEqual(self.workout.status, Workout.Status.SKIPPED)

            self.client.post(self.url, {"status": Workout.Status.PLANNED})
            self.workout.refresh_from_db()
            self.assertEqual(self.workout.status, Workout.Status.PLANNED)

            self.client.post(self.url, {"status": Workout.Status.SKIPPED})
            self.workout.refresh_from_db()
            self.assertEqual(self.workout.status, Workout.Status.SKIPPED)

        def test_forbidden_transition_skipped_to_completed_blocked(self):
            self.workout.status = Workout.Status.SKIPPED
            self.workout.save(update_fields=["status"])

            response = self.client.post(self.url, {"status": Workout.Status.COMPLETED})
            self.assertEqual(response.status_code, 302)
            self.workout.refresh_from_db()
            self.assertEqual(self.workout.status, Workout.Status.SKIPPED)

        def test_invalid_status_value_is_rejected(self):
            response = self.client.post(self.url, {"status": "unknown_status"})
            self.assertEqual(response.status_code, 302)
            self.workout.refresh_from_db()
            self.assertEqual(self.workout.status, Workout.Status.PLANNED)

    class ForeignDataAccessTests(TestCase):

        def setUp(self):
            self.owner = User.objects.create_user(
                username="owner", password="Password123!"
            )
            self.intruder = User.objects.create_user(
                username="intruder", password="Password123!"
            )
            self.today = timezone.localdate()

            self.owner_workout = Workout.objects.create(
                user=self.owner,
                title="Секретная тренировка",
                status=Workout.Status.COMPLETED,
            )
            self.owner_calorie = DailyCalories.objects.create(
                user=self.owner,
                workout=self.owner_workout,
                calories_burned=550,
            )

        def test_intruder_cannot_see_owner_workouts_or_calories_on_dashboard(self):
            self.client.force_login(self.intruder)
            response = self.client.get(
                reverse("dashboard"), {"date": self.today.isoformat()}
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(list(response.context["selected_day_workouts"]), [])
            self.assertEqual(list(response.context["selected_day_calorie_logs"]), [])
            self.assertEqual(response.context["selected_day_total_calories"], 0)
            self.assertEqual(response.context["total_completed_workouts"], 0)

        def test_intruder_cannot_delete_owner_calorie_entry(self):
            self.client.force_login(self.intruder)
            response = self.client.post(
                reverse("calorie_delete", args=[self.owner_calorie.id])
            )
            self.assertEqual(response.status_code, 404)
            self.assertTrue(
                DailyCalories.objects.filter(pk=self.owner_calorie.pk).exists()
            )

        def test_intruder_cannot_link_calories_to_completed_foreign_workout(self):
            self.client.force_login(self.intruder)
            response = self.client.post(
                reverse("calorie_create"),
                {"calories_burned": 200, "workout_id": self.owner_workout.id},
            )
            self.assertEqual(response.status_code, 404)
            self.assertEqual(
                DailyCalories.objects.filter(user=self.intruder).count(), 0
            )

            foreign_entry = DailyCalories(
                user=self.intruder,
                workout=self.owner_workout,
                calories_burned=200,
            )
            with self.assertRaises(ValidationError):
                foreign_entry.full_clean()

    class StatisticsPeriodBoundaryTests(TestCase):
        def setUp(self):
            self.user = User.objects.create_user(
                username="stats_user", password="Password123!"
            )
            self.client.force_login(self.user)
            self.tz = timezone.get_current_timezone()

        def _create_completed_workout_with_calories(
            self, target_date: date, hour: int, kcal: int
        ):
            w = Workout.objects.create(
                user=self.user,
                title=f"Тренировка {target_date}",
                scheduled_at=timezone.make_aware(
                    datetime.combine(target_date, time(hour, 0)), self.tz
                ),
                status=Workout.Status.COMPLETED,
            )
            DailyCalories.objects.create(
                user=self.user, workout=w, calories_burned=kcal
            )
            return w

        def test_week_statistics_include_exact_bounds_and_exclude_adjacent_days(self):
            selected = date(2026, 11, 18)
            prev_sunday = date(2026, 11, 15)
            monday_start = date(2026, 11, 16)
            sunday_end = date(2026, 11, 22)
            next_monday = date(2026, 11, 23)

            self._create_completed_workout_with_calories(prev_sunday, 23, 100)
            self._create_completed_workout_with_calories(monday_start, 0, 200)
            self._create_completed_workout_with_calories(sunday_end, 23, 350)
            self._create_completed_workout_with_calories(next_monday, 0, 400)

            response = self.client.get(
                reverse("dashboard"), {"date": selected.isoformat(), "mode": "week"}
            )

            self.assertEqual(response.context["week_period_start"], monday_start)
            self.assertEqual(response.context["week_period_end"], sunday_end)
            self.assertEqual(response.context["week_workouts_count"], 2)
            self.assertEqual(response.context["week_total_calories"], 550)
            self.assertEqual(response.context["total_completed_workouts"], 4)
            self.assertFalse(response.context["is_empty_week"])

        def test_month_mode_calendar_grid_boundaries(self):
            selected = date(2026, 11, 10)
            grid_first_day = date(2026, 10, 26)
            in_month_day = date(2026, 11, 10)

            self._create_completed_workout_with_calories(grid_first_day, 12, 150)
            self._create_completed_workout_with_calories(in_month_day, 12, 300)

            response = self.client.get(
                reverse("dashboard"), {"date": selected.isoformat(), "mode": "month"}
            )

            calendar_weeks = response.context["calendar_weeks"]
            first_cell = calendar_weeks[0][0]
            self.assertEqual(first_cell["date"], grid_first_day)
            self.assertFalse(first_cell["in_current_month"])
            self.assertEqual(first_cell["total_calories"], 150)
            self.assertEqual(response.context["selected_day_total_calories"], 300)


class CalendarTransitionsAndLabelsTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="cal_detail_user", password="Password123!"
        )
        self.client.force_login(self.user)

    def test_calendar_transitions_week_and_month_30_31_days(self):
        week_resp = self.client.get(
            reverse("dashboard"), {"date": "2026-11-18", "mode": "week"}
        )
        self.assertEqual(week_resp.context["prev_date"], date(2026, 11, 11))
        self.assertEqual(week_resp.context["next_date"], date(2026, 11, 25))

        apr_resp = self.client.get(
            reverse("dashboard"), {"date": "2026-03-31", "mode": "month"}
        )
        self.assertEqual(apr_resp.context["next_date"], date(2026, 4, 30))
        self.assertEqual(apr_resp.context["anchor_day"], 31)

        may_resp = self.client.get(
            reverse("dashboard"), {"date": "2026-04-30", "mode": "month", "day": 31}
        )
        self.assertEqual(may_resp.context["next_date"], date(2026, 5, 31))

    def test_leap_year_and_year_boundary_transitions(self):
        leap_feb = self.client.get(
            reverse("dashboard"), {"date": "2024-02-29", "mode": "month"}
        )
        self.assertEqual(leap_feb.context["prev_date"], date(2024, 1, 29))
        self.assertEqual(leap_feb.context["next_date"], date(2024, 3, 29))

        non_leap_feb = self.client.get(
            reverse("dashboard"), {"date": "2025-01-31", "mode": "month"}
        )
        self.assertEqual(non_leap_feb.context["next_date"], date(2025, 2, 28))

        dec_back = self.client.get(
            reverse("dashboard"), {"date": "2026-01-10", "mode": "month"}
        )
        self.assertEqual(dec_back.context["prev_date"], date(2025, 12, 10))

        cross_year_week = self.client.get(
            reverse("dashboard"), {"date": "2026-12-31", "mode": "week"}
        )
        self.assertEqual(
            cross_year_week.context["week_period_start"], date(2026, 12, 28)
        )
        self.assertEqual(cross_year_week.context["week_period_end"], date(2027, 1, 3))

    def test_statistics_label_matches_actual_week_period(self):
        response = self.client.get(
            reverse("dashboard"), {"date": "2026-11-18", "mode": "week"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["week_period_start"], date(2026, 11, 16))
        self.assertEqual(response.context["week_period_end"], date(2026, 11, 22))
        self.assertContains(response, "За неделю (16.11.2026 — 22.11.2026):")

    def test_empty_week_and_future_date_behavior(self):
        empty_resp = self.client.get(
            reverse("dashboard"), {"date": "2021-03-10", "mode": "week"}
        )
        self.assertTrue(empty_resp.context["is_empty_week"])
        self.assertContains(empty_resp, "(нет активности)")

        future_date = timezone.localdate() + timedelta(days=5)
        future_resp = self.client.get(
            reverse("dashboard"), {"date": future_date.isoformat()}
        )
        self.assertTrue(future_resp.context["is_future_selected"])
        self.assertContains(
            future_resp,
            f"Нельзя добавить запись калорий за будущую дату ({future_date:%d.%m.%Y}).",
        )
        self.assertNotContains(future_resp, 'action="/calories/add/"')

        post_resp = self.client.post(
            reverse("calorie_create"),
            {"date": future_date.isoformat(), "calories_burned": 300},
        )
        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(DailyCalories.objects.filter(user=self.user).count(), 0)


class SeedScriptSafetyTests(TestCase):

    def setUp(self):
        self.regular_user = User.objects.create_user(
            username="regular_athlete", password="Password123!"
        )
        self.regular_workout = Workout.objects.create(
            user=self.regular_user,
            title="Личная тренировка пользователя",
            status=Workout.Status.COMPLETED,
        )
        self.regular_calorie = DailyCalories.objects.create(
            user=self.regular_user,
            workout=self.regular_workout,
            calories_burned=420,
        )

    @override_settings(DEBUG=True)
    @patch.dict(os.environ, {"PULSEPLAN_DEMO_PASSWORD": "DemoPassword123!"})
    def test_repeated_seed_run_without_reset_does_not_duplicate_data(self):
        run_seed(reset=False)
        demo_user = User.objects.get(username=DEMO_USERNAME)
        workouts_count = Workout.objects.filter(user=demo_user).count()
        exercises_count = Exercise.objects.filter(workout__user=demo_user).count()
        calories_count = DailyCalories.objects.filter(user=demo_user).count()

        self.assertEqual(workouts_count, 3)
        self.assertEqual(calories_count, 3)

        run_seed(reset=False)

        self.assertEqual(User.objects.filter(username=DEMO_USERNAME).count(), 1)
        self.assertEqual(Workout.objects.filter(user=demo_user).count(), workouts_count)
        self.assertEqual(
            Exercise.objects.filter(workout__user=demo_user).count(), exercises_count
        )
        self.assertEqual(
            DailyCalories.objects.filter(user=demo_user).count(), calories_count
        )

    @override_settings(DEBUG=True)
    @patch.dict(os.environ, {"PULSEPLAN_DEMO_PASSWORD": "DemoPassword123!"})
    def test_seed_reset_preserves_other_users_and_blocks_staff_accounts(self):
        run_seed(reset=False)
        demo_user = User.objects.get(username=DEMO_USERNAME)

        extra_workout = Workout.objects.create(
            user=demo_user, title="Временная демо-тренировка"
        )

        run_seed(reset=True)

        self.assertFalse(Workout.objects.filter(pk=extra_workout.pk).exists())
        self.assertEqual(Workout.objects.filter(user=demo_user).count(), 3)

        self.assertTrue(Workout.objects.filter(pk=self.regular_workout.pk).exists())
        self.assertTrue(
            DailyCalories.objects.filter(pk=self.regular_calorie.pk).exists()
        )

        demo_user.is_staff = True
        demo_user.save(update_fields=["is_staff"])

        with self.assertRaises(CommandError):
            run_seed(reset=True)

        self.assertEqual(Workout.objects.filter(user=demo_user).count(), 3)

    @override_settings(DEBUG=True)
    @patch.dict(os.environ, {"PULSEPLAN_DEMO_PASSWORD": "DemoPassword123!"})
    def test_seed_transaction_rollback_on_error(self):
        run_seed(reset=False)
        demo_user = User.objects.get(username=DEMO_USERNAME)
        custom_workout = Workout.objects.create(
            user=demo_user, title="Важная тренировка до сбоя"
        )
        initial_workout_ids = set(
            Workout.objects.filter(user=demo_user).values_list("id", flat=True)
        )

        with patch(
            "seed_pulseplan._create_validated_exercises",
            side_effect=RuntimeError("Искусственный сбой при заполнении"),
        ), self.assertRaises(RuntimeError):
            run_seed(reset=True)

        current_workout_ids = set(
            Workout.objects.filter(user=demo_user).values_list("id", flat=True)
        )
        self.assertEqual(current_workout_ids, initial_workout_ids)
        self.assertTrue(Workout.objects.filter(pk=custom_workout.pk).exists())


class StatisticsLabelsPeriodMatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="label_stats_user", password="Password123!"
        )
        self.client.force_login(self.user)
        self.tz = timezone.get_current_timezone()

    def _add_completed_workout_calories(self, target_date: date, kcal: int):
        workout = Workout.objects.create(
            user=self.user,
            title=f"Тренировка {target_date.isoformat()}",
            scheduled_at=timezone.make_aware(
                datetime.combine(target_date, time(12, 0)), self.tz
            ),
            status=Workout.Status.COMPLETED,
        )
        DailyCalories.objects.create(
            user=self.user, workout=workout, calories_burned=kcal
        )

    def test_statistics_labels_across_month_and_year_boundaries(self):
        cases = [
            (date(2026, 12, 2), "02.12.2026", "30.11.2026 — 06.12.2026"),
            (date(2027, 1, 1), "01.01.2027", "28.12.2026 — 03.01.2027"),
            (date(2024, 2, 29), "29.02.2024", "26.02.2024 — 03.03.2024"),
        ]

        for selected, day_label, week_label in cases:
            with self.subTest(selected=selected):
                response = self.client.get(
                    reverse("dashboard"),
                    {"date": selected.isoformat(), "mode": "week"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f"Расход калорий за {day_label}")
                self.assertContains(response, f"Тренировки на {day_label}")
                self.assertContains(response, f"За неделю ({week_label}):")

    def test_week_label_in_month_mode_reflects_actual_week_not_month_grid(self):
        selected = date(2026, 11, 12)
        self._add_completed_workout_calories(date(2026, 10, 26), 500)
        self._add_completed_workout_calories(date(2026, 11, 9), 200)
        self._add_completed_workout_calories(date(2026, 11, 15), 300)

        response = self.client.get(
            reverse("dashboard"),
            {"date": selected.isoformat(), "mode": "month"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["week_period_start"], date(2026, 11, 9))
        self.assertEqual(response.context["week_period_end"], date(2026, 11, 15))
        self.assertEqual(response.context["week_total_calories"], 500)
        self.assertContains(response, "За неделю (09.11.2026 — 15.11.2026):")
        self.assertContains(response, "<strong>500 ккал</strong>", html=True)

    def test_statistics_label_updates_when_navigating_prev_and_next_week(self):
        base_date = date(2026, 10, 14)
        self._add_completed_workout_calories(date(2026, 10, 7), 150)
        self._add_completed_workout_calories(date(2026, 10, 21), 450)

        initial = self.client.get(
            reverse("dashboard"), {"date": base_date.isoformat(), "mode": "week"}
        )
        prev_url = f"{reverse('dashboard')}?date={initial.context['prev_date'].isoformat()}&mode=week"
        next_url = f"{reverse('dashboard')}?date={initial.context['next_date'].isoformat()}&mode=week"

        prev_resp = self.client.get(prev_url)
        self.assertContains(prev_resp, "За неделю (05.10.2026 — 11.10.2026):")
        self.assertContains(prev_resp, "<strong>150 ккал</strong>", html=True)

        next_resp = self.client.get(next_url)
        self.assertContains(next_resp, "За неделю (19.10.2026 — 25.10.2026):")
        self.assertContains(next_resp, "<strong>450 ккал</strong>", html=True)
class RemainingQaTasksTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="qa_tasks_user", password="Password123!"
        )
        self.client.force_login(self.user)
        self.tz = timezone.get_current_timezone()

    def test_midnight_workouts_on_week_boundary_and_calorie_totals(self):
        sunday_end = date(2026, 10, 18)
        monday_start = date(2026, 10, 19)

        w_sun = Workout.objects.create(
            user=self.user,
            title="Поздняя воскресная",
            scheduled_at=timezone.make_aware(
                datetime.combine(sunday_end, time(23, 59, 59)), self.tz
            ),
            status=Workout.Status.COMPLETED,
        )
        w_mon = Workout.objects.create(
            user=self.user,
            title="Полночная понедельника",
            scheduled_at=timezone.make_aware(
                datetime.combine(monday_start, time(0, 0, 0)), self.tz
            ),
            status=Workout.Status.COMPLETED,
        )
        DailyCalories.objects.create(user=self.user, workout=w_sun, calories_burned=220)
        DailyCalories.objects.create(user=self.user, workout=w_mon, calories_burned=380)

        resp_sun = self.client.get(
            reverse("dashboard"), {"date": sunday_end.isoformat(), "mode": "week"}
        )
        self.assertEqual(list(resp_sun.context["selected_day_workouts"]), [w_sun])
        self.assertEqual(resp_sun.context["week_total_calories"], 220)

        resp_mon = self.client.get(
            reverse("dashboard"), {"date": monday_start.isoformat(), "mode": "week"}
        )
        self.assertEqual(list(resp_mon.context["selected_day_workouts"]), [w_mon])
        self.assertEqual(resp_mon.context["week_total_calories"], 380)

    def test_forms_validation_edge_cases(self):
        wf_blank = WorkoutForm(data={"title": "   \t  "})
        self.assertFalse(wf_blank.is_valid())
        self.assertIn("title", wf_blank.errors)

        wf_seconds = WorkoutForm(
            data={"title": "Интервалы", "date": "2026-10-15", "time": "07:15:00"}
        )
        self.assertTrue(wf_seconds.is_valid())
        w = wf_seconds.save(user=self.user)
        self.assertEqual(timezone.localtime(w.scheduled_at).time(), time(7, 15))

        ef_invalid = ExerciseForm(
            data={"name": "Жим", "sets": -1, "reps": "abc", "weight_kg": "-0.5"}
        )
        self.assertFalse(ef_invalid.is_valid())
        self.assertIn("sets", ef_invalid.errors)
        self.assertIn("reps", ef_invalid.errors)
        self.assertIn("weight_kg", ef_invalid.errors)

        cf_min = DailyCaloriesForm(
            data={"date": "2026-10-15", "calories_burned": 1, "activity_note": "Разминка"}
        )
        self.assertTrue(cf_min.is_valid())

        cf_zero = DailyCaloriesForm(data={"date": "2026-10-15", "calories_burned": 0})
        self.assertFalse(cf_zero.is_valid())
        self.assertIn("calories_burned", cf_zero.errors)

        cf_future = DailyCaloriesForm(
            data={
                "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                "calories_burned": 250,
            }
        )
        self.assertFalse(cf_future.is_valid())

    def test_status_non_color_markers_and_focus_visible_styles(self):
        target_day = date(2026, 10, 15)
        for idx, (status_val, title) in enumerate(
            [
                (Workout.Status.PLANNED, "План-тренировка"),
                (Workout.Status.COMPLETED, "Готовая-тренировка"),
                (Workout.Status.SKIPPED, "Пропуск-тренировка"),
            ]
        ):
            Workout.objects.create(
                user=self.user,
                title=title,
                scheduled_at=timezone.make_aware(
                    datetime.combine(target_day, time(10 + idx, 0)), self.tz
                ),
                status=status_val,
            )

        response = self.client.get(
            reverse("dashboard"), {"date": target_day.isoformat(), "mode": "week"}
        )
        self.assertEqual(response.status_code, 200)

        self.assertContains(response, "◷")
        self.assertContains(response, "✓")
        self.assertContains(response, "✕")
        self.assertContains(response, "Запланирована: 10:00 План-тренировка")
        self.assertContains(response, "Выполнена: 11:00 Готовая-тренировка")
        self.assertContains(response, "Пропущена: 12:00 Пропуск-тренировка")

        self.assertContains(response, "workout/css/app.css")
        css_path = finders.find("workout/css/app.css")
        self.assertIsNotNone(css_path)
        self.assertIn(":focus-visible", Path(css_path).read_text(encoding="utf-8"))

class IntegrationAndFeaturesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="integration_qa_user", password="Password123!"
        )
        self.other_user = User.objects.create_user(
            username="other_qa_user", password="Password123!"
        )
        self.client.force_login(self.user)
        self.tz = timezone.get_current_timezone()

    def test_europe_kirov_timezone_configuration_and_conversion(self):
        from django.conf import settings as django_settings

        self.assertEqual(django_settings.TIME_ZONE, "Europe/Kirov")
        self.assertTrue(django_settings.USE_TZ)
        self.assertEqual(timezone.get_current_timezone_name(), "Europe/Kirov")

        utc_dt = datetime(2026, 10, 15, 21, 30, tzinfo=timezone.UTC)
        w = Workout.objects.create(
            user=self.user,
            title="Ночная по UTC, утренняя по Кирову",
            scheduled_at=utc_dt,
        )
        local_dt = timezone.localtime(w.scheduled_at)
        self.assertEqual(local_dt.date(), date(2026, 10, 16))
        self.assertEqual(local_dt.hour, 0)
        self.assertEqual(local_dt.minute, 30)

    def test_exercise_result_history_creation_and_view(self):
        workout = Workout.objects.create(
            user=self.user,
            title="Силовая",
            scheduled_at=timezone.make_aware(datetime(2026, 10, 15, 18, 0), self.tz),
        )
        exercise = Exercise.objects.create(
            workout=workout, name="Жим лёжа", sets=3, reps=10, weight_kg=Decimal("60.0")
        )

        history_url = reverse("exercise_history", args=[exercise.id])
        add_result_url = reverse("exercise_result_add", args=[exercise.id])

        resp_get = self.client.get(history_url)
        self.assertEqual(resp_get.status_code, 200)

        resp_post = self.client.post(
            add_result_url,
            data={"set_number": 1, "reps": 10, "weight_kg": "62.5"},
        )
        self.assertIn(resp_post.status_code, (200, 302))
        self.assertTrue(
            ExerciseResult.objects.filter(
                user=self.user,
                exercise=exercise,
                set_number=1,
                reps=10,
                weight_kg=Decimal("62.5"),
            ).exists()
        )

        resp_after = self.client.get(history_url)
        self.assertContains(resp_after, "62,5 кг")

    def test_exercise_result_rejects_foreign_workout_or_exercise(self):
        own_workout = Workout.objects.create(user=self.user, title="Моя тренировка")
        foreign_workout = Workout.objects.create(user=self.other_user, title="Чужая тренировка")
        own_exercise = Exercise.objects.create(workout=own_workout, name="Жим")
        foreign_exercise = Exercise.objects.create(workout=foreign_workout, name="Тяга")

        with self.assertRaises(ValidationError):
            ExerciseResult.objects.create(
                user=self.user,
                workout=foreign_workout,
                exercise=foreign_exercise,
                exercise_name="Тяга",
                set_number=1,
                reps=8,
            )
        with self.assertRaises(ValidationError):
            ExerciseResult.objects.create(
                user=self.user,
                workout=own_workout,
                exercise=foreign_exercise,
                exercise_name="Тяга",
                set_number=1,
                reps=8,
            )
        self.assertEqual(own_exercise.workout_id, own_workout.id)

    def test_exercise_result_survives_exercise_deletion(self):
        workout = Workout.objects.create(user=self.user, title="История")
        exercise = Exercise.objects.create(workout=workout, name="Жим")
        result = ExerciseResult.objects.create(
            user=self.user, workout=workout, exercise=exercise,
            exercise_name="Жим", set_number=1, reps=8,
            weight_kg=Decimal("40.0"),
        )

        exercise.delete()
        result.refresh_from_db()
        self.assertIsNone(result.exercise_id)
        self.assertTrue(ExerciseResult.objects.filter(id=result.id, user=self.user).exists())
        payload = self.client.get(
            reverse("export_user_data"), {"format": "json"}
        ).json()
        self.assertEqual(payload["exercise_results"][0]["exercise_name"], "Жим")

    def test_workout_template_creation_and_usage(self):
        resp_create = self.client.post(
            reverse("template_create"),
            data={"name": "Базовый фулбоди", "notes": "Разминка 10 мин"},
        )
        self.assertIn(resp_create.status_code, (200, 302))
        template = WorkoutTemplate.objects.get(user=self.user, name="Базовый фулбоди")

        use_url = reverse("template_use", args=[template.id])
        self.assertEqual(self.client.get(use_url).status_code, 200)

        resp_add_ex = self.client.post(
            reverse("template_exercise_add", args=[template.id]),
            data={"name": "Приседания", "sets": 4, "reps": 12, "weight_kg": "50.0"},
        )
        self.assertIn(resp_add_ex.status_code, (200, 302))
        self.assertEqual(template.exercises.count(), 1)

        target_date = "2026-10-20"
        resp_use = self.client.post(
            reverse("template_use", args=[template.id]),
            data={"date": target_date, "time": "19:00"},
        )
        self.assertIn(resp_use.status_code, (200, 302))

        created_workout = Workout.objects.filter(
            user=self.user, title="Базовый фулбоди"
        ).first()
        self.assertIsNotNone(created_workout)
        self.assertEqual(created_workout.exercises.count(), 1)
        created_exercise = created_workout.exercises.get()
        self.assertEqual(created_exercise.name, "Приседания")
        self.assertEqual(created_exercise.sets, 4)
        self.assertEqual(created_exercise.reps, 12)
        self.assertEqual(created_exercise.weight_kg, Decimal("50.0"))
        self.assertEqual(
            timezone.localtime(created_workout.scheduled_at),
            timezone.make_aware(datetime(2026, 10, 20, 19, 0), self.tz),
        )

        invalid_use = self.client.post(
            use_url, data={"date": "invalid-date", "time": "25:90"}
        )
        self.assertEqual(invalid_use.status_code, 200)
        self.assertEqual(
            Workout.objects.filter(user=self.user, title="Базовый фулбоди").count(), 1
        )

    def test_template_views_are_scoped_to_the_owner(self):
        template = WorkoutTemplate.objects.create(
            user=self.other_user, name="Чужой шаблон"
        )
        self.assertEqual(
            self.client.get(reverse("template_use", args=[template.id])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("template_delete", args=[template.id])).status_code,
            404,
        )
        self.assertTrue(WorkoutTemplate.objects.filter(id=template.id).exists())

    def test_template_exercise_delete_requires_post(self):
        template = WorkoutTemplate.objects.create(user=self.user, name="Мой шаблон")
        exercise = TemplateExercise.objects.create(template=template, name="Планка")
        response = self.client.get(
            reverse("template_exercise_delete", args=[exercise.id])
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(TemplateExercise.objects.filter(id=exercise.id).exists())

    def test_template_edit_and_delete_work_for_owner(self):
        template = WorkoutTemplate.objects.create(user=self.user, name="Черновик")
        exercise = TemplateExercise.objects.create(template=template, name="Присед")

        response = self.client.post(
            reverse("template_edit", args=[template.id]),
            data={"name": "Силовая", "notes": "Обновлённая заметка"},
        )
        self.assertEqual(response.status_code, 302)
        template.refresh_from_db()
        self.assertEqual(template.name, "Силовая")
        self.assertEqual(template.notes, "Обновлённая заметка")

        response = self.client.post(
            reverse("template_exercise_delete", args=[exercise.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TemplateExercise.objects.filter(id=exercise.id).exists())

        response = self.client.post(reverse("template_delete", args=[template.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(WorkoutTemplate.objects.filter(id=template.id).exists())

    def test_export_user_data_contains_only_own_records(self):
        Workout.objects.create(
            user=self.user,
            title="Моя экспортная тренировка",
            scheduled_at=timezone.make_aware(datetime(2026, 10, 15, 17, 0), self.tz),
        )
        Workout.objects.create(
            user=self.other_user,
            title="Чужая секретная тренировка",
            scheduled_at=timezone.make_aware(datetime(2026, 10, 15, 17, 0), self.tz),
        )

        page_resp = self.client.get(reverse("export_data"))
        self.assertEqual(page_resp.status_code, 200)

        export_resp = self.client.post(
            reverse("export_data"),
            data={"workouts": "on", "format": "json"},
        )
        self.assertEqual(export_resp.status_code, 200)
        body = export_resp.content.decode("utf-8", errors="replace")
        self.assertIn("Моя экспортная тренировка", body)
        self.assertNotIn("Чужая секретная тренировка", body)

    def test_full_export_includes_all_user_data_only(self):
        workout = Workout.objects.create(
            user=self.user,
            title="Мой архив",
            scheduled_at=timezone.make_aware(datetime(2026, 10, 15, 17, 0), self.tz),
            status=Workout.Status.COMPLETED,
        )
        exercise = Exercise.objects.create(
            workout=workout, name="Жим лёжа", sets=3, reps=8, weight_kg=Decimal("70.0")
        )
        ExerciseResult.objects.create(
            user=self.user, workout=workout, exercise=exercise,
            exercise_name=exercise.name, set_number=1, reps=7,
            weight_kg=Decimal("72.5"),
        )
        DailyCalories.objects.create(
            user=self.user, workout=workout, calories_burned=425
        )
        template = WorkoutTemplate.objects.create(
            user=self.user, name="Мой шаблон", notes="Сохранять технику"
        )
        TemplateExercise.objects.create(
            template=template, name="Тяга", sets=4, reps=6, weight_kg=Decimal("80.0")
        )

        foreign_workout = Workout.objects.create(
            user=self.other_user, title="Чужой приватный план"
        )
        foreign_template = WorkoutTemplate.objects.create(
            user=self.other_user, name="Чужой приватный шаблон"
        )
        TemplateExercise.objects.create(
            template=foreign_template, name="Чужое упражнение"
        )
        invalid_foreign_result = ExerciseResult(
            user=self.user,
            workout=foreign_workout,
            exercise_name="Чужой результат",
            set_number=1,
            reps=5,
        )
        ExerciseResult.objects.bulk_create([invalid_foreign_result])

        response = self.client.get(reverse("export_user_data"), {"format": "json"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        payload = response.json()
        self.assertEqual(payload["user"]["username"], self.user.username)
        self.assertEqual(payload["workouts"][0]["exercises"][0]["name"], "Жим лёжа")
        self.assertEqual(payload["exercise_results"][0]["weight_kg"], "72.5")
        self.assertEqual(payload["workout_templates"][0]["exercises"][0]["name"], "Тяга")
        self.assertEqual(payload["daily_calories"][0]["calories_burned"], 425)
        serialized = response.content.decode("utf-8")
        self.assertNotIn("Чужой приватный план", serialized)
        self.assertNotIn("Чужой приватный шаблон", serialized)
        self.assertNotIn("Чужое упражнение", serialized)
        self.assertNotIn("Чужой результат", serialized)
        self.assertTrue(Workout.objects.filter(id=foreign_workout.id).exists())

        csv_response = self.client.get(reverse("export_user_data"), {"format": "csv"})
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("text/csv", csv_response["Content-Type"])
        csv_content = csv_response.content.decode("utf-8-sig")
        self.assertIn("set_result", csv_content)
        self.assertIn("template_exercise", csv_content)
        self.assertNotIn("Чужой приватный план", csv_content)
        self.assertNotIn("Чужое упражнение", csv_content)

        selected_export = self.client.post(
            reverse("export_data"),
            data={"results": "on", "format": "json"},
        )
        self.assertEqual(selected_export.status_code, 200)
        self.assertNotIn(
            "Чужой результат",
            selected_export.content.decode("utf-8"),
        )

    def test_full_export_rejects_unknown_format(self):
        response = self.client.get(reverse("export_user_data"), {"format": "xml"})
        self.assertEqual(response.status_code, 400)

    def test_account_data_page_is_available_and_requires_login(self):
        response = self.client.get(reverse("account_data"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Безопасное удаление данных")
        self.assertContains(response, reverse("export_user_data"))

        self.client.logout()
        response = self.client.get(reverse("account_data"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_data_only_deletion_requires_confirmation_and_preserves_other_users(self):
        own_workout = Workout.objects.create(
            user=self.user, title="Удалить", status=Workout.Status.COMPLETED
        )
        own_exercise = Exercise.objects.create(workout=own_workout, name="Жим")
        ExerciseResult.objects.create(
            user=self.user, workout=own_workout, exercise=own_exercise,
            exercise_name="Жим", set_number=1, reps=5,
        )
        DailyCalories.objects.create(
            user=self.user, workout=own_workout, calories_burned=300
        )
        own_template = WorkoutTemplate.objects.create(user=self.user, name="Удалить")
        TemplateExercise.objects.create(template=own_template, name="Тяга")
        other_workout = Workout.objects.create(
            user=self.other_user, title="Оставить", status=Workout.Status.COMPLETED
        )
        other_exercise = Exercise.objects.create(workout=other_workout, name="Оставить")
        ExerciseResult.objects.create(
            user=self.other_user, workout=other_workout, exercise=other_exercise,
            exercise_name="Оставить", set_number=1, reps=5,
        )

        response = self.client.post(
            reverse("delete_user_data"),
            data={
                "scope": "data_only",
                "password": "wrong-password",
                "confirmation": "УДАЛИТЬ",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Workout.objects.filter(id=own_workout.id).exists())

        response = self.client.post(
            reverse("delete_user_data"),
            data={
                "scope": "data_only",
                "password": "Password123!",
                "confirmation": "УДАЛИТЬ",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(id=self.user.id).exists())
        self.assertFalse(Workout.objects.filter(user=self.user).exists())
        self.assertFalse(Exercise.objects.filter(workout__user=self.user).exists())
        self.assertFalse(ExerciseResult.objects.filter(user=self.user).exists())
        self.assertFalse(DailyCalories.objects.filter(user=self.user).exists())
        self.assertFalse(WorkoutTemplate.objects.filter(user=self.user).exists())
        self.assertFalse(TemplateExercise.objects.filter(template__user=self.user).exists())
        self.assertTrue(Workout.objects.filter(id=other_workout.id).exists())
        self.assertTrue(ExerciseResult.objects.filter(user=self.other_user).exists())

    def test_full_account_deletion_removes_owned_records_and_keeps_other_users(self):
        workout = Workout.objects.create(
            user=self.user, title="Удалить аккаунт", status=Workout.Status.COMPLETED
        )
        exercise = Exercise.objects.create(workout=workout, name="Присед")
        ExerciseResult.objects.create(
            user=self.user, workout=workout, exercise=exercise,
            exercise_name="Присед", set_number=1, reps=5,
        )
        DailyCalories.objects.create(
            user=self.user, workout=workout, calories_burned=250
        )
        template = WorkoutTemplate.objects.create(user=self.user, name="Мой шаблон")
        TemplateExercise.objects.create(template=template, name="Планка")
        other_workout = Workout.objects.create(user=self.other_user, title="Чужая")
        other_user_id = self.user.id

        response = self.client.post(
            reverse("delete_user_data"),
            data={
                "scope": "full_account",
                "password": "Password123!",
                "confirmation": "УДАЛИТЬ",
            },
        )
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(id=other_user_id).exists())
        self.assertFalse(Workout.objects.filter(user_id=other_user_id).exists())
        self.assertFalse(Exercise.objects.filter(workout__user_id=other_user_id).exists())
        self.assertFalse(ExerciseResult.objects.filter(user_id=other_user_id).exists())
        self.assertFalse(DailyCalories.objects.filter(user_id=other_user_id).exists())
        self.assertFalse(WorkoutTemplate.objects.filter(user_id=other_user_id).exists())
        self.assertTrue(User.objects.filter(id=self.other_user.id).exists())
        self.assertTrue(Workout.objects.filter(id=other_workout.id).exists())

    def test_full_account_deletion_is_blocked_for_staff(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = self.client.post(
            reverse("delete_user_data"),
            data={
                "scope": "full_account",
                "password": "Password123!",
                "confirmation": "УДАЛИТЬ",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(id=self.user.id).exists())

    def test_data_deletion_preserves_foreign_results_linked_to_owned_workout(self):
        workout = Workout.objects.create(user=self.user, title="Моя тренировка")
        foreign_result = ExerciseResult(
            user=self.other_user,
            workout=workout,
            exercise_name="Некорректная историческая запись",
            set_number=1,
            reps=5,
        )
        ExerciseResult.objects.bulk_create([foreign_result])

        response = self.client.post(
            reverse("delete_user_data"),
            data={
                "scope": "data_only",
                "password": "Password123!",
                "confirmation": "УДАЛИТЬ",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Workout.objects.filter(id=workout.id).exists())
        self.assertTrue(ExerciseResult.objects.filter(id=foreign_result.id).exists())

    def test_cascade_deletion_leaves_no_orphan_user_records(self):
        w = Workout.objects.create(
            user=self.user,
            title="Тренировка на удаление",
            scheduled_at=timezone.make_aware(datetime(2026, 10, 15, 15, 0), self.tz),
            status=Workout.Status.COMPLETED,
        )
        ex = Exercise.objects.create(workout=w, name="Подтягивания", sets=3, reps=8)
        ExerciseResult.objects.create(
            user=self.user, workout=w, exercise=ex, exercise_name=ex.name, set_number=1, reps=8
        )
        DailyCalories.objects.create(user=self.user, workout=w, calories_burned=310)

        tpl = WorkoutTemplate.objects.create(user=self.user, name="Шаблон на удаление")
        TemplateExercise.objects.create(template=tpl, name="Отжимания", sets=3, reps=15)

        user_id = self.user.id
        self.user.delete()

        self.assertFalse(Workout.objects.filter(user_id=user_id).exists())
        self.assertFalse(Exercise.objects.filter(workout__user_id=user_id).exists())
        self.assertFalse(ExerciseResult.objects.filter(user_id=user_id).exists())
        self.assertFalse(DailyCalories.objects.filter(user_id=user_id).exists())
        self.assertFalse(WorkoutTemplate.objects.filter(user_id=user_id).exists())
        self.assertFalse(TemplateExercise.objects.filter(template__user_id=user_id).exists())
