from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Workout(models.Model):
    """Тренировочная сессия пользователя в календаре."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Запланирована"
        COMPLETED = "completed", "Выполнена"
        SKIPPED = "skipped", "Пропущена"

    ALLOWED_STATUS_TRANSITIONS = {
        Status.PLANNED: {Status.COMPLETED, Status.SKIPPED},
        Status.COMPLETED: {Status.PLANNED, Status.SKIPPED},
        Status.SKIPPED: {Status.PLANNED},
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workouts",
        verbose_name="Пользователь",
    )
    title = models.CharField("Название тренировки", max_length=120)
    scheduled_at = models.DateTimeField("Дата и время", default=timezone.now, db_index=True)
    status = models.CharField(
        "Статус выполнения",
        max_length=16,
        choices=Status.choices,
        default=Status.PLANNED,
    )
    notes = models.TextField("Заметки для новичка", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Тренировка"
        verbose_name_plural = "Тренировки"
        ordering = ["scheduled_at"]

    def __str__(self) -> str:
        local_dt = timezone.localtime(self.scheduled_at)
        return f"{self.title} ({local_dt:%d.%m.%Y %H:%M})"

    @classmethod
    def can_transition(cls, current_status: str, new_status: str) -> bool:
        return (
            current_status == new_status
            or new_status in cls.ALLOWED_STATUS_TRANSITIONS.get(current_status, set())
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.pk:
            current_status = type(self).objects.filter(pk=self.pk).values_list(
                "status", flat=True
            ).first()
            if current_status is not None and not self.can_transition(
                current_status, self.status
            ):
                raise ValidationError(
                    {"status": f"Недопустимый переход статуса: {current_status} → {self.status}."}
                )
        super().save(*args, **kwargs)

    @property
    def total_calories(self) -> int:
        """Суммарный расход калорий, привязанных к данной тренировке."""
        if self.status != self.Status.COMPLETED:
            return 0
        result = self.calorie_entries.aggregate(total=Sum("calories_burned"))["total"]
        return result or 0


class Exercise(models.Model):
    """Упражнение внутри конкретной тренировки."""

    workout = models.ForeignKey(
        Workout,
        on_delete=models.CASCADE,
        related_name="exercises",
        verbose_name="Тренировка",
    )
    name = models.CharField("Название упражнения", max_length=120)
    sets = models.PositiveSmallIntegerField(
        "Подходы",
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    reps = models.PositiveSmallIntegerField(
        "Повторения",
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
    )
    weight_kg = models.DecimalField(
        "Вес (кг)",
        max_digits=5,
        decimal_places=1,
        default=Decimal("0.0"),
        validators=[MinValueValidator(Decimal("0.0")), MaxValueValidator(Decimal("999.9"))],
        help_text="Укажите 0, если упражнение выполняется с собственным весом",
    )
    order = models.PositiveSmallIntegerField("Порядок", default=1)

    class Meta:
        verbose_name = "Упражнение"
        verbose_name_plural = "Упражнения"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        weight_label = f"{self.weight_kg} кг" if self.weight_kg > 0 else "свой вес"
        return f"{self.name}: {self.sets}×{self.reps} ({weight_label})"

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ExerciseSetResult(models.Model):
    """Фактический результат выполненного подхода в упражнении."""

    exercise = models.ForeignKey(
        Exercise,
        on_delete=models.CASCADE,
        related_name="set_results",
        verbose_name="Упражнение",
    )
    set_number = models.PositiveSmallIntegerField(
        "Номер подхода",
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    reps = models.PositiveSmallIntegerField(
        "Фактические повторения",
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
    )
    weight_kg = models.DecimalField(
        "Фактический вес (кг)",
        max_digits=5,
        decimal_places=1,
        default=Decimal("0.0"),
        validators=[MinValueValidator(Decimal("0.0")), MaxValueValidator(Decimal("999.9"))],
    )
    notes = models.CharField("Комментарий", max_length=150, blank=True)
    recorded_at = models.DateTimeField("Время записи", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "Результат подхода"
        verbose_name_plural = "Результаты подходов"
        ordering = ["set_number", "recorded_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["exercise", "set_number"],
                name="unique_set_number_per_exercise",
            )
        ]

    def __str__(self) -> str:
        weight_label = f"{self.weight_kg} кг" if self.weight_kg > 0 else "свой вес"
        return f"{self.exercise.name} — подход {self.set_number}: {self.reps} повт. ({weight_label})"

    @property
    def volume(self) -> Decimal:
        return self.weight_kg * self.reps

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class WorkoutTemplate(models.Model):
    """Пользовательский шаблон тренировки для быстрого планирования."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workout_templates",
        verbose_name="Пользователь",
    )
    title = models.CharField("Название шаблона", max_length=120)
    notes = models.TextField("Заметки", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Шаблон тренировки"
        verbose_name_plural = "Шаблоны тренировок"
        ordering = ["-created_at", "id"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class TemplateExercise(models.Model):
    """Упражнение внутри шаблона тренировки."""

    template = models.ForeignKey(
        WorkoutTemplate,
        on_delete=models.CASCADE,
        related_name="exercises",
        verbose_name="Шаблон тренировки",
    )
    name = models.CharField("Название упражнения", max_length=120)
    sets = models.PositiveSmallIntegerField(
        "Подходы",
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    reps = models.PositiveSmallIntegerField(
        "Повторения",
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
    )
    weight_kg = models.DecimalField(
        "Вес (кг)",
        max_digits=5,
        decimal_places=1,
        default=Decimal("0.0"),
        validators=[MinValueValidator(Decimal("0.0")), MaxValueValidator(Decimal("999.9"))],
    )
    order = models.PositiveSmallIntegerField("Порядок", default=1)

    class Meta:
        verbose_name = "Упражнение шаблона"
        verbose_name_plural = "Упражнения шаблонов"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"{self.name}: {self.sets}×{self.reps} ({self.weight_kg} кг)"

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class DailyCalories(models.Model):
    """Запись о потраченных калориях (по тренировке или общей активности за день)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calorie_logs",
        verbose_name="Пользователь",
    )
    workout = models.ForeignKey(
        Workout,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calorie_entries",
        verbose_name="Связанная тренировка",
    )
    date = models.DateField("Дата учёта", default=timezone.localdate, db_index=True)
    calories_burned = models.PositiveIntegerField(
        "Потрачено ккал",
        validators=[MinValueValidator(1), MaxValueValidator(20000)],
    )
    activity_note = models.CharField(
        "Описание активности",
        max_length=150,
        blank=True,
        help_text="Например: Силовая сессия, ходьба 8 000 шагов, утренняя разминка",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Запись калорий"
        verbose_name_plural = "Записи калорий"
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workout"],
                condition=models.Q(workout__isnull=False),
                name="unique_calories_entry_per_workout",
            )
        ]

    def clean(self):
        super().clean()
        if self.workout_id and self.user_id and self.workout.user_id != self.user_id:
            raise ValidationError(
                {"workout": "Нельзя привязать калории к тренировке другого пользователя."}
            )
        if self.workout_id and self.workout.status != Workout.Status.COMPLETED:
            raise ValidationError(
                {"workout": "Калории можно привязать только к завершённой тренировке."}
            )
        if self.date and self.date > timezone.localdate():
            raise ValidationError({"date": "Нельзя добавить запись калорий за будущую дату."})

    def save(self, *args, **kwargs):
        if self.workout_id:
            self.date = timezone.localtime(self.workout.scheduled_at).date()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        source = self.workout.title if self.workout else (self.activity_note or "Дневная активность")
        return f"{self.date:%d.%m.%Y}: {self.calories_burned} ккал ({source})"