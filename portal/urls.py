from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    # registration
    path("register/", views.register, name="register"),
    path("register/review/", views.register_review, name="register_review"),
    path("register/confirm/", views.register_confirm, name="register_confirm"),
    path("register/done/", views.register_done, name="register_done"),
    # edit / recover
    path("team/edit/login/", views.team_edit_login, name="team_edit_login"),
    path("team/edit/", views.team_edit, name="team_edit"),
    path("team/edit/logout/", views.team_edit_logout, name="team_edit_logout"),
    path("team/find/", views.team_find, name="team_find"),
    # report submission
    path("submit/", views.submit_lookup, name="submit_lookup"),
    path("submit/upload/", views.submit_upload, name="submit_upload"),
    path("submit/done/", views.submit_done, name="submit_done"),
    path("submit/leave/", views.submit_leave, name="submit_leave"),
    # staff only
    path("files/<int:pk>/", views.download_upload, name="download_upload"),
]
