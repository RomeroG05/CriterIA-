from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class AccountAdmin(UserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "role", "institution", "date_joined", "is_staff")
    search_fields = ("email", "first_name", "last_name", "institution")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Datos", {"fields": ("first_name", "last_name", "institution", "role", "preferred_language")}),
        ("Permisos", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Fechas", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
