from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from .models import User


class RegisterForm(UserCreationForm):
    first_name = forms.CharField(label=_("Nombres"), max_length=150)
    last_name = forms.CharField(label=_("Apellidos"), max_length=150)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "institution", "role", "preferred_language")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(_("Ya existe una cuenta con este correo."))
        return email


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label=_("Correo electrónico"), widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"})
    )

    def clean_username(self):
        return self.cleaned_data["username"].lower()


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "institution", "role", "preferred_language")
