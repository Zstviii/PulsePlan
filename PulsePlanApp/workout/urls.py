from django.urls import path
from . import views

urlpatterns = [
    path("register/", views.register_view, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.dashboard_view, name="dashboard"),
    path("workout/add/", views.workout_create_view, name="workout_create"),
    path(
        "workout/<int:workout_id>/edit/",
        views.workout_update_view,
        name="workout_update",
    ),
    path(
        "workout/<int:workout_id>/delete/",
        views.workout_delete_view,
        name="workout_delete",
    ),
    path(
        "workout/<int:workout_id>/toggle/",
        views.workout_toggle_status_view,
        name="workout_toggle_status",
    ),
    path(
        "workout/<int:workout_id>/exercises/add/",
        views.exercise_create_view,
        name="exercise_create",
    ),
    path(
        "exercise/<int:exercise_id>/delete/",
        views.exercise_delete_view,
        name="exercise_delete",
    ),
    path("calories/add/", views.calorie_create_view, name="calorie_create"),
    path(
        "calories/<int:calorie_id>/delete/",
        views.calorie_delete_view,
        name="calorie_delete",
    ),
]
