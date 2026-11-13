import calendar
from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db.models import Prefetch, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import DailyCalories, Exercise, Workout


def _parse_selected_date(raw_date: str | None) -> date:
    """Парсит дату из query-параметра ?date=YYYY-MM-DD с откатом на сегодня."""
    if raw_date:
        try:
            return date.fromisoformat(raw_date)
        except ValueError:
            pass
    return timezone.localdate()


def register_view(request):
    """Регистрация нового пользователя с автоматическим входом."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Добро пожаловать в PulsePlan! Ваш профиль создан.")
        return redirect("dashboard")

    return render(request, "workout/register.html", {"form": form})


def login_view(request):
    """Вход пользователя в систему."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect("dashboard")

    return render(request, "workout/login.html", {"form": form})


@require_POST
def logout_view(request):
    """Выход из профиля."""
    logout(request)
    return redirect("login")


@login_required
def dashboard_view(request):
    """
    Личный кабинет и интерактивный календарь тренировок (неделя / месяц)
    с подсчётом калорий за выбранную дату.
    """
    selected_date = _parse_selected_date(request.GET.get("date"))
    view_mode = request.GET.get("mode", "week")  # 'week' или 'month'

    # Определяем границы календаря (текущая неделя Пн-Вс или весь месяц)
    start_of_week = selected_date - timedelta(days=selected_date.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    if view_mode == "month":
        cal = calendar.Calendar(firstweekday=0)
        month_weeks = cal.monthdatescalendar(selected_date.year, selected_date.month)
        range_start = month_weeks[0][0]
        range_end = month_weeks[-1][-1]
    else:
        month_weeks = [[start_of_week + timedelta(days=i) for i in range(7)]]
        range_start = start_of_week
        range_end = end_of_week

    # Загружаем тренировки за период вместе с упражнениями и калориями
    workouts_qs = (
        Workout.objects.filter(
            user=request.user,
            scheduled_at__date__range=(range_start, range_end),
        )
        .prefetch_related("exercises", "calorie_entries")
        .order_by("scheduled_at")
    )

    # Группируем тренировки и калории по датам для сетки календаря
    workouts_by_day: dict[date, list[Workout]] = {}
    for w in workouts_qs:
        w_date = timezone.localtime(w.scheduled_at).date()
        workouts_by_day.setdefault(w_date, []).append(w)

    calories_by_day_qs = (
        DailyCalories.objects.filter(
            user=request.user,
            date__range=(range_start, range_end),
        )
        .values("date")
        .annotate(day_total=Sum("calories_burned"))
    )
    calories_by_day = {row["date"]: row["day_total"] for row in calories_by_day_qs}

    # Формируем структуру недель для рендера в таблице календаря
    calendar_weeks = []
    today = timezone.localdate()
    for week in month_weeks:
        week_days = []
        for day in week:
            week_days.append(
                {
                    "date": day,
                    "is_today": day == today,
                    "is_selected": day == selected_date,
                    "in_current_month": day.month == selected_date.month,
                    "workouts": workouts_by_day.get(day, []),
                    "total_calories": calories_by_day.get(day, 0),
                }
            )
        calendar_weeks.append(week_days)

    # Детали выбранного дня
    selected_day_workouts = workouts_by_day.get(selected_date, [])
    selected_day_calorie_logs = DailyCalories.objects.filter(
        user=request.user, date=selected_date
    ).select_related("workout")
    selected_day_total_calories = (
        selected_day_calorie_logs.aggregate(total=Sum("calories_burned"))["total"] or 0
    )

    # Общая статистика профиля
    total_completed_workouts = Workout.objects.filter(
        user=request.user, status=Workout.Status.COMPLETED
    ).count()
    week_total_calories = sum(
        calories_by_day.get(start_of_week + timedelta(days=i), 0) for i in range(7)
    )

    context = {
        "selected_date": selected_date,
        "view_mode": view_mode,
        "calendar_weeks": calendar_weeks,
        "prev_date": (selected_date - timedelta(days=7 if view_mode == "week" else 30)),
        "next_date": (selected_date + timedelta(days=7 if view_mode == "week" else 30)),
        "selected_day_workouts": selected_day_workouts,
        "selected_day_calorie_logs": selected_day_calorie_logs,
        "selected_day_total_calories": selected_day_total_calories,
        "total_completed_workouts": total_completed_workouts,
        "week_total_calories": week_total_calories,
        "user_workouts_for_select": Workout.objects.filter(
            user=request.user, scheduled_at__date=selected_date
        ),
    }
    return render(request, "workout/dashboard.html", context)


@login_required
@require_POST
def workout_create_view(request):
    """Создание новой тренировки на выбранную дату."""
    title = request.POST.get("title", "").strip()
    date_str = request.POST.get("date")
    time_str = request.POST.get("time", "18:00")
    notes = request.POST.get("notes", "").strip()

    target_date = _parse_selected_date(date_str)
    try:
        parsed_time = time.fromisoformat(time_str)
    except ValueError:
        parsed_time = time(18, 0)

    scheduled_dt = timezone.make_aware(datetime.combine(target_date, parsed_time))

    if title:
        Workout.objects.create(
            user=request.user,
            title=title,
            scheduled_at=scheduled_dt,
            notes=notes,
        )
        messages.success(request, f"Тренировка «{title}» добавлена в план.")
    else:
        messages.error(request, "Укажите название тренировки.")

    return redirect(f"/?date={target_date.isoformat()}")


@login_required
@require_POST
def workout_toggle_status_view(request, workout_id: int):
    """Быстрое переключение статуса тренировки (Запланирована ↔ Выполнена)."""
    workout = get_object_or_404(Workout, id=workout_id, user=request.user)
    new_status = request.POST.get("status")

    if new_status in dict(Workout.Status.choices):
        workout.status = new_status
    else:
        workout.status = (
            Workout.Status.COMPLETED
            if workout.status != Workout.Status.COMPLETED
            else Workout.Status.PLANNED
        )
    workout.save(update_fields=["status"])

    w_date = timezone.localtime(workout.scheduled_at).date()
    return redirect(f"/?date={w_date.isoformat()}")


@login_required
@require_POST
def exercise_create_view(request, workout_id: int):
    """Добавление упражнения (подходы, повторения, вес) к тренировке."""
    workout = get_object_or_404(Workout, id=workout_id, user=request.user)
    name = request.POST.get("name", "").strip()
    sets = int(request.POST.get("sets") or 3)
    reps = int(request.POST.get("reps") or 10)
    weight_raw = request.POST.get("weight_kg") or "0"

    if name:
        next_order = workout.exercises.count() + 1
        Exercise.objects.create(
            workout=workout,
            name=name,
            sets=max(1, sets),
            reps=max(1, reps),
            weight_kg=max(0.0, float(weight_raw)),
            order=next_order,
        )
        messages.success(request, f"Упражнение «{name}» добавлено.")

    w_date = timezone.localtime(workout.scheduled_at).date()
    return redirect(f"/?date={w_date.isoformat()}")


@login_required
@require_POST
def calorie_create_view(request):
    """Добавление записи о потраченных калориях (к тренировке или за день)."""
    target_date = _parse_selected_date(request.POST.get("date"))
    calories_raw = request.POST.get("calories_burned", "0")
    workout_id = request.POST.get("workout_id")
    activity_note = request.POST.get("activity_note", "").strip()

    try:
        calories_burned = int(calories_raw)
    except ValueError:
        calories_burned = 0

    if calories_burned <= 0:
        messages.error(request, "Количество калорий должно быть положительным числом.")
        return redirect(f"/?date={target_date.isoformat()}")

    linked_workout = None
    if workout_id:
        linked_workout = Workout.objects.filter(
            id=workout_id, user=request.user
        ).first()
        if linked_workout:
            target_date = timezone.localtime(linked_workout.scheduled_at).date()

    DailyCalories.objects.create(
        user=request.user,
        workout=linked_workout,
        date=target_date,
        calories_burned=calories_burned,
        activity_note=activity_note,
    )
    messages.success(request, f"Учтено {calories_burned} ккал за {target_date:%d.%m.%Y}.")
    return redirect(f"/?date={target_date.isoformat()}")
