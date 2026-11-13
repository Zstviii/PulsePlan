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

    @property
    def total_calories(self) -> int:
        """Суммарный расход калорий, привязанных к данной тренировке."""
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

    def clean(self):
        super().clean()
        if self.workout_id and self.user_id and self.workout.user_id != self.user_id:
            raise ValidationError(
                {"workout": "Нельзя привязать калории к тренировке другого пользователя."}
            )

    def save(self, *args, **kwargs):
        if self.workout_id:
            self.date = timezone.localtime(self.workout.scheduled_at).date()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        source = self.workout.title if self.workout else (self.activity_note or "Дневная активность")
        return f"{self.date:%d.%m.%Y}: {self.calories_burned} ккал ({source})"
