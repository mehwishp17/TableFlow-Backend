from django.contrib.auth import authenticate
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import LoginSerializer
from .services import PresenceService


def success(data, http_status=status.HTTP_200_OK):
    """Standard envelope (PDF section 31): { data, error, status }."""
    return Response({'data': data, 'error': None, 'status': 'success'}, http_status)


def failure(code, message, http_status, details=None):
    error = {'code': code, 'message': message}
    if details is not None:
        error['details'] = details
    return Response({'data': None, 'error': error, 'status': 'error'}, http_status)


def _error_example(name, code, message, http_status):
    return OpenApiExample(
        name,
        value={
            'data': None,
            'error': {'code': code, 'message': message},
            'status': 'error',
        },
        response_only=True,
        status_codes=[str(http_status)],
    )


@extend_schema(
    tags=['Auth'],
    summary='Log in and obtain JWT tokens',
    description=(
        '**Public endpoint** (no token required). Send `id`, `password` and '
        '`role` (`Admin`, `Staff`, `Kitchen` or `Platform`) in a POST body. On '
        'success it returns a JWT **access** token (15 minutes) and a **refresh** '
        'token (7 days) inside the standard `{ data, error, status }` envelope. '
        'The role must match the role stored on the account.'
    ),
    request=LoginSerializer,
    responses={
        200: OpenApiResponse(
            description='Login successful; JWT access and refresh tokens returned.',
            examples=[
                OpenApiExample(
                    'Successful login',
                    value={
                        'data': {
                            'id': 'admin001',
                            'role': 'Admin',
                            'access': '<jwt-access-token>',
                            'refresh': '<jwt-refresh-token>',
                        },
                        'error': None,
                        'status': 'success',
                    },
                    response_only=True,
                    status_codes=['200'],
                )
            ],
        ),
        400: OpenApiResponse(
            description='Missing or malformed fields.',
            examples=[
                OpenApiExample(
                    'Validation error',
                    value={
                        'data': None,
                        'error': {
                            'code': 'validation_error',
                            'message': 'Invalid request data.',
                            'details': {'role': ['This field is required.']},
                        },
                        'status': 'error',
                    },
                    response_only=True,
                    status_codes=['400'],
                )
            ],
        ),
        401: OpenApiResponse(
            description='Invalid credentials.',
            examples=[
                _error_example(
                    'Invalid credentials',
                    'invalid_credentials',
                    'Invalid ID or password.',
                    401,
                )
            ],
        ),
        403: OpenApiResponse(
            description='Credentials are valid but the role does not match the account.',
            examples=[
                _error_example(
                    'Role mismatch',
                    'role_mismatch',
                    'The selected role does not match this account.',
                    403,
                )
            ],
        ),
    },
    examples=[
        OpenApiExample(
            'Login request',
            value={'id': 'admin001', 'password': 'your-password', 'role': 'Admin'},
            request_only=True,
        )
    ],
)
class LoginView(APIView):
    """POST /api/v1/auth/login: the only public endpoint in this app."""

    authentication_classes = []  # no token is needed (or looked at) to log in
    permission_classes = [AllowAny]  # overrides the project-wide IsAuthenticated

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return failure(
                'validation_error',
                'Invalid request data.',
                status.HTTP_400_BAD_REQUEST,
                details=serializer.errors,
            )
        credentials = serializer.validated_data

        # Checks the password hash and rejects inactive users. Returns None on
        # failure. The same message is used for "no such user" and "wrong
        # password" so attackers cannot discover which IDs exist.
        user = authenticate(
            request, id=credentials['id'], password=credentials['password']
        )
        if user is None:
            return failure(
                'invalid_credentials',
                'Invalid ID or password.',
                status.HTTP_401_UNAUTHORIZED,
            )

        # The role in the token comes from the database, never from the request.
        if user.role != credentials['role']:
            return failure(
                'role_mismatch',
                'The selected role does not match this account.',
                status.HTTP_403_FORBIDDEN,
            )

        PresenceService.mark_online(user)  # login: the user now counts as online

        refresh = RefreshToken.for_user(user)
        refresh['role'] = user.role  # custom claim; the access token inherits it
        return success(
            {
                'id': user.id,
                'role': user.role,
                'access': str(refresh.access_token),
                'refresh': str(refresh),
            }
        )


@extend_schema(
    tags=['Auth'],
    summary='Log out (marks the user offline)',
    description=(
        'Any authenticated role. Clears the user\'s online flag, so the user disappears from '
        '`GET /api/v1/staff/online/` immediately. Tokens are stateless JWTs, so the client '
        'must also discard its access and refresh tokens. Requires a JWT bearer token.'
    ),
    request=None,
    responses={
        200: OpenApiResponse(
            description='Logged out.',
            examples=[
                OpenApiExample(
                    'Logged out',
                    value={'data': {'logged_out': True}, 'error': None, 'status': 'success'},
                    response_only=True,
                    status_codes=['200'],
                )
            ],
        ),
        401: OpenApiResponse(description='Missing, invalid or expired JWT access token.'),
    },
)
class LogoutView(APIView):
    """POST /api/v1/auth/logout"""

    def post(self, request):
        PresenceService.mark_offline(request.user)
        return success({'logged_out': True})
