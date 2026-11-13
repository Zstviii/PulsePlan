from datetime import datetime, time
from django import forms
from django.utils import timezone

from .models import DailyCalories, Exercise, Workout


class WorkoutForm(forms.ModelForm):
    """Форма создания и редактирования тренировки."""

    date = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"],
        error_messages={"invalid": "Некорректный формат даты."},
    )
    time = forms.TimeField(
        required=False,
        input_formats=["%H:%M", "%H:%M:%S"],
        error_messages={"invalid": "Некорректный формат времени (используйте ЧЧ:ММ)."},
    )

    class Meta:
        model = Workout
        fields = ["title", "status", "notes"]
        error_messages = {
            "title": {
                "required": "Укажите название тренировки.",
                "max_length": "Название тренировки не должно превышать 120 символов.",
            }
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # При создании из формы шаблон может не передавать status — ставим planned по умолчанию
        self.fields["status"].required = False

    def clean_title(self) -> str:
        title = (self.cleaned_data.get("title") or "").strip()
        if not title:
            raise forms.ValidationError("Укажите название тренировки.")
        return title

    def clean_status(self) -> str:
        status = self.cleaned_data.get("status")
        if not status:
            return self.instance.status if self.instance.pk else Workout.Status.PLANNED
        return status

    def save(self, user=None, commit: bool = True) -> Workout:
        workout = super().save(commit=False)
        if user is not None:
            workout.user = user

        # Определяем дату и время с опорой на текущее значение объекта или дефолт
        existing_local = (
            timezone.localtime(workout.scheduled_at)
            if workout.pk and workout.scheduled_at
            else None
        )
        target_date = self.cleaned_data.get("date") or (
            existing_local.date() if existing_local else timezone.localdate()
        )
        target_time = self.cleaned_data.get("time") or (
            existing_local.time().replace(second=0, microsecond=0)
            if existing_local
            else time(18, 0)
        )
        workout.scheduled_at = timezone.make_aware(datetime.combine(target_date, target_time))

        if commit:
            workout.save()
        return workout


class ExerciseForm(forms.ModelForm):
    """Форма добавления упражнения к тренировке."""

    class Meta:
        model = Exercise
        fields = ["name", "sets", "reps", "weight_kg"]
        error_messages = {
            "name": {"required": "Укажите название упражнения."},
            "sets": {
                "invalid": "Количество подходов должно быть целым числом.",
                "min_value": "Минимум 1 подход.",
                "max_value": "Максимум 100 подходов.",
            },
            "reps": {
                "invalid": "Количество повторений должно быть целым числом.",
                "min_value": "Минимум 1 повторение.",
                "max_value": "Максимум 1000 повторений.",
            },
            "weight_kg": {
                "invalid": "Вес должен быть числом (например, 12.5).",
                "min_value": "Вес не может быть отрицательным.",
                "max_value": "Слишком большое значение веса.",
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sets"].required = False
        self.fields["reps"].required = False
        self.fields["weight_kg"].required = False

    def clean_name(self) -> str:
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Укажите название упражнения.")
        return name

    def clean_sets(self) -> int:
        val = self.cleaned_data.get("sets")
        return val if val is not None else 3

    def clean_reps(self) -> int:
        val = self.cleaned_data.get("reps")
        return val if val is not None else 10

    def clean_weight_kg(self):
        val = self.cleaned_data.get("weight_kg")
        return val if val is not None else 0


class DailyCaloriesForm(forms.ModelForm):
    """Форма добавления записи о потраченных калориях."""

    workout_id = forms.IntegerField(required=False)

    class Meta:
        model = DailyCalories
        fields = ["date", "calories_burned", "activity_note"]
        error_messages = {
            "calories_burned": {
                "required": "Укажите количество потраченных калорий.",
                "invalid": "Количество калорий должно быть целым числом.",
                "min_value": "Количество калорий должно быть больше 0.",
                "max_value": "Значение не может превышать 20 000 ккал.",
            },
            "date": {"invalid": "Некорректная дата."},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].required = False
        self.fields["activity_note"].required = False
