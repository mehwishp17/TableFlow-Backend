from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from menus.views import _E400, _E401, _E403, EnvelopeMixin, _envelope

from .models import PrinterSettings
from .permissions import CanUsePrinterSettings
from .serializers import PrinterSettingsSerializer

_AUTH_NOTE = ' Requires a JWT bearer token.'
_LOCAL_NOTE = (
    ' The server only STORES this text; it never opens or checks the folder, which lives on '
    'the restaurant\'s own computer. The local `print.py` script reads it from here (see the '
    'README, "Printer settings and print.py").'
)


class PrinterSettingsView(EnvelopeMixin, APIView):
    permission_classes = [CanUsePrinterSettings]

    @extend_schema(
        tags=['Restaurant'], summary='Get the printer settings',
        description='Roles: **Admin**, and **Kitchen** (read only, so `print.py` can fetch the '
                    'folder). Always the caller\'s own restaurant. A restaurant that has '
                    'never saved a path gets `folder_path: ""` and `is_configured: false`.'
                    + _LOCAL_NOTE + _AUTH_NOTE,
        responses={200: _envelope('PrinterSettingsEnvelope', PrinterSettingsSerializer),
                   401: _E401, 403: _E403},
    )
    def get(self, request):
        settings = PrinterSettings.objects.filter(restaurant_id=request.user.restaurant_id).first()
        if settings is None:
            return Response({'folder_path': '', 'is_configured': False, 'updated_at': None})
        return Response(PrinterSettingsSerializer(settings).data)

    @extend_schema(
        tags=['Restaurant'], summary='Save the printer settings',
        description='Role: **Admin** only. Saves the local folder path of the restaurant. '
                    'The path must be absolute (Windows `C:\\...`, UNC `\\\\server\\share\\...` '
                    'or POSIX `/...`), at most 260 characters, without `..`, control '
                    'characters or `< > " | ? *`. An empty string clears the setting.'
                    + _LOCAL_NOTE + _AUTH_NOTE,
        request=PrinterSettingsSerializer,
        responses={200: _envelope('PrinterSettingsSavedEnvelope', PrinterSettingsSerializer),
                   400: _E400, 401: _E401, 403: _E403},
    )
    def patch(self, request):
        settings, _ = PrinterSettings.objects.get_or_create(restaurant_id=request.user.restaurant_id)
        serializer = PrinterSettingsSerializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
