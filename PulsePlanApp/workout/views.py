import calendar
from datetime import date, timedelta
from time import sleep
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db import OperationalError, connection, transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import DailyCaloriesForm, ExerciseForm, WorkoutForm
from .models import DailyCalories, Exercise, Workout


def _parse_selected_date(raw_date: str | None) -> date:
    if raw_date:
        try:
            return date.fromisoformat(raw_date)
        except ValueError:
            pass
    return timezone.localdate()


def _parse_anchor_day(raw_day: str | None, fallback_day: int) -> int:
    if raw_day:
        try:
            val = int(raw_day)
            if 1 <= val <= 31:
                return val
        except ValueError:
            pass
    return fallback_day


def _normalize_view_mode(raw_mode: str | None) -> str:
    return raw_mode if raw_mode in {"week", "month"} else "week"


def _shift_month(target_date: date, delta_months: int, anchor_day: int | None = None) -> date:
    month_index = target_date.year * 12 + target_date.month - 1 + delta_months
    new_year, month_offset = divmod(month_index, 12)
    new_month = month_offset + 1
    if not 1 <= new_year <= 9999:
        return target_date
    _, max_day = calendar.monthrange(new_year, new_month)
    desired_day = anchor_day if anchor_day is not None else target_date.day
    return date(new_year, new_month, min(desired_day, max_day))


def _dashboard_redirect(target_date: date, view_mode: str | None = None, anchor_day: int | None = None):
    params = {"date": target_date.isoformat()}
    if view_mode in {"week", "month"}:
        params["mode"] = view_mode
    if anchor_day is not None:
        params["day"] = anchor_day
    return redirect(f"{reverse('dashboard')}?{urlencode(params)}")


def _flash_form_errors(request, form) -> None:
    for field_errors in form.errors.values():
        for err in field_errors:
            messages.error(request, err)


def _get_user_workout(user, workout_id: int) -> Workout:
    return get_object_or_404(Workout, id=workout_id, user=user)


def register_view(request):
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
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect("dashboard")

    return render(request, "workout/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def dashboard_view(request):
    selected_date = _parse_selected_date(request.GET.get("date"))
    anchor_day = _parse_anchor_day(request.GET.get("day"), selected_date.day)
    view_mode = _normalize_view_mode(request.GET.get("mode"))
    today = timezone.localdate()

    start_of_week = selected_date - timedelta(days=selected_date.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    if view_mode == "month":
        cal = calendar.Calendar(firstweekday=0)
        month_weeks = cal.monthdatescalendar(selected_date.year, selected_date.month)
        range_start = month_weeks[0][0]
        range_end = month_weeks[-1][-1]
        prev_date = _shift_month(selected_date, -1, anchor_day)
        next_date = _shift_month(selected_date, 1, anchor_day)
    else:
        month_weeks = [[start_of_week + timedelta(days=i) for i in range(7)]]
        range_start = start_of_week
        range_end = end_of_week
        prev_date = selected_date - timedelta(days=7)
        next_date = selected_date + timedelta(days=7)

    stats_range_start = min(range_start, start_of_week)
    stats_range_end = max(range_end, end_of_week)

    tz = timezone.get_current_timezone()
    dt_start = timezone.make_aware(
        timezone.datetime.combine(stats_range_start, timezone.datetime.min.time()), tz
    )
    dt_end = timezone.make_aware(
        timezone.datetime.combine(
            stats_range_end + timedelta(days=1), timezone.datetime.min.time()
        ),
        tz,
    )

    workouts_qs = (
        Workout.objects.filter(
            user=request.user,
            scheduled_at__gte=dt_start,
            scheduled_at__lt=dt_end,
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
            date__range=(stats_range_start, stats_range_end),
        )
        .filter(Q(workout__isnull=True) | Q(workout__status=Workout.Status.COMPLETED))
        .values("date")
        .annotate(day_total=Sum("calories_burned"))
    )
    calories_by_day = {row["date"]: row["day_total"] for row in calories_by_day_qs}

    calendar_weeks = []
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
    selected_day_calorie_logs = (
        DailyCalories.objects.filter(user=request.user, date=selected_date)
        .filter(Q(workout__isnull=True) | Q(workout__status=Workout.Status.COMPLETED))
        .select_related("workout")
    )
    selected_day_total_calories = (
        selected_day_calorie_logs.aggregate(total=Sum("calories_burned"))["total"] or 0
    )

    total_completed_workouts = Workout.objects.filter(
        user=request.user, status=Workout.Status.COMPLETED
    ).count()

    week_total_calories = sum(
        calories_by_day.get(start_of_week + timedelta(days=i), 0) for i in range(7)
    )

    week_workouts_count = sum(
        len(workouts_by_day.get(start_of_week + timedelta(days=i), []))
        for i in range(7)
    )

    completed_on_selected_day = [
        w for w in selected_day_workouts if w.status == Workout.Status.COMPLETED
    ]

    context = {
        "selected_date": selected_date,
        "anchor_day": anchor_day,
        "today": today,
        "is_future_selected": selected_date > today,
        "view_mode": view_mode,
        "calendar_weeks": calendar_weeks,
        "prev_date": prev_date,
        "next_date": next_date,
        "selected_day_workouts": selected_day_workouts,
        "selected_day_calorie_logs": selected_day_calorie_logs,
        "selected_day_total_calories": selected_day_total_calories,
        "total_completed_workouts": total_completed_workouts,
        "week_total_calories": week_total_calories,
        "week_workouts_count": week_workouts_count,
        "is_empty_week": week_workouts_count == 0 and week_total_calories == 0,
        "week_period_start": start_of_week,
        "week_period_end": end_of_week,
        "workout_statuses": Workout.Status.choices,
        "user_workouts_for_select": completed_on_selected_day,
    }
    return render(request, "workout/dashboard.html", context)


@login_required
@require_POST
def workout_create_view(request):
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
    view_mode = request.POST.get("mode")

    with transaction.atomic():
        workout = get_object_or_404(
            Workout.objects.select_for_update(), id=workout_id, user=request.user
        )
        current_date = timezone.localtime(workout.scheduled_at).date()
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
    view_mode = request.POST.get("mode")
    with transaction.atomic():
        workout = get_object_or_404(
            Workout.objects.select_for_update(), id=workout_id, user=request.user
        )
        w_date = timezone.localtime(workout.scheduled_at).date()
        title = workout.title
        workout.calorie_entries.all().delete()
        workout.delete()
    messages.success(request, f"Тренировка «{title}» удалена.")
    return _dashboard_redirect(w_date, view_mode)


@login_required
@require_POST
def workout_toggle_status_view(request, workout_id: int):
    new_status = request.POST.get("status")
    view_mode = request.POST.get("mode")
    with transaction.atomic():
        workout = get_object_or_404(
            Workout.objects.select_for_update(), id=workout_id, user=request.user
        )
        if new_status is None:
            new_status = (
                Workout.Status.PLANNED
                if workout.status == Workout.Status.COMPLETED
                else Workout.Status.COMPLETED
            )
        if new_status not in dict(Workout.Status.choices):
            messages.error(request, "Указан некорректный статус тренировки.")
            w_date = timezone.localtime(workout.scheduled_at).date()
            return _dashboard_redirect(w_date, view_mode)
        if not Workout.can_transition(workout.status, new_status):
            messages.error(request, "Такой переход статуса тренировки недопустим.")
            w_date = timezone.localtime(workout.scheduled_at).date()
            return _dashboard_redirect(w_date, view_mode)
        if workout.status != new_status:
            workout.status = new_status
            workout.save(update_fields=["status"])
        w_date = timezone.localtime(workout.scheduled_at).date()
    return _dashboard_redirect(w_date, view_mode)


@login_required
@require_POST
def exercise_create_view(request, workout_id: int):
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
def exercise_update_view(request, exercise_id: int):
    with transaction.atomic():
        exercise = get_object_or_404(
            Exercise.objects.select_related("workout"),
            id=exercise_id,
            workout__user=request.user,
        )
        w_date = timezone.localtime(exercise.workout.scheduled_at).date()
        form = ExerciseForm(request.POST, instance=exercise)
        if form.is_valid():
            updated = form.save()
            messages.success(request, f"Упражнение «{updated.name}» обновлено.")
            return _dashboard_redirect(w_date, request.POST.get("mode"))

    _flash_form_errors(request, form)
    return _dashboard_redirect(w_date, request.POST.get("mode"))


@login_required
@require_POST
def exercise_delete_view(request, exercise_id: int):
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
    fallback_date = _parse_selected_date(request.POST.get("date"))
    view_mode = request.POST.get("mode")

    form = DailyCaloriesForm(request.POST)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return _dashboard_redirect(fallback_date, view_mode)

    workout_id = form.cleaned_data.get("workout_id")
    target_date = form.cleaned_data.get("date") or fallback_date

    if workout_id:
        for attempt in range(5):
            try:
                with transaction.atomic():
                    linked_workout = get_object_or_404(
                        Workout.objects.select_for_update(),
                        id=workout_id,
                        user=request.user,
                    )
                    target_date = timezone.localtime(linked_workout.scheduled_at).date()
                    if linked_workout.status != Workout.Status.COMPLETED:
                        messages.error(
                            request,
                            "Калории можно записать только для завершённой тренировки.",
                        )
                        return _dashboard_redirect(target_date, view_mode)

                    existing_entries = list(
                        DailyCalories.objects.filter(workout=linked_workout).order_by("pk")[:2]
                    )
                    if len(existing_entries) > 1:
                        messages.error(
                            request,
                            "Для тренировки уже есть несколько записей калорий. "
                            "Удалите лишние записи перед обновлением.",
                        )
                        return _dashboard_redirect(target_date, view_mode)

                    if existing_entries:
                        entry = existing_entries[0]
                        entry.calories_burned = form.cleaned_data["calories_burned"]
                        entry.activity_note = form.cleaned_data["activity_note"]
                        entry.date = target_date
                    else:
                        entry = form.save(commit=False)
                        entry.user = request.user
                        entry.workout = linked_workout
                        entry.date = target_date
                    entry.save()
                break
            except OperationalError as error:
                if (
                    connection.vendor != "sqlite"
                    or "locked" not in str(error).lower()
                    or attempt == 4
                ):
                    raise
                sleep(0.05 * (attempt + 1))
    else:
        if target_date > timezone.localdate():
            messages.error(request, "Нельзя добавить запись калорий за будущую дату.")
            return _dashboard_redirect(fallback_date, view_mode)
        entry = form.save(commit=False)
        entry.user = request.user
        entry.workout = None
        entry.date = target_date
        entry.save()

    messages.success(request, f"Учтено {entry.calories_burned} ккал за {target_date:%d.%m.%Y}.")
    return _dashboard_redirect(target_date, view_mode)


@login_required
@require_POST
def calorie_delete_view(request, calorie_id: int):
    entry = get_object_or_404(DailyCalories, id=calorie_id, user=request.user)
    entry_date = entry.date
    view_mode = request.POST.get("mode")
    entry.delete()
    messages.success(request, "Запись калорий удалена.")
    return _dashboard_redirect(entry_date, view_mode)