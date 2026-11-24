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
        "workout/<int:workout_id>/save-template/",
        views.workout_save_as_template_view,
        name="workout_save_as_template",
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
    path(
        "exercise/<int:exercise_id>/sets/add/",
        views.set_result_create_view,
        name="set_result_create",
    ),
    path(
        "sets/<int:set_result_id>/delete/",
        views.set_result_delete_view,
        name="set_result_delete",
    ),
    path("history/", views.exercise_history_view, name="exercise_history"),
    path("templates/", views.templates_list_view, name="templates_list"),
    path("templates/add/", views.template_create_view, name="template_create"),
    path(
        "templates/<int:template_id>/delete/",
        views.template_delete_view,
        name="template_delete",
    ),
    path(
        "templates/<int:template_id>/exercises/add/",
        views.template_exercise_create_view,
        name="template_exercise_create",
    ),
    path(
        "templates/exercises/<int:template_exercise_id>/delete/",
        views.template_exercise_delete_view,
        name="template_exercise_delete",
    ),
    path(
        "templates/<int:template_id>/apply/",
        views.template_apply_view,
        name="template_apply",
    ),
    path("calories/add/", views.calorie_create_view, name="calorie_create"),
    path(
        "calories/<int:calorie_id>/delete/",
        views.calorie_delete_view,
        name="calorie_delete",
    ),
    path("account/data/", views.account_data_view, name="account_data"),
    path("account/export/", views.export_user_data_view, name="export_user_data"),
    path("account/delete-data/", views.delete_user_data_view, name="delete_user_data"),
]