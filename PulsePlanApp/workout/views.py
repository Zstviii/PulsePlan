import calendar
import csv
import io
import json
from datetime import date, datetime, timedelta
from hashlib import sha256
from time import sleep
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django import forms
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.db import OperationalError, connection, transaction
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    CALENDAR_MAX_DATE,
    CALENDAR_MIN_DATE,
    DailyCaloriesForm,
    ExerciseForm,
    ExerciseResultForm,
    RecoveryEmailForm,
    RegistrationForm,
    SafeDataDeleteForm,
    TemplateExerciseForm,
    TemplateUseForm,
    WorkoutForm,
    WorkoutTemplateForm,
)
from .models import (
    CreateSubmission,
    DailyCalories,
    Exercise,
    ExerciseResult,
    TemplateExercise,
    TemplateUseSubmission,
    Workout,
    WorkoutTemplate,
)

def _parse_selected_date(raw_date: str | None) -> date:
    if raw_date:
        try:
            selected = date.fromisoformat(raw_date)
            if CALENDAR_MIN_DATE <= selected <= CALENDAR_MAX_DATE:
                return selected
        except (OverflowError, ValueError):
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
    shifted = date(new_year, new_month, min(desired_day, max_day))
    return shifted if CALENDAR_MIN_DATE <= shifted <= CALENDAR_MAX_DATE else target_date


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
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return f"'{text}"
    return text


def register_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = RegistrationForm(request.POST if request.method == 'POST' else None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Добро пожаловать в PulsePlan! Ваш профиль создан.")
        return redirect("dashboard")

    return render(request, "workout/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = AuthenticationForm(request, data=request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        next_url = request.POST.get("next")
        if url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
            return redirect(next_url)
        return redirect("dashboard")

    return render(request, "workout/login.html", {"form": form, "next": request.POST.get("next", request.GET.get("next", ""))})


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
        previous_week = selected_date - timedelta(days=7)
        following_week = selected_date + timedelta(days=7)
        prev_date = (
            previous_week
            if previous_week >= CALENDAR_MIN_DATE
            else selected_date
        )
        next_date = (
            following_week
            if following_week <= CALENDAR_MAX_DATE
            else selected_date
        )

    can_navigate_previous = prev_date != selected_date
    can_navigate_next = next_date != selected_date

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
            day_workouts = workouts_by_day.get(day, [])
            week_days.append(
                {
                    "date": day,
                    "is_navigable": CALENDAR_MIN_DATE <= day <= CALENDAR_MAX_DATE,
                    "is_today": day == today,
                    "is_selected": day == selected_date,
                    "in_current_month": day.month == selected_date.month,
                    "workouts": day_workouts,
                    "planned_count": sum(
                        workout.status == Workout.Status.PLANNED
                        for workout in day_workouts
                    ),
                    "completed_count": sum(
                        workout.status == Workout.Status.COMPLETED
                        for workout in day_workouts
                    ),
                    "skipped_count": sum(
                        workout.status == Workout.Status.SKIPPED
                        for workout in day_workouts
                    ),
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
    selected_day_calorie_logs = list(selected_day_calorie_logs)
    workout_calorie_logs = [entry for entry in selected_day_calorie_logs if entry.workout_id]
    activity_calorie_logs = [entry for entry in selected_day_calorie_logs if not entry.workout_id]
    selected_day_total_calories = sum(entry.calories_burned for entry in selected_day_calorie_logs)

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
        "can_navigate_previous": can_navigate_previous,
        "can_navigate_next": can_navigate_next,
        "selected_day_workouts": selected_day_workouts,
        "selected_day_calorie_logs": selected_day_calorie_logs,
        "workout_calorie_logs": workout_calorie_logs,
        "activity_calorie_logs": activity_calorie_logs,
        "workout_submission_token": uuid4(),
        "calorie_submission_token": uuid4(),
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


def _render_entry_form(request, form, title, selected_date, *, token=None, description=None):
    for name, field in form.fields.items():
        input_type = getattr(field.widget, "input_type", None)
        if form.has_error(name) and input_type in {"date", "time", "number"}:
            # Native date and number fields discard malformed strings on render.
            field.widget = forms.TextInput(attrs={"inputmode": "decimal"} if input_type == "number" else {})
        field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
    period = request.POST if request.method == "POST" else request.GET
    return render(request, "workout/entry_form.html", {
        "form": form,
        "heading": title,
        "description": description,
        "selected_date": selected_date,
        "view_mode": _normalize_view_mode(period.get("mode")),
        "anchor_day": _parse_anchor_day(period.get("day"), selected_date.day),
        "submission_token": token,
    })


def _add_model_errors(form, error):
    if hasattr(error, "error_dict"):
        for name, errors in error.error_dict.items():
            field = "workout_id" if name == "workout" else name
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, error)


def _submission_key(request, form, valid):
    token = request.POST.get("submission_token")
    if "submission_token" in request.POST:
        try:
            parsed_token = UUID(token)
        except (ValueError, AttributeError):
            form.add_error(None, "Не удалось распознать отправку. Проверьте поля и отправьте форму ещё раз.")
            return None, uuid4()
        return parsed_token.hex if valid else None, parsed_token

    # Older clients omitted the token. Their exact payload still gets a stable key.
    payload = json.dumps({name: request.POST.get(name, "") for name in form.fields}, sort_keys=True)
    return sha256(payload.encode()).hexdigest() if valid else None, uuid4()


def _save_once(request, action, key, target_date, save):
    for attempt in range(5):
        try:
            with transaction.atomic():
                submission, created = CreateSubmission.objects.get_or_create(
                    user=request.user, action=action, key=key,
                    defaults={"redirect_date": target_date},
                )
                if created:
                    actual_date, notice = save()
                    if actual_date != submission.redirect_date:
                        submission.redirect_date = actual_date
                        submission.save(update_fields=["redirect_date"])
                else:
                    notice = "Эта отправка уже была обработана."
            messages.success(request, notice)
            return _dashboard_redirect(
                submission.redirect_date, request.POST.get("mode"), submission.redirect_date.day,
            )
        except OperationalError as error:
            if connection.vendor != "sqlite" or "locked" not in str(error).lower() or attempt == 4:
                raise
            sleep(0.05 * (attempt + 1))


@login_required
@require_http_methods(["GET", "POST"])
def workout_create_view(request):
    data = request.POST if request.method == "POST" else None
    selected_date = _parse_selected_date((data if data is not None else request.GET).get("date"))
    form = WorkoutForm(data, initial={"date": selected_date, "time": "18:30"})
    token = uuid4()
    if data is not None:
        key, token = _submission_key(request, form, form.is_valid())
        if key:
            def save():
                fresh_form = WorkoutForm(data)
                workout = fresh_form.save(user=request.user)
                target_date = timezone.localtime(workout.scheduled_at).date()
                return target_date, f"Тренировка «{workout.title}» добавлена в план."

            try:
                return _save_once(request, "workout", key, selected_date, save)
            except ValidationError as error:
                _add_model_errors(form, error)
    return _render_entry_form(request, form, "Запланировать тренировку", selected_date, token=token)


@login_required
@require_http_methods(["GET", "POST"])
def workout_update_view(request, workout_id: int):
    if request.method == "POST":
        try:
            with transaction.atomic():
                workout = get_object_or_404(
                    Workout.objects.select_for_update(), id=workout_id, user=request.user,
                )
                current_date = timezone.localtime(workout.scheduled_at).date()
                form = WorkoutForm(request.POST, instance=workout)
                if form.is_valid():
                    updated = form.save()
                    new_date = timezone.localtime(updated.scheduled_at).date()
                    messages.success(request, f"Тренировка «{updated.title}» обновлена.")
                    return _dashboard_redirect(new_date, request.POST.get("mode"), new_date.day)
        except ValidationError as error:
            _add_model_errors(form, error)
    else:
        workout = _get_user_workout(request.user, workout_id)
        scheduled = timezone.localtime(workout.scheduled_at)
        current_date = scheduled.date()
        form = WorkoutForm(instance=workout, initial={"date": current_date, "time": scheduled.time()})
    return _render_entry_form(request, form, "Редактировать тренировку", current_date)


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
@require_http_methods(["GET", "POST"])
def exercise_create_view(request, workout_id: int):
    workout = _get_user_workout(request.user, workout_id)
    workout_date = timezone.localtime(workout.scheduled_at).date()
    data = request.POST if request.method == "POST" else None
    form = ExerciseForm(data)
    if form.is_valid():
        exercise = form.save(commit=False)
        exercise.workout = workout
        exercise.order = workout.exercises.count() + 1
        try:
            exercise.save()
        except ValidationError as error:
            _add_model_errors(form, error)
        else:
            messages.success(request, f"Упражнение «{exercise.name}» добавлено.")
            return _dashboard_redirect(workout_date, request.POST.get("mode"))
    return _render_entry_form(request, form, "Добавить упражнение", workout_date, description=workout.title)


@login_required
@require_http_methods(["GET", "POST"])
def exercise_update_view(request, exercise_id: int):
    exercise = get_object_or_404(
        Exercise.objects.select_related("workout"), id=exercise_id, workout__user=request.user,
    )
    workout_date = timezone.localtime(exercise.workout.scheduled_at).date()
    data = request.POST if request.method == "POST" else None
    form = ExerciseForm(data, instance=exercise)
    if form.is_valid():
        try:
            updated = form.save()
        except ValidationError as error:
            _add_model_errors(form, error)
        else:
            messages.success(request, f"Упражнение «{updated.name}» обновлено.")
            return _dashboard_redirect(workout_date, request.POST.get("mode"))
    return _render_entry_form(
        request, form, "Редактировать упражнение", workout_date, description=exercise.workout.title,
    )


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


def _calorie_workout_options(user, selected_date, submitted_id=None):
    choices = [("", "Общая активность за день")]
    workouts = Workout.objects.filter(user=user, status=Workout.Status.COMPLETED).filter(
        Q(scheduled_at__date=selected_date) | Q(pk=submitted_id)
    )
    choices.extend((w.pk, f"{w.title} · {timezone.localtime(w.scheduled_at):%d.%m.%Y %H:%M}") for w in workouts)
    return choices


@login_required
@require_http_methods(["GET", "POST"])
def calorie_create_view(request):
    data = request.POST if request.method == "POST" else None
    selected_date = _parse_selected_date((data if data is not None else request.GET).get("date"))
    form = DailyCaloriesForm(data, initial={"date": selected_date})
    token = uuid4()
    workout_id = None
    if data is not None:
        key, token = _submission_key(request, form, form.is_valid())
        workout_id = form.cleaned_data.get("workout_id")
        if key:
            def save():
                entry = DailyCalories(user=request.user, date=form.cleaned_data.get("date") or selected_date)
                if workout_id:
                    linked_workout = Workout.objects.select_for_update().filter(
                        id=workout_id, user=request.user,
                    ).first()
                    if linked_workout is None:
                        if "submission_token" not in data:
                            raise Http404
                        raise ValidationError({"workout": "Выберите доступную тренировку."})
                    entry = DailyCalories.objects.filter(
                        user=request.user, workout=linked_workout,
                    ).first() or entry
                    entry.workout = linked_workout
                entry.calories_burned = form.cleaned_data["calories_burned"]
                entry.activity_note = form.cleaned_data["activity_note"]
                entry.save()
                return entry.date, f"Учтено {entry.calories_burned} ккал за {entry.date:%d.%m.%Y}."

            try:
                return _save_once(request, "calorie", key, selected_date, save)
            except ValidationError as error:
                _add_model_errors(form, error)

    form.fields["workout_id"].widget.choices = _calorie_workout_options(
        request.user, selected_date, workout_id,
    )
    return _render_entry_form(request, form, "Добавить расход калорий", selected_date, token=token)


@login_required
@require_http_methods(["GET", "POST"])
def calorie_update_view(request, calorie_id: int):
    entry = get_object_or_404(
        DailyCalories.objects.filter(Q(workout__isnull=True) | Q(workout__user=request.user)),
        id=calorie_id, user=request.user,
    )
    original_date = entry.date
    data = request.POST.copy() if request.method == "POST" else None
    if data is not None:
        data.pop("workout_id", None)
        if entry.workout_id:
            data["date"] = entry.date.isoformat()
    form = DailyCaloriesForm(data, instance=entry)
    del form.fields["workout_id"]
    if entry.workout_id:
        del form.fields["date"]
    else:
        form.fields["date"].required = True
    if form.is_valid():
        try:
            updated = form.save()
        except ValidationError as error:
            _add_model_errors(form, error)
        else:
            messages.success(request, "Запись калорий обновлена.")
            return _dashboard_redirect(updated.date, request.POST.get("mode"), updated.date.day)
    return _render_entry_form(
        request, form, "Редактировать расход калорий", original_date,
        description=str(entry.workout) if entry.workout_id else "Общая активность за день",
    )


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
def template_list_view(request):
    templates = WorkoutTemplate.objects.filter(user=request.user).prefetch_related("exercises")
    return render(request, "workout/template_list.html", {"templates": templates})


@login_required
def template_create_view(request):
    if request.method == "POST":
        form = WorkoutTemplateForm(request.POST)
        if form.is_valid():
            template = form.save(commit=False)
            template.user = request.user
            template.save()
            messages.success(request, f"Шаблон «{template.name}» создан.")
            return redirect("template_edit", template.id)
    else:
        form = WorkoutTemplateForm()
    return render(request, "workout/template_form.html", {"form": form, "template_obj": None})


@login_required
def template_edit_view(request, template_id):
    template = get_object_or_404(WorkoutTemplate, id=template_id, user=request.user)
    if request.method == "POST":
        form = WorkoutTemplateForm(request.POST, instance=template)
        if form.is_valid():
            form.save()
            messages.success(request, "Шаблон обновлён.")
            return redirect("template_edit", template.id)
    else:
        form = WorkoutTemplateForm(instance=template)
    return render(request, "workout/template_form.html", {"form": form, "template_obj": template})


@login_required
@require_POST
def template_exercise_add_view(request, template_id):
    template = get_object_or_404(WorkoutTemplate, id=template_id, user=request.user)
    if request.method == "POST":
        form = TemplateExerciseForm(request.POST)
        if form.is_valid():
            exercise = form.save(commit=False)
            exercise.template = template
            exercise.order = template.exercises.count() + 1
            exercise.save()
            messages.success(request, f"Упражнение «{exercise.name}» добавлено в шаблон.")
        else:
            _flash_form_errors(request, form)
    return redirect("template_edit", template.id)


@login_required
@require_POST
def template_exercise_delete_view(request, exercise_id):
    exercise = get_object_or_404(TemplateExercise, id=exercise_id, template__user=request.user)
    template_id = exercise.template_id
    exercise.delete()
    messages.success(request, "Упражнение шаблона удалено.")
    return redirect("template_edit", template_id)


@login_required
@require_POST
def template_delete_view(request, template_id):
    template = get_object_or_404(WorkoutTemplate, id=template_id, user=request.user)
    name = template.name
    template.delete()
    messages.success(request, f"Шаблон «{name}» удалён.")
    return redirect("template_list")


@login_required
@require_http_methods(["GET", "POST"])
def template_use_view(request, template_id):
    template = get_object_or_404(WorkoutTemplate.objects.prefetch_related("exercises"), id=template_id, user=request.user)
    form = TemplateUseForm(request.POST if request.method == "POST" else None)
    if request.method == "POST":
        if form.is_valid():
            template_exercises = list(template.exercises.all())
            if not template_exercises:
                form.add_error(None, "Нельзя создать тренировку: в шаблоне нет упражнений.")
            else:
                target_date = form.cleaned_data["date"]
                parsed_time = form.cleaned_data["time"]
                with transaction.atomic():
                    submission, is_new = TemplateUseSubmission.objects.get_or_create(
                        user=request.user,
                        token=form.cleaned_data["submission_token"],
                    )
                    if not is_new:
                        submission = TemplateUseSubmission.objects.select_for_update().get(
                            pk=submission.pk
                        )
                        if submission.workout_id:
                            existing_workout = Workout.objects.filter(
                                pk=submission.workout_id, user=request.user
                            ).first()
                            if existing_workout:
                                return _dashboard_redirect(
                                    timezone.localtime(
                                        existing_workout.scheduled_at
                                    ).date()
                                )
                            messages.info(request, "Эта отправка уже была обработана.")
                            return redirect("dashboard")

                    workout = Workout.objects.create(
                        user=request.user,
                        title=template.name,
                        notes=template.notes,
                        scheduled_at=timezone.make_aware(
                            datetime.combine(target_date, parsed_time)
                        ),
                    )
                    Exercise.objects.bulk_create(
                        [
                            Exercise(
                                workout=workout,
                                name=item.name,
                                sets=item.sets,
                                reps=item.reps,
                                weight_kg=item.weight_kg,
                                order=item.order,
                            )
                            for item in template_exercises
                        ]
                    )
                    submission.workout = workout
                    submission.save(update_fields=["workout"])
                messages.success(request, f"Тренировка «{workout.title}» создана из шаблона.")
                return _dashboard_redirect(target_date)
    return render(
        request,
        "workout/template_use.html",
        {"template_obj": template, "form": form},
    )


@login_required
def exercise_history_view(request, exercise_id):
    exercise = get_object_or_404(Exercise, id=exercise_id, workout__user=request.user)
    results = ExerciseResult.objects.filter(
        user=request.user,
        workout__user=request.user,
        exercise_name__iexact=exercise.name,
    ).filter(
        Q(exercise__isnull=True) | Q(exercise__workout__user=request.user)
    ).select_related("workout", "exercise")
    return render(request, "workout/exercise_history.html", {
        "exercise": exercise, "results": results,
    })


@login_required
def exercise_result_add_view(request, exercise_id):
    exercise = get_object_or_404(Exercise, id=exercise_id, workout__user=request.user)
    if request.method == "POST":
        form = ExerciseResultForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                workout = get_object_or_404(
                    Workout.objects.select_for_update(),
                    pk=exercise.workout_id,
                    user=request.user,
                )
                if workout.status != Workout.Status.COMPLETED:
                    form.add_error(
                        None,
                        "Результаты можно записывать только для выполненной тренировки.",
                    )
                else:
                    result = form.save(commit=False)
                    result.workout = workout
                    result.exercise = exercise
                    result.exercise_name = exercise.name
                    result.performed_at = workout.scheduled_at
                    existing = ExerciseResult.objects.filter(
                        workout=workout,
                        exercise=exercise,
                        set_number=result.set_number,
                    ).first()
                    if existing and existing.user_id != request.user.pk:
                        form.add_error(
                            None,
                            "Результат этого подхода принадлежит другому аккаунту.",
                        )
                    else:
                        ExerciseResult.objects.update_or_create(
                            workout=workout,
                            exercise=exercise,
                            set_number=result.set_number,
                            defaults={
                                "user": request.user,
                                "exercise_name": result.exercise_name,
                                "reps": result.reps,
                                "weight_kg": result.weight_kg,
                                "performed_at": result.performed_at,
                            },
                        )
                        messages.success(request, "Результат подхода сохранён.")
                        return redirect("exercise_history", exercise.id)
    else:
        form = ExerciseResultForm(initial={"set_number": exercise.sets, "reps": exercise.reps, "weight_kg": exercise.weight_kg})
    return render(request, "workout/exercise_result_form.html", {"form": form, "exercise": exercise})


@login_required
def settings_view(request, recovery_form=None):
    user = request.user
    context = {
        "workouts_count": Workout.objects.filter(user=user).count(),
        "set_results_count": ExerciseResult.objects.filter(
            user=user, workout__user=user
        ).filter(Q(exercise__isnull=True) | Q(exercise__workout__user=user)).count(),
        "exercises_count": Exercise.objects.filter(workout__user=user).count(),
        "templates_count": WorkoutTemplate.objects.filter(user=user).count(),
        "calories_count": DailyCalories.objects.filter(user=user)
        .filter(Q(workout__isnull=True) | Q(workout__user=user))
        .count(),
        "recovery_form": recovery_form if recovery_form is not None else RecoveryEmailForm(
            user, initial={"email": user.email},
        ),
    }
    return render(request, "workout/account_data.html", context)


@login_required
@require_POST
def recovery_email_view(request):
    form = RecoveryEmailForm(request.user, request.POST)
    if not form.is_valid():
        return settings_view(request, recovery_form=form)
    request.user.email = form.cleaned_data["email"]
    request.user.save(update_fields=["email"])
    messages.success(request, "Адрес для восстановления доступа сохранён.")
    return redirect("settings")


@login_required
def export_user_data_view(request):
    export_format = (request.GET.get("format") or "json").strip().lower()
    if export_format not in {"json", "csv"}:
        return HttpResponseBadRequest("Неподдерживаемый формат экспорта.")

    user = request.user
    exported_at = timezone.localtime()
    stamp = exported_at.strftime("%Y%m%d_%H%M%S")
    workouts = list(
        Workout.objects.filter(user=user)
        .prefetch_related("exercises")
        .order_by("scheduled_at", "id")
    )
    results = list(
        ExerciseResult.objects.filter(user=user, workout__user=user)
        .filter(Q(exercise__isnull=True) | Q(exercise__workout__user=user))
        .select_related("workout", "exercise")
        .order_by("performed_at", "id")
    )
    templates = list(
        WorkoutTemplate.objects.filter(user=user)
        .prefetch_related("exercises")
        .order_by("name", "id")
    )
    calories = list(
        DailyCalories.objects.filter(user=user)
        .filter(Q(workout__isnull=True) | Q(workout__user=user))
        .select_related("workout")
        .order_by("date", "id")
    )

    if export_format == "json":
        payload = {
            "schema_version": 1,
            "exported_at": exported_at.isoformat(),
            "timezone": str(timezone.get_current_timezone()),
            "user": {
                "id": user.pk,
                "username": user.get_username(),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
            },
            "workouts": [
                {
                    "id": workout.pk,
                    "title": workout.title,
                    "scheduled_at": timezone.localtime(workout.scheduled_at).isoformat(),
                    "local_date": timezone.localtime(workout.scheduled_at).date().isoformat(),
                    "status": workout.status,
                    "notes": workout.notes,
                    "exercises": [
                        {
                            "id": exercise.pk,
                            "workout_id": workout.pk,
                            "name": exercise.name,
                            "sets": exercise.sets,
                            "reps": exercise.reps,
                            "weight_kg": str(exercise.weight_kg),
                            "weight_unit": "kg",
                            "order": exercise.order,
                        }
                        for exercise in workout.exercises.all()
                    ],
                }
                for workout in workouts
            ],
            "exercise_results": [
                {
                    "id": result.pk,
                    "workout_id": result.workout_id,
                    "exercise_id": result.exercise_id,
                    "exercise_name": result.exercise_name,
                    "set_number": result.set_number,
                    "reps": result.reps,
                    "weight_kg": str(result.weight_kg),
                    "weight_unit": "kg",
                    "performed_at": timezone.localtime(result.performed_at).isoformat(),
                    "local_date": timezone.localtime(result.performed_at).date().isoformat(),
                }
                for result in results
            ],
            "workout_templates": [
                {
                    "id": template.pk,
                    "name": template.name,
                    "notes": template.notes,
                    "created_at": timezone.localtime(template.created_at).isoformat(),
                    "updated_at": timezone.localtime(template.updated_at).isoformat(),
                    "exercises": [
                        {
                            "id": exercise.pk,
                            "template_id": template.pk,
                            "name": exercise.name,
                            "sets": exercise.sets,
                            "reps": exercise.reps,
                            "weight_kg": str(exercise.weight_kg),
                            "weight_unit": "kg",
                            "order": exercise.order,
                        }
                        for exercise in template.exercises.all()
                    ],
                }
                for template in templates
            ],
            "daily_calories": [
                {
                    "id": item.pk,
                    "date": item.date.isoformat(),
                    "calories_burned": item.calories_burned,
                    "calorie_unit": "kcal",
                    "activity_note": item.activity_note,
                    "workout_id": item.workout_id,
                }
                for item in calories
            ],
        }
        response = HttpResponse(
            json.dumps(payload, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="pulseplan_user_{user.pk}_{stamp}.json"'
        )
        return response

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "type", "id", "parent_id", "date", "title", "status", "exercise",
            "set_number", "reps", "weight_kg", "calories", "notes", "username",
            "email", "first_name", "last_name",
        ]
    )
    writer.writerow(
        ["profile", user.pk, "", "", "", "", "", "", "", "", "", "",
         _safe_csv_cell(user.get_username()), _safe_csv_cell(user.email),
         _safe_csv_cell(user.first_name), _safe_csv_cell(user.last_name)]
    )
    for workout in workouts:
        workout_date = timezone.localtime(workout.scheduled_at)
        writer.writerow(
            ["workout", workout.pk, "", workout_date.isoformat(),
             _safe_csv_cell(workout.title), workout.get_status_display(), "", "",
             "", "", "", _safe_csv_cell(workout.notes), "", "", "", ""]
        )
        for exercise in workout.exercises.all():
            writer.writerow(
                ["exercise", exercise.pk, workout.pk, workout_date.isoformat(),
                 _safe_csv_cell(workout.title), workout.get_status_display(),
                 _safe_csv_cell(exercise.name), exercise.sets, exercise.reps,
                 exercise.weight_kg, "", "", "", "", "", ""]
            )
    for result in results:
        writer.writerow(
            ["set_result", result.pk, result.workout_id,
             timezone.localtime(result.performed_at).isoformat(),
             _safe_csv_cell(result.workout.title), result.workout.get_status_display(),
             _safe_csv_cell(result.exercise_name), result.set_number, result.reps,
             result.weight_kg, "", "", "", "", "", ""]
        )
    for item in calories:
        writer.writerow(
            ["calories", item.pk, item.workout_id or "", item.date.isoformat(),
             _safe_csv_cell(item.workout.title if item.workout else ""),
             "", _safe_csv_cell(item.activity_note), "", "", "",
             item.calories_burned, "", "", "", "", ""]
        )
    for template in templates:
        created_at = timezone.localtime(template.created_at).isoformat()
        writer.writerow(
            ["template", template.pk, "", created_at,
             _safe_csv_cell(template.name), "", "", "", "", "", "",
             _safe_csv_cell(template.notes), "", "", "", ""]
        )
        for exercise in template.exercises.all():
            writer.writerow(
                ["template_exercise", exercise.pk, template.pk, created_at,
                 _safe_csv_cell(template.name), "", _safe_csv_cell(exercise.name),
                 exercise.sets, exercise.reps, exercise.weight_kg, "", "", "", "", "", ""]
            )

    response = HttpResponse(
        "\ufeff" + output.getvalue(),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="pulseplan_user_{user.pk}_{stamp}.csv"'
    )
    return response


@login_required
@require_POST
def delete_user_data_view(request):
    form = SafeDataDeleteForm(user=request.user, data=request.POST)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return redirect("settings")

    user = request.user
    if (
        form.cleaned_data["scope"] == SafeDataDeleteForm.SCOPE_FULL_ACCOUNT
        and (user.is_staff or user.is_superuser)
    ):
        messages.error(
            request,
            "Удаление административного аккаунта через эту форму запрещено.",
        )
        return redirect("settings")

    has_misowned_results = (
        ExerciseResult.objects.filter(workout__user=user)
        .exclude(user=user)
        .exists()
        or ExerciseResult.objects.filter(user=user)
        .exclude(workout__user=user)
        .exists()
        or ExerciseResult.objects.filter(user=user, workout__user=user)
        .exclude(Q(exercise__isnull=True) | Q(exercise__workout__user=user))
        .exists()
    )
    has_misowned_calories = (
        DailyCalories.objects.filter(user=user)
        .exclude(Q(workout__isnull=True) | Q(workout__user=user))
        .exists()
    )
    if has_misowned_results or has_misowned_calories:
        messages.error(
            request,
            "Удаление остановлено: обнаружены связанные данные, которые не принадлежат "
            "этому аккаунту. Обратитесь к администратору.",
        )
        return redirect("settings")

    with transaction.atomic():
        ExerciseResult.objects.filter(user=user, workout__user=user).filter(
            Q(exercise__isnull=True) | Q(exercise__workout__user=user)
        ).delete()
        DailyCalories.objects.filter(user=user).filter(
            Q(workout__isnull=True) | Q(workout__user=user)
        ).delete()
        Workout.objects.filter(user=user).delete()
        WorkoutTemplate.objects.filter(user=user).delete()
        TemplateUseSubmission.objects.filter(user=user).delete()
        if form.cleaned_data["scope"] == SafeDataDeleteForm.SCOPE_FULL_ACCOUNT:
            user.delete()

    if form.cleaned_data["scope"] == SafeDataDeleteForm.SCOPE_FULL_ACCOUNT:
        logout(request)
        return redirect("login")

    messages.success(
        request,
        "Ваши тренировки, результаты подходов, шаблоны и записи калорий удалены.",
    )
    return redirect("settings")


@login_required
def export_data_view(request):
    if request.method == "POST":
        export_format = request.POST.get("format", "csv")
        include_workouts = request.POST.get("workouts") == "on"
        include_calories = request.POST.get("calories") == "on"
        include_results = request.POST.get("results") == "on"
        include_templates = request.POST.get("templates") == "on"
        date_from = request.POST.get("date_from")
        date_to = request.POST.get("date_to")
        try:
            date_from = date.fromisoformat(date_from) if date_from else None
            date_to = date.fromisoformat(date_to) if date_to else None
        except ValueError:
            messages.error(request, "Некорректный диапазон дат.")
            return settings_view(request)

        if date_from and date_to and date_from > date_to:
            messages.error(request, "Дата начала не может быть позже даты окончания.")
            return settings_view(request)

        rows = []
        if include_workouts:
            qs = Workout.objects.filter(user=request.user).prefetch_related("exercises")
            if date_from:
                qs = qs.filter(scheduled_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(scheduled_at__date__lte=date_to)
            for workout in qs:
                workout_date = timezone.localtime(workout.scheduled_at).date().isoformat()
                rows.append({
                    "type": "workout", "id": workout.pk, "parent_id": "",
                    "date": workout_date, "title": workout.title,
                    "status": workout.get_status_display(), "exercise": "",
                    "sets": "", "reps": "", "weight_kg": "", "weight_unit": "kg",
                    "calories": "", "calorie_unit": "kcal", "notes": workout.notes,
                })
                for ex in workout.exercises.all():
                    rows.append({
                        "type": "exercise", "id": ex.pk, "parent_id": workout.pk,
                        "date": workout_date, "title": workout.title,
                        "status": workout.get_status_display(), "exercise": ex.name,
                        "sets": ex.sets, "reps": ex.reps, "weight_kg": ex.weight_kg,
                        "weight_unit": "kg", "calories": "", "calorie_unit": "kcal",
                        "notes": "",
                    })
        if include_calories:
            qs = DailyCalories.objects.filter(user=request.user).filter(
                Q(workout__isnull=True) | Q(workout__user=request.user)
            )
            if date_from: qs = qs.filter(date__gte=date_from)
            if date_to: qs = qs.filter(date__lte=date_to)
            for item in qs.select_related("workout"):
                rows.append({
                    "type": "calories", "id": item.pk,
                    "parent_id": item.workout_id or "", "date": item.date.isoformat(),
                    "title": item.workout.title if item.workout else "", "status": "",
                    "exercise": item.activity_note, "sets": "", "reps": "",
                    "weight_kg": "", "weight_unit": "kg",
                    "calories": item.calories_burned, "calorie_unit": "kcal",
                    "notes": "",
                })
        if include_results:
            qs = ExerciseResult.objects.filter(
                user=request.user, workout__user=request.user
            ).filter(
                Q(exercise__isnull=True)
                | Q(exercise__workout__user=request.user)
            )
            if date_from: qs = qs.filter(performed_at__date__gte=date_from)
            if date_to: qs = qs.filter(performed_at__date__lte=date_to)
            for item in qs:
                rows.append({
                    "type": "set_result", "id": item.pk, "parent_id": item.workout_id,
                    "date": timezone.localtime(item.performed_at).date().isoformat(),
                    "title": item.workout.title, "status": item.workout.get_status_display(),
                    "exercise": item.exercise_name, "sets": item.set_number,
                    "reps": item.reps, "weight_kg": item.weight_kg,
                    "weight_unit": "kg", "calories": "", "calorie_unit": "kcal",
                    "notes": "",
                })

        if include_templates:
            qs = WorkoutTemplate.objects.filter(user=request.user).prefetch_related("exercises")
            if date_from:
                qs = qs.filter(created_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(created_at__date__lte=date_to)
            for template in qs:
                template_date = timezone.localtime(template.created_at).date().isoformat()
                rows.append({
                    "type": "template",
                    "id": template.pk,
                    "parent_id": "",
                    "date": template_date,
                    "title": template.name,
                    "status": "",
                    "exercise": "",
                    "sets": "",
                    "reps": "",
                    "weight_kg": "",
                    "weight_unit": "kg",
                    "calories": "",
                    "calorie_unit": "kcal",
                    "notes": template.notes,
                })
                for exercise in template.exercises.all():
                    rows.append({
                        "type": "template_exercise",
                        "id": exercise.pk,
                        "parent_id": template.pk,
                        "date": template_date,
                        "title": template.name,
                        "status": "",
                        "exercise": exercise.name,
                        "sets": exercise.sets,
                        "reps": exercise.reps,
                        "weight_kg": exercise.weight_kg,
                        "weight_unit": "kg",
                        "calories": "",
                        "calorie_unit": "kcal",
                        "notes": "",
                    })

        if not rows:
            messages.error(request, "За выбранный период нет данных для экспорта.")
            return settings_view(request)

        filename = f"pulseplan_export_{timezone.localdate().isoformat()}"
        if export_format == "json":
            response = JsonResponse(
                {
                    "schema_version": 1,
                    "timezone": str(timezone.get_current_timezone()),
                    "user_id": request.user.pk,
                    "records": rows,
                },
                json_dumps_params={"ensure_ascii": False, "indent": 2},
            )
            response["Content-Disposition"] = f'attachment; filename="{filename}.json"'
            return response

        import csv
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
        response.write("\ufeff")
        writer = csv.DictWriter(
            response,
            fieldnames=[
                "type", "id", "parent_id", "date", "title", "status", "exercise",
                "sets", "reps", "weight_kg", "weight_unit", "calories",
                "calorie_unit", "notes",
            ],
        )
        writer.writeheader()
        writer.writerows(
            {
                key: _safe_csv_cell(value) if isinstance(value, str) else value
                for key, value in row.items()
            }
            for row in rows
        )
        return response

    return settings_view(request)
