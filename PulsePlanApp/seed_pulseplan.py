"""
Скрипт наполнения БД демонстрационными данными для MVP «PulsePlan».
Создаёт:
  - 1 тестового пользователя (логин: demo_user, пароль: PulsePlan2026!)
  - 3 тренировки на текущую неделю (1 выполненная, 2 запланированные)
  - Набор базовых упражнений для каждой тренировки
  - Записи о расходе калорий (привязанные к тренировке и дневной активности)

Способ запуска:
  python manage.py shell < seed_pulseplan.py
"""
from datetime import datetime, time, timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from workout.models import DailyCalories, Exercise, Workout

User = get_user_model()


def run_seed():
    user, created = User.objects.get_or_create(
        username="demo_user",
        defaults={"email": "demo@pulseplan.local", "first_name": "Алексей"},
    )
    if created:
        user.set_password("PulsePlan2026!")
        user.save()
        print("[OK] Создан пользователь: demo_user (пароль: PulsePlan2026!)")
    else:
        print("[INFO] Пользователь demo_user уже существует, обновляем демо-данные...")
        Workout.objects.filter(user=user).delete()
        DailyCalories.objects.filter(user=user).delete()

    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())

    def aware_dt(target_date, hour=18, minute=30):
        return timezone.make_aware(datetime.combine(target_date, time(hour, minute)))

    # 1. Тренировка в понедельник (Выполнена)
    w1 = Workout.objects.create(
        user=user,
        title="Фулбоди А: Базовая адаптация",
        scheduled_at=aware_dt(monday, 18, 30),
        status=Workout.Status.COMPLETED,
        notes="Разминка суставов 7 минут, спокойный темп без отказа.",
    )
    Exercise.objects.bulk_create([
        Exercise(workout=w1, name="Приседания с собственным весом", sets=3, reps=15, weight_kg=0, order=1),
        Exercise(workout=w1, name="Отжимания от скамьи / с колен", sets=3, reps=12, weight_kg=0, order=2),
        Exercise(workout=w1, name="Тяга гантелей в наклоне", sets=3, reps=12, weight_kg=8.0, order=3),
        Exercise(workout=w1, name="Планка классическая (сек)", sets=3, reps=40, weight_kg=0, order=4),
    ])
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

    # 2. Тренировка на сегодня (Запланирована)
    w2 = Workout.objects.create(
        user=user,
        title="Кардио + Кор: Выносливость",
        scheduled_at=aware_dt(today, 19, 0),
        status=Workout.Status.PLANNED,
        notes="Следить за пульсом в зоне 125–140 уд/мин.",
    )
    Exercise.objects.bulk_create([
        Exercise(workout=w2, name="Быстрая ходьба на дорожке (мин)", sets=1, reps=20, weight_kg=0, order=1),
        Exercise(workout=w2, name="Скручивания на коврике", sets=3, reps=15, weight_kg=0, order=2),
        Exercise(workout=w2, name="Ягодичный мостик", sets=3, reps=15, weight_kg=0, order=3),
    ])
    DailyCalories.objects.create(
        user=user,
        workout=None,
        date=today,
        calories_burned=210,
        activity_note="Утренняя разминка и пешая дорога до офиса",
    )

    # 3. Тренировка через 2 дня (Запланирована)
    future_day = today + timedelta(days=2)
    w3 = Workout.objects.create(
        user=user,
        title="Фулбоди Б: Техника и тонус",
        scheduled_at=aware_dt(future_day, 18, 30),
        status=Workout.Status.PLANNED,
        notes="Фокус на плавной негативной фазе движения.",
    )
    Exercise.objects.bulk_create([
        Exercise(workout=w3, name="Выпады на месте", sets=3, reps=10, weight_kg=0, order=1),
        Exercise(workout=w3, name="Жим гантелей сидя", sets=3, reps=12, weight_kg=6.0, order=2),
        Exercise(workout=w3, name="Гиперэкстензия", sets=3, reps=15, weight_kg=0, order=3),
    ])

    print("[OK] Успешно создано 3 тренировки, 10 упражнений и 3 записи калорий.")


run_seed()
