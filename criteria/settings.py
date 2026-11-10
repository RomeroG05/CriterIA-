"""Configuración de CriterIA. Los valores del entorno se leen de `.env`."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DEBUG = _bool("DJANGO_DEBUG", True)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Define DJANGO_SECRET_KEY cuando DJANGO_DEBUG=false.")
    SECRET_KEY = "solo-desarrollo-no-usar-en-produccion"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "corpus",
    "chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "criteria.urls"
WSGI_APPLICATION = "criteria.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "chat:home"
LOGOUT_REDIRECT_URL = "chat:home"

LANGUAGE_CODE = "es"
LANGUAGES = [("es", "Español"), ("en", "English"), ("pt", "Português")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "America/Guayaquil"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "media/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Identidad del asistente ---
PRODUCT_NAME = "CriterIA"
ASSISTANT_NAME = "CriterIA"

# --- IA local ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen3.5:4b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
OLLAMA_TIMEOUT = 300

# --- Corpus y búsqueda ---
CORPUS_DIR = BASE_DIR / "corpus_pdfs"
CHUNK_WORDS = 180
CHUNK_OVERLAP = 40
RAG_TOP_K = 6
RAG_MIN_SIMILARITY = float(os.getenv("RAG_MIN_SIMILARITY", "0.50"))
RAG_KEEP_SIMILARITY = RAG_MIN_SIMILARITY - 0.08  # fragmentos más débiles no llegan al modelo
RAG_STRONG_SIMILARITY = RAG_MIN_SIMILARITY + 0.12  # evidencia que prevalece sobre el clasificador

# --- Gemini: solo da formato (imagen/audio) a respuestas ya fundamentadas en los PDF ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts")
GEMINI_DAILY_IMAGE_LIMIT = int(os.getenv("GEMINI_DAILY_IMAGE_LIMIT", "5"))
GEMINI_DAILY_AUDIO_LIMIT = int(os.getenv("GEMINI_DAILY_AUDIO_LIMIT", "20"))

# --- Voz local ---
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
