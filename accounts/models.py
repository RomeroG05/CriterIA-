from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("El correo es obligatorio.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self._create(email, password, **extra)


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = "student", _("Estudiante")
        JOURNALIST = "journalist", _("Periodista")
        TEACHER = "teacher", _("Docente")
        RESEARCHER = "researcher", _("Investigador/a")
        OTHER = "other", _("Otro")

    username = None
    email = models.EmailField(_("Correo electrónico"), unique=True)
    institution = models.CharField(_("Institución"), max_length=200, blank=True)
    role = models.CharField(_("Perfil"), max_length=20, choices=Role.choices, default=Role.STUDENT)
    preferred_language = models.CharField(_("Idioma"), max_length=2, choices=settings.LANGUAGES, default="es")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    @property
    def display_name(self) -> str:
        return self.get_full_name() or self.email.split("@")[0]

    @property
    def initials(self) -> str:
        parts = self.get_full_name().split() or [self.email]
        return "".join(p[0] for p in parts[:2]).upper()
