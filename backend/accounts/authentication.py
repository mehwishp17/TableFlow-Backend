from rest_framework_simplejwt.authentication import JWTAuthentication

from .services import PresenceService


class ActivityJWTAuthentication(JWTAuthentication):
    """Normal JWT authentication, plus a throttled `last_seen` heartbeat (online status)."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            PresenceService.touch(result[0])
        return result
