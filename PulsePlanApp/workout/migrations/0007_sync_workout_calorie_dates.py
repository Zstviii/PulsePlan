from django.db import migrations
from django.utils import timezone


def sync_workout_calorie_dates(apps, schema_editor):
    DailyCalories = apps.get_model("workout", "DailyCalories")
    entries = DailyCalories.objects.using(schema_editor.connection.alias)
    linked = entries.filter(workout__isnull=False).select_related("workout")
    for entry in linked.iterator(chunk_size=500):
        workout_date = timezone.localtime(entry.workout.scheduled_at).date()
        if entry.date != workout_date:
            entries.filter(pk=entry.pk).update(date=workout_date)


class Migration(migrations.Migration):
    dependencies = [("workout", "0006_create_submission")]
    operations = [migrations.RunPython(sync_workout_calorie_dates, migrations.RunPython.noop)]
