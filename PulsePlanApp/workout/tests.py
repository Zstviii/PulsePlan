import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

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
        self.assertEqual(timezone.localtime(self.alice_workout.scheduled_at).date(), target_date)
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

        self.assertFalse(DailyCalories.objects.filter(workout=self.alice_workout).exists())

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
        self.assertEqual(DailyCalories.objects.filter(workout=self.alice_workout).count(), 1)

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

        response = self.client.post(
            reverse("exercise_delete", args=[exercise.id])
        )

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
        self.user = User.objects.create_user(username="calendar_user", password="Password123!")
        self.client.force_login(self.user)

    def test_month_navigation_preserves_anchor_day_across_consecutive_months(self):
        # 31 января -> 28 февраля (с сохранением anchor_day=31) -> 31 марта
        feb_response = self.client.get(
            reverse("dashboard"),
            {"date": "2026-01-31", "mode": "month"},
        )
        self.assertEqual(feb_response.context["next_date"], date(2026, 2, 28))
        self.assertEqual(feb_response.context["anchor_day"], 31)
        self.assertContains(feb_response, "day=31")

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

    def test_week_view_shows_range_and_status_legend(self):
        response = self.client.get(
            reverse("dashboard"),
            {"date": "2026-05-06", "mode": "week"},
        )

        self.assertContains(response, "04.05.2026 — 10.05.2026")
        for label in (
            "Запланирована",
            "Выполнена",
            "Пропущена",
            "Выбранный день",
            "Сегодня",
        ):
            self.assertContains(response, label)

    def test_dashboard_marks_today_and_exposes_labeled_form_limits(self):
        today = timezone.localdate()
        workout = Workout.objects.create(user=self.user, title="Сегодня")
        Exercise.objects.create(workout=workout, name="Присед")

        response = self.client.get(
            reverse("dashboard"),
            {"date": today.isoformat(), "mode": "month"},
        )

        self.assertContains(response, "cal-selected cal-today")
        self.assertContains(response, 'for="new-workout-title"')
        self.assertContains(response, 'maxlength="120"')
        self.assertContains(response, 'max="1000"')
        self.assertContains(response, 'max="999.9"')
        self.assertContains(response, 'max="20000"')

    def test_mobile_calendar_counts_workouts_by_status(self):
        today = timezone.localdate()
        Workout.objects.create(user=self.user, title="План", status=Workout.Status.PLANNED)
        Workout.objects.create(
            user=self.user, title="Готово", status=Workout.Status.COMPLETED
        )
        response = self.client.get(
            reverse("dashboard"),
            {"date": today.isoformat(), "mode": "week"},
        )
        calendar_day = next(
            day
            for week in response.context["calendar_weeks"]
            for day in week
            if day["date"] == today
        )

        self.assertEqual(calendar_day["planned_count"], 1)
        self.assertEqual(calendar_day["completed_count"], 1)
        self.assertEqual(calendar_day["skipped_count"], 0)
        self.assertContains(response, "Запланировано: 1, выполнено: 1, пропущено: 0")


class WorkoutFeatureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="features", password="Password123!")
        self.other_user = User.objects.create_user(username="other", password="Password123!")
        self.client.force_login(self.user)
        self.workout = Workout.objects.create(user=self.user, title="Силовая")
        self.exercise = Exercise.objects.create(
            workout=self.workout,
            name="Жим лёжа",
            sets=3,
            reps=10,
            weight_kg=40,
        )

    def test_exercise_history_records_and_displays_set_results(self):
        response = self.client.post(
            reverse("exercise_result_add", args=[self.exercise.pk]),
            {"set_number": 1, "reps": 8, "weight_kg": "42.5"},
        )

        self.assertRedirects(
            response,
            reverse("exercise_history", args=[self.exercise.pk]),
        )
        history = self.client.get(
            reverse("exercise_history", args=[self.exercise.pk])
        )
        result = history.context["results"].get()
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.workout, self.workout)
        self.assertEqual(result.exercise_name, self.exercise.name)
        self.assertEqual(result.reps, 8)
        self.assertEqual(str(result.weight_kg), "42.5")
        self.assertContains(history, "42,5 кг")

    def test_exercise_history_is_private_to_the_workout_owner(self):
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse("exercise_history", args=[self.exercise.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_template_can_be_created_edited_and_used(self):
        response = self.client.post(
            reverse("template_create"),
            {"name": "Верх тела", "notes": "Разминка перед началом"},
        )
        template = WorkoutTemplate.objects.get(user=self.user, name="Верх тела")
        self.assertRedirects(
            response,
            reverse("template_edit", args=[template.pk]),
        )

        self.client.post(
            reverse("template_exercise_add", args=[template.pk]),
            {"name": "Тяга блока", "sets": 4, "reps": 12, "weight_kg": "25"},
        )
        self.client.post(
            reverse("template_edit", args=[template.pk]),
            {"name": "Верх тела A", "notes": "Обновлённая заметка"},
        )
        template.refresh_from_db()
        self.assertEqual(template.name, "Верх тела A")
        self.assertEqual(template.notes, "Обновлённая заметка")
        self.assertEqual(template.exercises.count(), 1)

        target_date = timezone.localdate() + timedelta(days=1)
        use_response = self.client.post(
            reverse("template_use", args=[template.pk]),
            {"date": target_date.isoformat(), "time": "18:30"},
        )

        created_workout = Workout.objects.get(user=self.user, title=template.name)
        self.assertRedirects(use_response, reverse("dashboard") + f"?date={target_date}")
        self.assertEqual(created_workout.exercises.count(), 1)
        self.assertEqual(created_workout.exercises.get().name, "Тяга блока")

    def test_template_deletion_requires_post_and_removes_its_exercises(self):
        template = WorkoutTemplate.objects.create(user=self.user, name="На удаление")
        exercise = TemplateExercise.objects.create(
            template=template, name="Присед", sets=3, reps=8
        )

        get_response = self.client.get(
            reverse("template_delete", args=[template.pk])
        )
        self.assertEqual(get_response.status_code, 405)
        self.assertTrue(WorkoutTemplate.objects.filter(pk=template.pk).exists())

        response = self.client.post(
            reverse("template_delete", args=[template.pk])
        )
        self.assertRedirects(response, reverse("template_list"))
        self.assertFalse(WorkoutTemplate.objects.filter(pk=template.pk).exists())
        self.assertFalse(TemplateExercise.objects.filter(pk=exercise.pk).exists())

    def test_dashboard_delete_confirmation_warns_about_related_workout_data(self):
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, 'data-delete-title="Силовая"')
        self.assertContains(
            response,
            "Также будут удалены упражнения, результаты подходов и связанные записи калорий.",
        )
        self.assertContains(response, "Это действие нельзя отменить.")

    def test_export_includes_selected_user_data_and_template_details(self):
        result = ExerciseResult.objects.create(
            user=self.user,
            workout=self.workout,
            exercise=self.exercise,
            exercise_name=self.exercise.name,
            set_number=1,
            reps=8,
            weight_kg=42.5,
        )
        DailyCalories.objects.create(
            user=self.user,
            calories_burned=250,
            activity_note="Прогулка",
        )
        template = WorkoutTemplate.objects.create(
            user=self.user,
            name="Шаблон A",
            notes="Шаблонная заметка",
        )
        TemplateExercise.objects.create(
            template=template,
            name="Тяга блока",
            sets=4,
            reps=12,
            weight_kg=25,
        )
        foreign_workout = Workout.objects.create(
            user=self.other_user,
            title="Чужая тренировка",
        )

        response = self.client.post(
            reverse("export_data"),
            {
                "format": "json",
                "workouts": "on",
                "calories": "on",
                "results": "on",
                "templates": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        rows = json.loads(response.content)
        types = {row["type"] for row in rows}
        self.assertTrue(
            {"workout", "exercise", "calories", "set_result", "template", "template_exercise"}
            <= types
        )
        self.assertIn(
            "Шаблонная заметка",
            [row.get("notes") for row in rows],
        )
        self.assertIn(result.exercise_name, [row["exercise"] for row in rows])
        self.assertNotIn(foreign_workout.title, [row["title"] for row in rows])

        csv_response = self.client.post(
            reverse("export_data"),
            {"format": "csv", "templates": "on"},
        )
        self.assertTrue(csv_response.content.startswith(b"\xef\xbb\xbf"))
        csv_content = csv_response.content.decode("utf-8-sig")
        self.assertIn("template_exercise", csv_content)
        self.assertIn("Шаблонная заметка", csv_content)

    def test_invalid_template_exercise_add_shows_accessible_error_notification(self):
        template = WorkoutTemplate.objects.create(user=self.user, name="Шаблон")

        response = self.client.post(
            reverse("template_exercise_add", args=[template.pk]),
            {"name": "", "sets": 0, "reps": 0, "weight_kg": "-1"},
            follow=True,
        )

        self.assertEqual(template.exercises.count(), 0)
        self.assertTrue(
            any(message.tags == "error" for message in response.context["messages"])
        )
        self.assertContains(response, 'aria-live="assertive"')