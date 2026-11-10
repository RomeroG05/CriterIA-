from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("registro/", views.register, name="register"),
    path("ingresar/", views.EmailLoginView.as_view(), name="login"),
    path("salir/", LogoutView.as_view(), name="logout"),
    path("perfil/", views.profile, name="profile"),
    path("clave/", views.PasswordChange.as_view(), name="password_change"),
]
