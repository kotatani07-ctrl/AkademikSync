import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-change-me-in-production-please')
DEBUG = os.getenv('DEBUG', 'True') == 'True'

# Allow all hosts in development; restrict in production via ALLOWED_HOSTS env var
if DEBUG:
    ALLOWED_HOSTS = ['*']
else:
    ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Trust all origins in debug (Replit proxy / .replit.dev domains)
CSRF_TRUSTED_ORIGINS = os.getenv(
    'CSRF_TRUSTED_ORIGINS',
    'https://*.replit.dev,https://*.replit.app,http://localhost:*'
).split(',')

# ── Cookie / CSRF settings untuk Replit proxy ───────────────────────
# Replit proxies HTTPS → HTTP ke Django. Cookie CSRF sering diblok
# browser karena SameSite / Secure mismatch di balik proxy.
# Solusi: simpan token CSRF di session (bukan cookie terpisah).
CSRF_USE_SESSIONS    = True
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = 'Lax'
# Percayai header X-Forwarded-Proto dari Replit proxy
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'dashboard',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'transkrip_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'dashboard.context_processors.inject_session',
            ],
        },
    },
]

WSGI_APPLICATION = 'transkrip_project.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'id'
TIME_ZONE = 'Asia/Jakarta'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# Neo Feeder config (dari .env)
FEEDER_HOST = os.getenv('FEEDER_HOST', '')
FEEDER_USERNAME = os.getenv('FEEDER_USERNAME', '')
FEEDER_PASSWORD = os.getenv('FEEDER_PASSWORD', '')

# Masukkan MK dari Feeder yang tidak ada di kurikulum ke perhitungan total SKS & IPK
# (True = IPK ikut hitungan MK elektif/MK tak terdokumentasi, False = hanya dicatat)
INCLUDE_MK_TAK_DIKENALI = os.getenv('INCLUDE_MK_TAK_DIKENALI', 'True') == 'True'

# Bila True: saat Neo Feeder tidak punya data nilai untuk suatu NIM, transkrip
# memakai hasil scraping DHS eCampuz (data/ecampuz/out/dhs_<nim>.json) sebagai fallback.
FALLBACK_DHS_SAAT_FEEDER_KOSONG = os.getenv('FALLBACK_DHS_SAAT_FEEDER_KOSONG', 'True') == 'True'

# Default nama dekan
DEFAULT_NAMA_DEKAN = os.getenv('DEFAULT_NAMA_DEKAN', 'Nama Dekan')
