import os
import sys
from datetime import datetime, time, timedelta
from decimal import Decimal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from workout.models import DailyCalories, Exercise, Workout

DEMO_USERNAME = "demo_user"


def _aware_dt(target_date, hour=18, minute=30):
    return timezone.make_aware(datetime.combine(target_date, time(hour, minute)))


def _create_validated_exercises(workout: Workout, items: list[dict]) -> None:
    exercises = []
    for idx, item in enumerate(items, start=1):
        ex = Exercise(
            workout=workout,
            name=item["name"],
            sets=item.get("sets", 3),
            reps=item.get("reps", 10),
            weight_kg=Decimal(str(item.get("weight_kg", "0.0"))),
            order=idx,
        )
        ex.full_clean()
        exercises.append(ex)
    Exercise.objects.bulk_create(exercises)


def run_seed(reset: bool = False) -> None:
    if not settings.DEBUG:
        raise CommandError("Демо-данные разрешено создавать только при DJANGO_DEBUG=True.")

    demo_password = os.environ.get("PULSEPLAN_DEMO_PASSWORD")
    if not demo_password:
        raise CommandError("Для демо-пользователя задайте PULSEPLAN_DEMO_PASSWORD.")

    User = get_user_model()

    with transaction.atomic():
        user, created = User.objects.select_for_update().get_or_create(
            username=DEMO_USERNAME,
            defaults={"email": "demo@pulseplan.local", "first_name": "Алексей"},
        )

        if user.is_superuser or user.is_staff:
            raise CommandError("Запрещено изменять административные учётные записи через seed.")

        if created:
            user.set_password(demo_password)
            user.save(update_fields=["password"])
        elif not reset:
            print(
                "demo_user уже существует, данные сохранены. "
                "Для пересоздания передайте --reset."
            )
            return
        else:
            Workout.objects.filter(user=user).delete()
            DailyCalories.objects.filter(user=user).delete()

        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())

        w1 = Workout.objects.create(
            user=user,
            title="Фулбоди А: Базовая адаптация",
            scheduled_at=_aware_dt(monday, 18, 30),
            status=Workout.Status.COMPLETED,
            notes="Разминка суставов 7 минут, спокойный темп без отказа.",
        )
        _create_validated_exercises(
            w1,
            [
                {"name": "Приседания с собственным весом", "sets": 3, "reps": 15, "weight_kg": 0},
                {"name": "Отжимания от скамьи / с колен", "sets": 3, "reps": 12, "weight_kg": 0},
                {"name": "Тяга гантелей в наклоне", "sets": 3, "reps": 12, "weight_kg": 8.0},
                {"name": "Планка классическая (сек)", "sets": 3, "reps": 40, "weight_kg": 0},
            ],
        )
        DailyCalories.objects.create(
            user=user,
            workout=w1,
            date=monday,
            calories_burned=320,
            activity_note="Силовая тренировка Фулбоди А",
        )
        DailyCalories.objects.create(
            user=user,
            workout=None,
            date=monday,
            calories_burned=180,
            activity_note="Вечерняя прогулка 6 500 шагов",
        )

        w2 = Workout.objects.create(
            user=user,
            title="Кардио + Кор: Выносливость",
            scheduled_at=_aware_dt(today, 19, 0),
            status=Workout.Status.PLANNED,
            notes="Следить за пульсом в зоне 125–140 уд/мин.",
        )
        _create_validated_exercises(
            w2,
            [
                {"name": "Быстрая ходьба на дорожке (мин)", "sets": 1, "reps": 20, "weight_kg": 0},
                {"name": "Скручивания на коврике", "sets": 3, "reps": 15, "weight_kg": 0},
                {"name": "Ягодичный мостик", "sets": 3, "reps": 15, "weight_kg": 0},
            ],
        )
        DailyCalories.objects.create(
            user=user,
            workout=None,
            date=today,
            calories_burned=210,
            activity_note="Утренняя разминка",
        )

        future_day = today + timedelta(days=2)
        w3 = Workout.objects.create(
            user=user,
            title="Фулбоди Б: Техника и тонус",
            scheduled_at=_aware_dt(future_day, 18, 30),
            status=Workout.Status.PLANNED,
            notes="Фокус на плавной негативной фазе движения.",
        )
        _create_validated_exercises(
            w3,
            [
                {"name": "Выпады на месте", "sets": 3, "reps": 10, "weight_kg": 0},
                {"name": "Жим гантелей сидя", "sets": 3, "reps": 12, "weight_kg": 6.0},
                {"name": "Гиперэкстензия", "sets": 3, "reps": 15, "weight_kg": 0},
            ],
        )

    print("Демо-данные PulsePlan успешно созданы.")


if __name__ == "__main__":
    reset_flag = "--reset" in sys.argv
    run_seed(reset=reset_flag)