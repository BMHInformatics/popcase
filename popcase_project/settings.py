import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load key=value pairs from a .env file without requiring extra packages."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default=None) -> list[str]:
    value = os.environ.get(name)
    if value is None:
        return default or []
    return [item.strip() for item in value.split(",") if item.strip()]


def env_int(name: str, default: int = 0) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # "django.contrib.gis",
    "popcase",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "popcase_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "popcase_project.wsgi.application"

DATABASES = {
    "popcase_manual_etl": {
        "ENGINE": os.environ.get("DB_MANUAL_ENGINE", "django.db.backends.postgresql"),
        "NAME": os.environ["DB_MANUAL_NAME"],
        "USER": os.environ["DB_MANUAL_USER"],
        "PASSWORD": os.environ["DB_MANUAL_PASSWORD"],
        "HOST": os.environ["DB_MANUAL_HOST"],
        "PORT": os.environ.get("DB_MANUAL_PORT", "5432"),
    },
    "default": {
        "ENGINE": os.environ.get("DB_DEFAULT_ENGINE", "django.db.backends.postgresql"),
        "NAME": os.environ["DB_DEFAULT_NAME"],
        "USER": os.environ["DB_DEFAULT_USER"],
        "PASSWORD": os.environ["DB_DEFAULT_PASSWORD"],
        "HOST": os.environ["DB_DEFAULT_HOST"],
        "PORT": os.environ.get("DB_DEFAULT_PORT", "5432"),
        "OPTIONS": {
            "options": os.environ.get("DB_DEFAULT_OPTIONS", "-c search_path=public"),
        },
    },
}

manual_options = os.environ.get("DB_MANUAL_OPTIONS", "")
if manual_options:
    DATABASES["popcase_manual_etl"]["OPTIONS"] = {"options": manual_options}

DATABASE_ROUTERS = ["popcase_project.db_router.PopcaseRouter"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = os.environ.get("DJANGO_LANGUAGE_CODE", "en-us")
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = os.environ.get("DJANGO_STATIC_URL", "static/")
STATICFILES_DIRS = []
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "popcase:login"
LOGIN_REDIRECT_URL = "popcase:wizard"
LOGOUT_REDIRECT_URL = "popcase:login"

SESSION_ENGINE = os.environ.get(
    "DJANGO_SESSION_ENGINE",
    "django.contrib.sessions.backends.signed_cookies",
)

# Security settings. Keep strict values in production and relax only in local/dev .env files.
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = env_bool("DJANGO_SECURE_CONTENT_TYPE_NOSNIFF", True)
X_FRAME_OPTIONS = os.environ.get("DJANGO_X_FRAME_OPTIONS", "DENY")
