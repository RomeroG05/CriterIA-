from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _

from .forms import LoginForm, ProfileForm, RegisterForm


def _apply_language(response, user):
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, user.preferred_language, max_age=31536000, samesite="Lax")
    return response


class EmailLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        return _apply_language(super().form_valid(form), form.get_user())


def register(request):
    if request.user.is_authenticated:
        return redirect("chat:home")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, _("Tu cuenta fue creada. Tus conversaciones ahora se guardarán en tu historial."))
        return _apply_language(redirect("chat:home"), user)
    return render(request, "accounts/register.html", {"form": form})


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, _("Perfil actualizado."))
        return _apply_language(redirect("accounts:profile"), user)
    return render(request, "accounts/profile.html", {"form": form})


class PasswordChange(PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, _("Contraseña actualizada."))
        return super().form_valid(form)
