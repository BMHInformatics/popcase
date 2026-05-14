from django.urls import path
from . import views

app_name = "popcase"

urlpatterns = [
    path("", views.home, name="home"),
    # path("login/", views.PopcaseLoginView.as_view(), name="login"),
    # path("logout/", views.PopcaseLogoutView.as_view(), name="logout"),

    path("wizard/", views.wizard_step, {"step": "geographic-level"}, name="wizard"),
    path("wizard/<slug:step>/", views.wizard_step, name="wizard_step"),

    path("results/", views.results, name="results"),
    path("reset/", views.reset_wizard, name="reset"),
    path("results/export/", views.export_geo_dataset_csv, name="export_geo_dataset_csv")]
