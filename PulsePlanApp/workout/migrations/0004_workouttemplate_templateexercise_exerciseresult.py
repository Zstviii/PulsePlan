from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.core.validators import MaxValueValidator, MinValueValidator
import django.utils.timezone
from decimal import Decimal


class Migration(migrations.Migration):
    dependencies = [("workout", "0003_dailycalories_unique_calories_entry_per_workout")]

    operations = [
        migrations.CreateModel(
            name="WorkoutTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, verbose_name="Название шаблона")),
                ("notes", models.TextField(blank=True, verbose_name="Заметки")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Изменено")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="workout_templates", to=settings.AUTH_USER_MODEL, verbose_name="Пользователь")),
            ],
            options={"ordering": ["name", "id"], "verbose_name": "Шаблон тренировки", "verbose_name_plural": "Шаблоны тренировок"},
        ),
        migrations.CreateModel(
            name="TemplateExercise",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, verbose_name="Название упражнения")),
                ("sets", models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(100)], verbose_name="Подходы")),
                ("reps", models.PositiveSmallIntegerField(default=10, validators=[MinValueValidator(1), MaxValueValidator(1000)], verbose_name="Повторения")),
                ("weight_kg", models.DecimalField(decimal_places=1, default=Decimal("0.0"), max_digits=5, validators=[MinValueValidator(Decimal("0.0")), MaxValueValidator(Decimal("999.9"))], verbose_name="Вес (кг)")),
                ("order", models.PositiveSmallIntegerField(default=1, verbose_name="Порядок")),
                ("template", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exercises", to="workout.workouttemplate", verbose_name="Шаблон")),
            ],
            options={"ordering": ["order", "id"], "verbose_name": "Упражнение шаблона", "verbose_name_plural": "Упражнения шаблона"},
        ),
        migrations.CreateModel(
            name="ExerciseResult",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("exercise_name", models.CharField(max_length=120, verbose_name="Упражнение")),
                ("set_number", models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(100)], verbose_name="Номер подхода")),
                ("reps", models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(1000)], verbose_name="Повторения")),
                ("weight_kg", models.DecimalField(decimal_places=1, default=Decimal("0.0"), max_digits=5, validators=[MinValueValidator(Decimal("0.0")), MaxValueValidator(Decimal("999.9"))], verbose_name="Фактический вес (кг)")),
                ("performed_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now, verbose_name="Выполнено")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("exercise", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="results", to="workout.exercise")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exercise_results", to=settings.AUTH_USER_MODEL)),
                ("workout", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exercise_results", to="workout.workout")),
            ],
            options={"ordering": ["-performed_at", "set_number"], "verbose_name": "Результат подхода", "verbose_name_plural": "Результаты подходов"},
        ),
    ]
