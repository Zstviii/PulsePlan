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
    path(
        "exercise/<int:exercise_id>/edit/",
        views.exercise_update_view,
        name="exercise_update",
    ),
    path("templates/", views.template_list_view, name="template_list"),
    path("templates/add/", views.template_create_view, name="template_create"),
    path("templates/<int:template_id>/edit/", views.template_edit_view, name="template_edit"),
    path("templates/<int:template_id>/use/", views.template_use_view, name="template_use"),
    path("templates/<int:template_id>/delete/", views.template_delete_view, name="template_delete"),
    path("templates/<int:template_id>/exercises/add/", views.template_exercise_add_view, name="template_exercise_add"),
    path("template-exercise/<int:exercise_id>/delete/", views.template_exercise_delete_view, name="template_exercise_delete"),
    path("exercise/<int:exercise_id>/history/", views.exercise_history_view, name="exercise_history"),
    path("exercise/<int:exercise_id>/history/add/", views.exercise_result_add_view, name="exercise_result_add"),
    path("export/", views.export_data_view, name="export_data"),
    path("calories/add/", views.calorie_create_view, name="calorie_create"),
    path(
        "calories/<int:calorie_id>/delete/",
        views.calorie_delete_view,
        name="calorie_delete",
    ),
]
