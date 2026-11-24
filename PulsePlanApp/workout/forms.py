from datetime import datetime, time
from decimal import Decimal
from django import forms
from django.utils import timezone

from .models import (
    DailyCalories,
    Exercise,
    ExerciseSetResult,
    TemplateExercise,
    Workout,
    WorkoutTemplate,
)


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
        fields = ["title", "notes"]
        error_messages = {
            "title": {
                "required": "Укажите название тренировки.",
                "max_length": "Название тренировки не должно превышать 120 символов.",
            }
        }

    def clean_title(self) -> str:
        title = (self.cleaned_data.get("title") or "").strip()
        if not title:
            raise forms.ValidationError("Укажите название тренировки.")
        return title

    def save(self, user=None, commit: bool = True) -> Workout:
        workout = super().save(commit=False)
        if user is not None:
            workout.user = user

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
    """Форма добавления или редактирования упражнения."""

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


class ExerciseSetResultForm(forms.ModelForm):
    """Форма фиксации результата одного подхода."""

    class Meta:
        model = ExerciseSetResult
        fields = ["set_number", "reps", "weight_kg", "notes"]
        error_messages = {
            "set_number": {
                "invalid": "Номер подхода должен быть целым числом.",
                "min_value": "Номер подхода должен быть не меньше 1.",
                "max_value": "Максимум 100 подходов.",
            },
            "reps": {
                "required": "Укажите количество выполненных повторений.",
                "invalid": "Повторения должны быть целым числом.",
                "min_value": "Минимум 1 повторение.",
                "max_value": "Максимум 1000 повторений.",
            },
            "weight_kg": {
                "invalid": "Вес должен быть числом (например, 20.5).",
                "min_value": "Вес не может быть отрицательным.",
                "max_value": "Слишком большое значение веса.",
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["set_number"].required = False
        self.fields["weight_kg"].required = False
        self.fields["notes"].required = False

    def clean_weight_kg(self) -> Decimal:
        val = self.cleaned_data.get("weight_kg")
        return val if val is not None else Decimal("0.0")

    def clean_notes(self) -> str:
        return (self.cleaned_data.get("notes") or "").strip()


class WorkoutTemplateForm(forms.ModelForm):
    """Форма создания шаблона тренировки."""

    class Meta:
        model = WorkoutTemplate
        fields = ["title", "notes"]
        error_messages = {
            "title": {
                "required": "Укажите название шаблона.",
                "max_length": "Название шаблона не должно превышать 120 символов.",
            }
        }

    def clean_title(self) -> str:
        title = (self.cleaned_data.get("title") or "").strip()
        if not title:
            raise forms.ValidationError("Укажите название шаблона.")
        return title


class TemplateExerciseForm(forms.ModelForm):
    """Форма добавления упражнения в шаблон тренировки."""

    class Meta:
        model = TemplateExercise
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
                "invalid": "Вес должен быть числом.",
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

    def clean_weight_kg(self) -> Decimal:
        val = self.cleaned_data.get("weight_kg")
        return val if val is not None else Decimal("0.0")


class ApplyTemplateForm(forms.Form):
    """Форма создания тренировки из шаблона на выбранную дату и время."""

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

    def clean(self):
        cleaned_data = super().clean()
        target_date = cleaned_data.get("date")
        if target_date and target_date > timezone.localdate():
            raise forms.ValidationError("Нельзя добавить запись калорий за будущую дату.")
        return cleaned_data


class SafeDataDeleteForm(forms.Form):
    """Форма безопасного удаления данных с проверкой пароля и фразы подтверждения."""

    SCOPE_DATA_ONLY = "data_only"
    SCOPE_FULL_ACCOUNT = "full_account"
    SCOPE_CHOICES = [
        (SCOPE_DATA_ONLY, "Удалить все тренировки, подходы, шаблоны и калории (оставить аккаунт)"),
        (SCOPE_FULL_ACCOUNT, "Удалить аккаунт и все связанные данные безвозвратно"),
    ]
    CONFIRMATION_PHRASE = "УДАЛИТЬ"

    scope = forms.ChoiceField(
        choices=SCOPE_CHOICES,
        initial=SCOPE_DATA_ONLY,
        error_messages={"invalid_choice": "Выберите корректный режим удаления."},
    )
    password = forms.CharField(
        widget=forms.PasswordInput,
        error_messages={"required": "Введите текущий пароль для подтверждения."},
    )
    confirmation = forms.CharField(
        error_messages={"required": "Введите слово подтверждения УДАЛИТЬ."},
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self) -> str:
        password = self.cleaned_data.get("password") or ""
        if not self.user.check_password(password):
            raise forms.ValidationError("Неверный текущий пароль.")
        return password

    def clean_confirmation(self) -> str:
        confirmation = (self.cleaned_data.get("confirmation") or "").strip()
        if confirmation != self.CONFIRMATION_PHRASE:
            raise forms.ValidationError(
                f"Для подтверждения введите точное слово: {self.CONFIRMATION_PHRASE}."
            )
        return confirmation