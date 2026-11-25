from django import template

register = template.Library()


@register.filter
def exercise_count(value):
    count = int(value)
    noun = _russian_noun(count, "упражнение", "упражнения", "упражнений")
    return f"{count} {noun}"


@register.filter
def workout_count(value):
    count = int(value)
    noun = _russian_noun(count, "тренировка", "тренировки", "тренировок")
    return f"{count} {noun}"


def _russian_noun(count, one, few, many):
    remainder = count % 100
    if 11 <= remainder <= 14:
        return many
    elif count % 10 == 1:
        return one
    elif 2 <= count % 10 <= 4:
        return few
    return many