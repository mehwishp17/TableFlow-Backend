from django.apps import AppConfig
from django.core import checks


def check_frontend_base_url(app_configs, **kwargs):
    """Printed QR codes embed FRONTEND_BASE_URL, so a wrong value in production
    produces QR codes that point at localhost and must be reprinted."""
    from django.conf import settings

    url = settings.FRONTEND_BASE_URL.strip()
    problems = []
    if not url.startswith(('http://', 'https://')):
        problems.append(checks.Error(
            'FRONTEND_BASE_URL must start with http:// or https://.',
            hint='Set FRONTEND_BASE_URL in the environment, e.g. https://app.example.com',
            id='tables.E001',
        ))
    elif not settings.DEBUG and ('localhost' in url or '127.0.0.1' in url):
        problems.append(checks.Warning(
            'FRONTEND_BASE_URL points at localhost while DEBUG is off; table QR codes '
            'would only work on this machine.',
            hint='Set FRONTEND_BASE_URL to the public frontend URL before exporting QR PDFs.',
            id='tables.W001',
        ))
    return problems


class TablesConfig(AppConfig):
    name = 'tables'

    def ready(self):
        checks.register(check_frontend_base_url)
