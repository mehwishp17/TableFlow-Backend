"""OpenAPI (Swagger) description of our JWT authentication class.

drf-spectacular only knows `JWTAuthentication` itself, not our subclass
`ActivityJWTAuthentication`. Without this, every protected endpoint would lose its
"Authorize / Bearer token" entry in Swagger. It is the same scheme, under the same name.
"""
from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class ActivityJWTScheme(SimpleJWTScheme):
    target_class = 'accounts.authentication.ActivityJWTAuthentication'
    name = 'jwtAuth'
