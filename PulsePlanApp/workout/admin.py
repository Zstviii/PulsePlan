from django.contrib import admin
from .models import DailyCalories, Exercise, ExerciseResult, TemplateExercise, Workout, WorkoutTemplate

admin.site.register([Workout, Exercise, DailyCalories, WorkoutTemplate, TemplateExercise, ExerciseResult])
