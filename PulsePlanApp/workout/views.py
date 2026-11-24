import calendar
import csv
import io
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from time import sleep
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.db import OperationalError, connection, transaction
from django.db.models import Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    ApplyTemplateForm,
    DailyCaloriesForm,
    ExerciseForm,
    ExerciseSetResultForm,
    SafeDataDeleteForm,
    TemplateExerciseForm,
    WorkoutForm,
    WorkoutTemplateForm,
)
from .models import (
    DailyCalories,
    Exercise,
    ExerciseSetResult,
    TemplateExercise,
    Workout,
    WorkoutTemplate,
)


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


def _safe_csv_cell(value) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{text}"
    return text


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
        .prefetch_related("exercises__set_results", "calorie_entries")
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
    workout_templates = WorkoutTemplate.objects.filter(user=request.user).prefetch_related(
        "exercises"
    )

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
        "workout_templates": workout_templates,
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
def set_result_create_view(request, exercise_id: int):
    """Сохраняет фактический результат выполненного подхода для упражнения."""
    view_mode = request.POST.get("mode")
    with transaction.atomic():
        exercise = get_object_or_404(
            Exercise.objects.select_related("workout"),
            id=exercise_id,
            workout__user=request.user,
        )
        w_date = timezone.localtime(exercise.workout.scheduled_at).date()
        form = ExerciseSetResultForm(request.POST)
        if not form.is_valid():
            _flash_form_errors(request, form)
            return _dashboard_redirect(w_date, view_mode)

        set_number = form.cleaned_data.get("set_number")
        if set_number is None:
            max_num = exercise.set_results.aggregate(m=Max("set_number"))["m"] or 0
            set_number = max_num + 1

        if exercise.set_results.filter(set_number=set_number).exists():
            messages.error(
                request,
                f"Подход №{set_number} для упражнения «{exercise.name}» уже записан.",
            )
            return _dashboard_redirect(w_date, view_mode)

        result = form.save(commit=False)
        result.exercise = exercise
        result.set_number = set_number
        try:
            result.save()
        except ValidationError as exc:
            for errs in exc.message_dict.values():
                for err in errs:
                    messages.error(request, err)
            return _dashboard_redirect(w_date, view_mode)

    messages.success(
        request,
        f"Записан подход №{result.set_number} ({result.reps} повт. × {result.weight_kg} кг) для «{exercise.name}».",
    )
    return _dashboard_redirect(w_date, view_mode)


@login_required
@require_POST
def set_result_delete_view(request, set_result_id: int):
    """Удаляет запись о выполненном подходе текущего пользователя."""
    result = get_object_or_404(
        ExerciseSetResult.objects.select_related("exercise__workout"),
        id=set_result_id,
        exercise__workout__user=request.user,
    )
    w_date = timezone.localtime(result.exercise.workout.scheduled_at).date()
    next_url = request.POST.get("next")
    view_mode = request.POST.get("mode")
    result.delete()
    messages.success(request, "Результат подхода удалён.")
    if next_url == "history":
        exercise_filter = (request.POST.get("exercise") or "").strip()
        if exercise_filter:
            return redirect(f"{reverse('exercise_history')}?{urlencode({'exercise': exercise_filter})}")
        return redirect("exercise_history")
    return _dashboard_redirect(w_date, view_mode)


@login_required
def exercise_history_view(request):
    """История всех записанных результатов подходов с фильтрацией по упражнению."""
    selected_exercise = (request.GET.get("exercise") or "").strip()

    base_qs = ExerciseSetResult.objects.filter(
        exercise__workout__user=request.user
    ).select_related("exercise", "exercise__workout")

    exercise_names = list(
        base_qs.values_list("exercise__name", flat=True).distinct().order_by("exercise__name")
    )

    if selected_exercise:
        results_qs = base_qs.filter(exercise__name__iexact=selected_exercise)
    else:
        results_qs = base_qs

    results = list(
        results_qs.order_by(
            "-exercise__workout__scheduled_at", "exercise__order", "set_number", "id"
        )
    )

    max_weight = max((r.weight_kg for r in results), default=Decimal("0.0"))
    total_volume = sum((r.volume for r in results), Decimal("0.0"))

    context = {
        "results": results,
        "exercise_names": exercise_names,
        "selected_exercise": selected_exercise,
        "total_sets": len(results),
        "max_weight": max_weight,
        "total_volume": total_volume,
    }
    return render(request, "workout/exercise_history.html", context)


@login_required
def templates_list_view(request):
    """Страница управления шаблонами тренировок."""
    templates = WorkoutTemplate.objects.filter(user=request.user).prefetch_related("exercises")
    context = {
        "templates": templates,
        "today": timezone.localdate(),
    }
    return render(request, "workout/templates_list.html", context)


@login_required
@require_POST
def template_create_view(request):
    """Создание пустого шаблона тренировки."""
    form = WorkoutTemplateForm(request.POST)
    if form.is_valid():
        tpl = form.save(commit=False)
        tpl.user = request.user
        tpl.save()
        messages.success(request, f"Шаблон «{tpl.title}» создан.")
    else:
        _flash_form_errors(request, form)
    return redirect("templates_list")


@login_required
@require_POST
def template_delete_view(request, template_id: int):
    """Удаление шаблона тренировки текущего пользователя."""
    tpl = get_object_or_404(WorkoutTemplate, id=template_id, user=request.user)
    title = tpl.title
    tpl.delete()
    messages.success(request, f"Шаблон «{title}» удалён.")
    return redirect("templates_list")


@login_required
@require_POST
def template_exercise_create_view(request, template_id: int):
    """Добавление упражнения в шаблон тренировки."""
    tpl = get_object_or_404(WorkoutTemplate, id=template_id, user=request.user)
    form = TemplateExerciseForm(request.POST)
    if form.is_valid():
        ex = form.save(commit=False)
        ex.template = tpl
        ex.order = tpl.exercises.count() + 1
        ex.save()
        messages.success(request, f"Упражнение «{ex.name}» добавлено в шаблон «{tpl.title}».")
    else:
        _flash_form_errors(request, form)
    return redirect("templates_list")


@login_required
@require_POST
def template_exercise_delete_view(request, template_exercise_id: int):
    """Удаление упражнения из шаблона тренировки."""
    ex = get_object_or_404(
        TemplateExercise, id=template_exercise_id, template__user=request.user
    )
    name = ex.name
    ex.delete()
    messages.success(request, f"Упражнение «{name}» удалено из шаблона.")
    return redirect("templates_list")


@login_required
@require_POST
def workout_save_as_template_view(request, workout_id: int):
    """Сохраняет существующую тренировку вместе со всеми упражнениями как шаблон."""
    view_mode = request.POST.get("mode")
    with transaction.atomic():
        workout = get_object_or_404(
            Workout.objects.prefetch_related("exercises"),
            id=workout_id,
            user=request.user,
        )
        w_date = timezone.localtime(workout.scheduled_at).date()
        tpl = WorkoutTemplate.objects.create(
            user=request.user,
            title=workout.title,
            notes=workout.notes,
        )
        tpl_exercises = []
        for idx, ex in enumerate(workout.exercises.all(), start=1):
            tpl_ex = TemplateExercise(
                template=tpl,
                name=ex.name,
                sets=ex.sets,
                reps=ex.reps,
                weight_kg=ex.weight_kg,
                order=idx,
            )
            tpl_ex.full_clean()
            tpl_exercises.append(tpl_ex)
        if tpl_exercises:
            TemplateExercise.objects.bulk_create(tpl_exercises)

    messages.success(request, f"Тренировка «{workout.title}» сохранена как шаблон.")
    return _dashboard_redirect(w_date, view_mode)


@login_required
@require_POST
def template_apply_view(request, template_id: int):
    """Создаёт новую тренировку на выбранную дату и время по шаблону."""
    view_mode = request.POST.get("mode")
    fallback_date = _parse_selected_date(request.POST.get("date"))
    form = ApplyTemplateForm(request.POST)

    if not form.is_valid():
        _flash_form_errors(request, form)
        return _dashboard_redirect(fallback_date, view_mode)

    target_date = form.cleaned_data.get("date") or fallback_date
    target_time = form.cleaned_data.get("time") or time(18, 30)

    with transaction.atomic():
        tpl = get_object_or_404(
            WorkoutTemplate.objects.prefetch_related("exercises"),
            id=template_id,
            user=request.user,
        )
        scheduled_at = timezone.make_aware(datetime.combine(target_date, target_time))
        workout = Workout.objects.create(
            user=request.user,
            title=tpl.title,
            scheduled_at=scheduled_at,
            status=Workout.Status.PLANNED,
            notes=tpl.notes,
        )
        new_exercises = []
        for idx, tpl_ex in enumerate(tpl.exercises.all(), start=1):
            ex = Exercise(
                workout=workout,
                name=tpl_ex.name,
                sets=tpl_ex.sets,
                reps=tpl_ex.reps,
                weight_kg=tpl_ex.weight_kg,
                order=idx,
            )
            ex.full_clean()
            new_exercises.append(ex)
        if new_exercises:
            Exercise.objects.bulk_create(new_exercises)

    messages.success(
        request,
        f"Тренировка «{workout.title}» создана из шаблона на {target_date:%d.%m.%Y}.",
    )
    return _dashboard_redirect(target_date, view_mode)


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


@login_required
def account_data_view(request):
    """Страница экспорта и безопасного удаления пользовательских данных."""
    user = request.user
    context = {
        "workouts_count": Workout.objects.filter(user=user).count(),
        "set_results_count": ExerciseSetResult.objects.filter(
            exercise__workout__user=user
        ).count(),
        "templates_count": WorkoutTemplate.objects.filter(user=user).count(),
        "calories_count": DailyCalories.objects.filter(user=user).count(),
        "delete_form": SafeDataDeleteForm(user=user),
    }
    return render(request, "workout/account_data.html", context)


@login_required
def export_user_data_view(request):
    """Экспортирует все данные текущего пользователя в формате JSON или CSV."""
    export_format = (request.GET.get("format") or "json").strip().lower()
    user = request.user
    now_local = timezone.localtime()
    stamp = now_local.strftime("%Y%m%d_%H%M")

    workouts = (
        Workout.objects.filter(user=user)
        .prefetch_related("exercises__set_results")
        .order_by("scheduled_at")
    )
    templates = WorkoutTemplate.objects.filter(user=user).prefetch_related("exercises")
    calories = DailyCalories.objects.filter(user=user).select_related("workout").order_by("date", "id")

    if export_format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(
            [
                "Тип записи",
                "Дата и время",
                "Название / Заголовок",
                "Статус",
                "Упражнение",
                "Подходы / Номер подхода",
                "Повторения",
                "Вес (кг)",
                "Калории (ккал)",
                "Заметка",
            ]
        )
        for w in workouts:
            w_dt = timezone.localtime(w.scheduled_at).strftime("%Y-%m-%d %H:%M")
            ex_list = list(w.exercises.all())
            if not ex_list:
                writer.writerow(
                    [
                        "Тренировка",
                        w_dt,
                        _safe_csv_cell(w.title),
                        w.get_status_display(),
                        "",
                        "",
                        "",
                        "",
                        "",
                        _safe_csv_cell(w.notes),
                    ]
                )
            for ex in ex_list:
                set_list = list(ex.set_results.all())
                if not set_list:
                    writer.writerow(
                        [
                            "Упражнение",
                            w_dt,
                            _safe_csv_cell(w.title),
                            w.get_status_display(),
                            _safe_csv_cell(ex.name),
                            ex.sets,
                            ex.reps,
                            str(ex.weight_kg),
                            "",
                            _safe_csv_cell(w.notes),
                        ]
                    )
                for sr in set_list:
                    writer.writerow(
                        [
                            "Подход",
                            w_dt,
                            _safe_csv_cell(w.title),
                            w.get_status_display(),
                            _safe_csv_cell(ex.name),
                            sr.set_number,
                            sr.reps,
                            str(sr.weight_kg),
                            "",
                            _safe_csv_cell(sr.notes),
                        ]
                    )
        for c in calories:
            writer.writerow(
                [
                    "Калории",
                    c.date.isoformat(),
                    _safe_csv_cell(c.workout.title if c.workout else "Дневная активность"),
                    "",
                    "",
                    "",
                    "",
                    "",
                    c.calories_burned,
                    _safe_csv_cell(c.activity_note),
                ]
            )
        for tpl in templates:
            for ex in tpl.exercises.all():
                writer.writerow(
                    [
                        "Шаблон",
                        timezone.localtime(tpl.created_at).strftime("%Y-%m-%d %H:%M"),
                        _safe_csv_cell(tpl.title),
                        "",
                        _safe_csv_cell(ex.name),
                        ex.sets,
                        ex.reps,
                        str(ex.weight_kg),
                        "",
                        _safe_csv_cell(tpl.notes),
                    ]
                )

        csv_bytes = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="pulseplan_{user.username}_{stamp}.csv"'
        )
        return response

    payload = {
        "exported_at": now_local.isoformat(),
        "timezone": str(timezone.get_current_timezone()),
        "user": {
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
        },
        "workouts": [
            {
                "id": w.id,
                "title": w.title,
                "scheduled_at": timezone.localtime(w.scheduled_at).isoformat(),
                "status": w.status,
                "notes": w.notes,
                "exercises": [
                    {
                        "name": ex.name,
                        "sets": ex.sets,
                        "reps": ex.reps,
                        "weight_kg": str(ex.weight_kg),
                        "order": ex.order,
                        "set_results": [
                            {
                                "set_number": sr.set_number,
                                "reps": sr.reps,
                                "weight_kg": str(sr.weight_kg),
                                "notes": sr.notes,
                                "recorded_at": timezone.localtime(sr.recorded_at).isoformat(),
                            }
                            for sr in ex.set_results.all()
                        ],
                    }
                    for ex in w.exercises.all()
                ],
            }
            for w in workouts
        ],
        "workout_templates": [
            {
                "id": tpl.id,
                "title": tpl.title,
                "notes": tpl.notes,
                "exercises": [
                    {
                        "name": ex.name,
                        "sets": ex.sets,
                        "reps": ex.reps,
                        "weight_kg": str(ex.weight_kg),
                        "order": ex.order,
                    }
                    for ex in tpl.exercises.all()
                ],
            }
            for tpl in templates
        ],
        "daily_calories": [
            {
                "id": c.id,
                "date": c.date.isoformat(),
                "calories_burned": c.calories_burned,
                "activity_note": c.activity_note,
                "workout_id": c.workout_id,
            }
            for c in calories
        ],
    }

    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    response = HttpResponse(json_bytes, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="pulseplan_{user.username}_{stamp}.json"'
    )
    return response


@login_required
@require_POST
def delete_user_data_view(request):
    """Безопасное удаление данных или аккаунта после проверки пароля и подтверждения."""
    form = SafeDataDeleteForm(user=request.user, data=request.POST)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return redirect("account_data")

    scope = form.cleaned_data["scope"]
    user = request.user

    if scope == SafeDataDeleteForm.SCOPE_FULL_ACCOUNT:
        if user.is_staff or user.is_superuser:
            messages.error(
                request,
                "Удаление административного аккаунта через пользовательскую форму запрещено.",
            )
            return redirect("account_data")

        with transaction.atomic():
            DailyCalories.objects.filter(user=user).delete()
            Workout.objects.filter(user=user).delete()
            WorkoutTemplate.objects.filter(user=user).delete()
            logout(request)
            user.delete()

        messages.success(request, "Ваш аккаунт и все связанные данные безвозвратно удалены.")
        return redirect("login")

    with transaction.atomic():
        DailyCalories.objects.filter(user=user).delete()
        Workout.objects.filter(user=user).delete()
        WorkoutTemplate.objects.filter(user=user).delete()

    messages.success(
        request,
        "Все ваши тренировки, результаты подходов, шаблоны и записи калорий удалены.",
    )
    return redirect("account_data")