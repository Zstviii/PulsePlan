import calendar
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import DailyCaloriesForm, ExerciseForm, WorkoutForm
from .models import DailyCalories, Exercise, Workout


def _parse_selected_date(raw_date: str | None) -> date:
    """Парсит дату из query-параметра ?date=YYYY-MM-DD с откатом на сегодня."""
    if raw_date:
        try:
            return date.fromisoformat(raw_date)
        except ValueError:
            pass
    return timezone.localdate()


def _normalize_view_mode(raw_mode: str | None) -> str:
    """Возвращает валидный режим отображения календаря ('week' или 'month')."""
    return raw_mode if raw_mode in {"week", "month"} else "week"


def _shift_month(target_date: date, delta_months: int) -> date:
    """Корректно сдвигает дату на delta_months месяцев с сохранением валидного дня."""
    total_months = (target_date.year * 12 + (target_date.month - 1)) + delta_months
    new_year = total_months // 12
    new_month = (total_months % 12) + 1
    _, max_day = calendar.monthrange(new_year, new_month)
    return date(new_year, new_month, min(target_date.day, max_day))


def _dashboard_redirect(target_date: date, view_mode: str | None = None):
    """Строит редирект на дашборд с сохранением выбранной даты и режима календаря."""
    params = {"date": target_date.isoformat()}
    if view_mode in {"week", "month"}:
        params["mode"] = view_mode
    return redirect(f"{reverse('dashboard')}?{urlencode(params)}")


def _flash_form_errors(request, form) -> None:
    """Выводит ошибки формы в Django messages."""
    for field_errors in form.errors.values():
        for err in field_errors:
            messages.error(request, err)


def _get_user_workout(user, workout_id: int) -> Workout:
    """Возвращает тренировку только если она принадлежит текущему пользователю, иначе 404."""
    return get_object_or_404(Workout, id=workout_id, user=user)


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
    view_mode = _normalize_view_mode(request.GET.get("mode"))

    start_of_week = selected_date - timedelta(days=selected_date.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    if view_mode == "month":
        cal = calendar.Calendar(firstweekday=0)
        month_weeks = cal.monthdatescalendar(selected_date.year, selected_date.month)
        range_start = month_weeks[0][0]
        range_end = month_weeks[-1][-1]
        prev_date = _shift_month(selected_date, -1)
        next_date = _shift_month(selected_date, 1)
    else:
        month_weeks = [[start_of_week + timedelta(days=i) for i in range(7)]]
        range_start = start_of_week
        range_end = end_of_week
        prev_date = selected_date - timedelta(days=7)
        next_date = selected_date + timedelta(days=7)

    workouts_qs = (
        Workout.objects.filter(
            user=request.user,
            scheduled_at__date__range=(range_start, range_end),
        )
        .prefetch_related("exercises", "calorie_entries")
        .order_by("scheduled_at")
    )

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

    selected_day_workouts = workouts_by_day.get(selected_date, [])
    selected_day_calorie_logs = DailyCalories.objects.filter(
        user=request.user, date=selected_date
    ).select_related("workout")
    selected_day_total_calories = (
        selected_day_calorie_logs.aggregate(total=Sum("calories_burned"))["total"] or 0
    )

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
        "prev_date": prev_date,
        "next_date": next_date,
        "selected_day_workouts": selected_day_workouts,
        "selected_day_calorie_logs": selected_day_calorie_logs,
        "selected_day_total_calories": selected_day_total_calories,
        "total_completed_workouts": total_completed_workouts,
        "week_total_calories": week_total_calories,
        "workout_statuses": Workout.Status.choices,
        "user_workouts_for_select": Workout.objects.filter(
            user=request.user, scheduled_at__date=selected_date
        ),
    }
    return render(request, "workout/dashboard.html", context)
@login_required
@require_POST
def workout_create_view(request):
    """Создание новой тренировки с валидацией через WorkoutForm."""
    fallback_date = _parse_selected_date(request.POST.get("date"))
    view_mode = request.POST.get("mode")
    form = WorkoutForm(request.POST)

    if form.is_valid():
        workout = form.save(user=request.user)
        target_date = timezone.localtime(workout.scheduled_at).date()
        messages.success(request, f"Тренировка «{workout.title}» добавлена в план.")
        return _dashboard_redirect(target_date, view_mode)

    _flash_form_errors(request, form)
    return _dashboard_redirect(fallback_date, view_mode)


@login_required
@require_POST
def workout_update_view(request, workout_id: int):
    """Редактирование существующей тренировки (название, дата/время, статус, заметки)."""
    workout = _get_user_workout(request.user, workout_id)
    current_date = timezone.localtime(workout.scheduled_at).date()
    view_mode = request.POST.get("mode")

    form = WorkoutForm(request.POST, instance=workout)
    if form.is_valid():
        updated = form.save()
        new_date = timezone.localtime(updated.scheduled_at).date()
        if new_date != current_date:
            updated.calorie_entries.update(date=new_date)
        messages.success(request, f"Тренировка «{updated.title}» обновлена.")
        return _dashboard_redirect(new_date, view_mode)

    _flash_form_errors(request, form)
    return _dashboard_redirect(current_date, view_mode)


@login_required
@require_POST
def workout_delete_view(request, workout_id: int):
    """Удаление тренировки текущего пользователя."""
    workout = _get_user_workout(request.user, workout_id)
    w_date = timezone.localtime(workout.scheduled_at).date()
    view_mode = request.POST.get("mode")
    title = workout.title
    workout.delete()
    messages.success(request, f"Тренировка «{title}» удалена.")
    return _dashboard_redirect(w_date, view_mode)


@login_required
@require_POST
def workout_toggle_status_view(request, workout_id: int):
    """Изменение статуса тренировки (поддержка всех 3 статусов или быстрый toggle)."""
    workout = _get_user_workout(request.user, workout_id)
    new_status = request.POST.get("status")
    view_mode = request.POST.get("mode")

    if new_status in dict(Workout.Status.choices):
        workout.status = new_status
    elif new_status is None:
        workout.status = (
            Workout.Status.COMPLETED
            if workout.status != Workout.Status.COMPLETED
            else Workout.Status.PLANNED
        )
    else:
        messages.error(request, "Указан некорректный статус тренировки.")
        w_date = timezone.localtime(workout.scheduled_at).date()
        return _dashboard_redirect(w_date, view_mode)

    workout.save(update_fields=["status"])
    w_date = timezone.localtime(workout.scheduled_at).date()
    return _dashboard_redirect(w_date, view_mode)


@login_required
@require_POST
def exercise_create_view(request, workout_id: int):
    """Добавление упражнения (подходы, повторения, вес) к тренировке через ExerciseForm."""
    workout = _get_user_workout(request.user, workout_id)
    w_date = timezone.localtime(workout.scheduled_at).date()
    view_mode = request.POST.get("mode")

    form = ExerciseForm(request.POST)
    if form.is_valid():
        exercise = form.save(commit=False)
        exercise.workout = workout
        exercise.order = workout.exercises.count() + 1
        exercise.save()
        messages.success(request, f"Упражнение «{exercise.name}» добавлено.")
    else:
        _flash_form_errors(request, form)

    return _dashboard_redirect(w_date, view_mode)


@login_required
@require_POST
def exercise_delete_view(request, exercise_id: int):
    """Удаление упражнения из тренировки текущего пользователя."""
    exercise = get_object_or_404(Exercise, id=exercise_id, workout__user=request.user)
    w_date = timezone.localtime(exercise.workout.scheduled_at).date()
    view_mode = request.POST.get("mode")
    name = exercise.name
    exercise.delete()
    messages.success(request, f"Упражнение «{name}» удалено.")
    return _dashboard_redirect(w_date, view_mode)


@login_required
@require_POST
def calorie_create_view(request):
    """Добавление записи о потраченных калориях (к тренировке или за день)."""
    fallback_date = _parse_selected_date(request.POST.get("date"))
    view_mode = request.POST.get("mode")

    form = DailyCaloriesForm(request.POST)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return _dashboard_redirect(fallback_date, view_mode)

    workout_id = form.cleaned_data.get("workout_id")
    linked_workout = None
    target_date = form.cleaned_data.get("date") or fallback_date

    if workout_id:
        linked_workout = _get_user_workout(request.user, workout_id)
        target_date = timezone.localtime(linked_workout.scheduled_at).date()

    entry = form.save(commit=False)
    entry.user = request.user
    entry.workout = linked_workout
    entry.date = target_date
    entry.save()

    messages.success(request, f"Учтено {entry.calories_burned} ккал за {target_date:%d.%m.%Y}.")
    return _dashboard_redirect(target_date, view_mode)


@login_required
@require_POST
def calorie_delete_view(request, calorie_id: int):
    """Удаление записи калорий текущего пользователя."""
    entry = get_object_or_404(DailyCalories, id=calorie_id, user=request.user)
    entry_date = entry.date
    view_mode = request.POST.get("mode")
    entry.delete()
    messages.success(request, "Запись калорий удалена.")
    return _dashboard_redirect(entry_date, view_mode)
