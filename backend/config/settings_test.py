"""Settings for running tests WITHOUT PostgreSQL.

Usage: python manage.py test --settings=config.settings_test

Uses an in-memory SQLite database and dummy env values. This does NOT verify
PostgreSQL behaviour; run the normal settings against Postgres before release.
"""
import os

os.environ.setdefault('DJANGO_SECRET_KEY', 'test-only-not-a-real-secret')
os.environ.setdefault('DATABASE_NAME', 'unused')
os.environ.setdefault('DATABASE_USER', 'unused')
os.environ.setdefault('DATABASE_PASSWORD', 'unused')

from .settings import *  # noqa: E402,F401,F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
