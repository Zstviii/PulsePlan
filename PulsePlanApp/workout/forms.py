from datetime import date, datetime, time
import uuid

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone

from .models import DailyCalories, Exercise, ExerciseResult, TemplateExercise, Workout, WorkoutTemplate


CALENDAR_MIN_DATE = date(1900, 1, 1)
CALENDAR_MAX_DATE = date(2100, 12, 31)


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(
        label="Электронная почта",
        required=False,
        help_text="Укажите адрес, чтобы восстанавливать доступ к аккаунту.",
    )

    class Meta(UserCreationForm.Meta):
        fields = ("username", "email")


class RecoveryEmailForm(forms.Form):
    email = forms.EmailField(label="Электронная почта", max_length=254)
    password = forms.CharField(label="Текущий пароль", widget=forms.PasswordInput(
        attrs={"autocomplete": "current-password"},
    ))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Неверный текущий пароль.")
        return password


class WorkoutForm(forms.ModelForm):
    """Форма создания и редактирования тренировки."""

    date = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"],
        label="Дата",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        validators=[
            MinValueValidator(CALENDAR_MIN_DATE),
            MaxValueValidator(CALENDAR_MAX_DATE),
        ],
        error_messages={
            "invalid": "Некорректный формат даты.",
            "min_value": "Дата должна быть не ранее 01.01.1900.",
            "max_value": "Дата должна быть не позднее 31.12.2100.",
        },
    )
    time = forms.TimeField(
        required=False,
        input_formats=["%H:%M", "%H:%M:%S"],
        label="Время",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
        error_messages={"invalid": "Некорректный формат времени (используйте ЧЧ:ММ)."},
    )

    class Meta:
        model = Workout
        fields = ["title", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
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


class DailyCaloriesForm(forms.ModelForm):
    """Форма добавления записи о потраченных калориях."""

    workout_id = forms.IntegerField(
        required=False, label="Связанная тренировка", widget=forms.Select,
        min_value=1, max_value=2**63 - 1,
        help_text="Для связанной записи используется дата тренировки.",
    )

    class Meta:
        model = DailyCalories
        fields = ["date", "calories_burned", "activity_note"]
        widgets = {"date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}
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

    def clean_date(self):
        target_date = self.cleaned_data.get("date")
        if (
            target_date
            and target_date > timezone.localdate()
            and not self.data.get("workout_id")
            and not self.instance.workout_id
        ):
            raise forms.ValidationError("Нельзя добавить запись калорий за будущую дату.")
        return target_date


class WorkoutTemplateForm(forms.ModelForm):
    class Meta:
        model = WorkoutTemplate
        fields = ["name", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Например: Силовая тренировка"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Необязательно"}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Укажите название шаблона.")
        return name


class TemplateExerciseForm(forms.ModelForm):
    class Meta:
        model = TemplateExercise
        fields = ["name", "sets", "reps", "weight_kg"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Жим лёжа"}),
            "sets": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "reps": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "weight_kg": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": "0.5"}),
        }


class TemplateUseForm(forms.Form):
    submission_token = forms.UUIDField(
        initial=uuid.uuid4,
        widget=forms.HiddenInput,
    )
    date = forms.DateField(
        label="Дата",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        validators=[
            MinValueValidator(CALENDAR_MIN_DATE),
            MaxValueValidator(CALENDAR_MAX_DATE),
        ],
        error_messages={
            "invalid": "Некорректная дата.",
            "min_value": "Дата должна быть не ранее 01.01.1900.",
            "max_value": "Дата должна быть не позднее 31.12.2100.",
        },
    )
    time = forms.TimeField(
        label="Время",
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
        error_messages={"invalid": "Некорректное время."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and self.errors:
            for name in ("date", "time"):
                if name in self.errors:
                    self.fields[name].widget.attrs.update(
                        {
                            "aria-invalid": "true",
                            "aria-describedby": f"{name}-error",
                        }
                    )


class ExerciseResultForm(forms.ModelForm):
    class Meta:
        model = ExerciseResult
        fields = ["set_number", "reps", "weight_kg"]
        widgets = {
            "set_number": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 100, "step": 1}
            ),
            "reps": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 1000, "step": 1}
            ),
            "weight_kg": forms.NumberInput(
                attrs={"class": "form-control", "min": 0, "max": "999.9", "step": "0.1"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        first_invalid = True
        if self.is_bound and self.errors:
            for name, field in self.fields.items():
                if name in self.errors:
                    field.widget.attrs["class"] += " is-invalid"
                    field.widget.attrs.update(
                        {
                            "aria-invalid": "true",
                            "aria-describedby": f"id_{name}-error",
                        }
                    )
                    if first_invalid:
                        field.widget.attrs["autofocus"] = True
                        first_invalid = False


class SafeDataDeleteForm(forms.Form):
    SCOPE_DATA_ONLY = "data_only"
    SCOPE_FULL_ACCOUNT = "full_account"
    CONFIRMATION_PHRASE = "УДАЛИТЬ"
    SCOPE_CHOICES = [
        (SCOPE_DATA_ONLY, "Удалить тренировки, подходы, шаблоны и калории"),
        (SCOPE_FULL_ACCOUNT, "Удалить аккаунт и все связанные данные"),
    ]

    scope = forms.ChoiceField(choices=SCOPE_CHOICES, initial=SCOPE_DATA_ONLY)
    password = forms.CharField(
        widget=forms.PasswordInput,
        error_messages={"required": "Введите текущий пароль для подтверждения."},
    )
    backup_confirmed = forms.BooleanField(
        required=True,
        error_messages={
            "required": "Перед удалением подтвердите, что экспорт уже сохранён."
        },
    )
    confirmation = forms.CharField(
        error_messages={"required": "Введите слово подтверждения УДАЛИТЬ."},
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data.get("password") or ""
        if not self.user.check_password(password):
            raise forms.ValidationError("Неверный текущий пароль.")
        return password

    def clean_confirmation(self):
        confirmation = (self.cleaned_data.get("confirmation") or "").strip()
        if confirmation != self.CONFIRMATION_PHRASE:
            raise forms.ValidationError(
                f"Для подтверждения введите точное слово: {self.CONFIRMATION_PHRASE}."
            )
        return confirmation
