from pathlib import Path
from datetime import timedelta
import os
import dj_database_url 

# Build paths 
BASE_DIR = Path(__file__).resolve().parent.parent

# ==========================================
# SECURITY SETTINGS
# ==========================================


SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-local-development-key"
)

DEBUG = os.environ.get("DEBUG", "False") == "True"

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

CSRF_TRUSTED_ORIGINS = ['https://*.onrender.com']


# ==========================================
# APPLICATIONS
# ==========================================

INSTALLED_APPS = [
    # 'jazzmin', 
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party Apps
    'rest_framework',
    'rest_framework.authtoken',
    'rest_framework_simplejwt',
    'django_filters',
    'corsheaders',
    'drf_yasg',
    'import_export',

    # Local Apps
    'user_api',
    'academic',
]


# ==========================================
# MIDDLEWARE
# ==========================================

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    

    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware', # CORS Middleware

    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


ROOT_URLCONF = 'Class_Routine.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'Class_Routine.wsgi.application'


# ==========================================
# DATABASE
# ==========================================

# DATABASES = {
#     'default': dj_database_url.config(
#         default='postgres://mdrashedulislamrabby:168315@localhost:5432/routine_project',
#         conn_max_age=600
#     )
# }

import os
import dj_database_url

DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get('DATABASE_URL', 'postgresql://routinedb_g90u_user:ByyB7O9HyGS0OhvRduKyG1uLbvnX7pjc@dpg-d9hss9naqgkc73c3f6s0-a.oregon-postgres.render.com/routinedb_g90u'),
        conn_max_age=600
    )
}









# ==========================================
# PASSWORD VALIDATION
# ==========================================

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# ==========================================
# INTERNATIONALIZATION
# ==========================================

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# ==========================================
# STATIC & MEDIA FILES
# ==========================================
# DEBUG = os.environ.get('DEBUG', 'True') == 'True'
DEBUG = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'


STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ==========================================
# CORS
# ==========================================


ALLOWED_HOSTS = ['routine-nvxe.onrender.com', 'localhost', '127.0.0.1']

CSRF_TRUSTED_ORIGINS = [
    'https://routine-nvxe.onrender.com'
]

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    # Frontend URL
    "https://automated-university-routine.vercel.app"
]

# CORS_ALLOW_ALL_ORIGINS = True


# ==========================================
# DRF & JWT SETTINGS
# ==========================================

REST_FRAMEWORK = {
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
    ),
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}


ACCOUNT_SESSION_REMEMBER = True

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
}


# ==========================================
# CUSTOM USER MODEL
# ==========================================

AUTH_USER_MODEL = 'user_api.User'


# ==========================================
# IMPORT EXPORT SETTINGS
# ==========================================

IMPORT_EXPORT_USE_TRANSACTIONS = True
IMPORT_EXPORT_SKIP_ADMIN_LOG = False



SWAGGER_SETTINGS = {
    'SECURITY_DEFINITIONS': {
        'Bearer': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header',
            'description': "Format: Bearer <your_jwt_access_token>"
        }
    },
    'USE_SESSION_AUTH': False, # Session auth disable 
}


STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}




