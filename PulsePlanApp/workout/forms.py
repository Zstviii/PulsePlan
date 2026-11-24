from datetime import datetime, time
from django import forms
from django.utils import timezone

from .models import DailyCalories, Exercise, ExerciseResult, TemplateExercise, Workout, WorkoutTemplate


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
    date = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        error_messages={"invalid": "Некорректная дата."},
    )
    time = forms.TimeField(
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
        error_messages={"invalid": "Некорректное время."},
    )


class ExerciseResultForm(forms.ModelForm):
    class Meta:
        model = ExerciseResult
        fields = ["set_number", "reps", "weight_kg"]
        widgets = {
            "set_number": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "reps": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "weight_kg": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": "0.5"}),
        }


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
