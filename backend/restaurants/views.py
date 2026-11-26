from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from menus.views import _E400, _E401, _E403, EnvelopeMixin, _envelope

from .permissions import IsRestaurantAdmin
from .serializers import RestaurantProfileSerializer

_AUTH_NOTE = ' Requires a JWT bearer token.'


class RestaurantProfileView(EnvelopeMixin, APIView):
    permission_classes = [IsRestaurantAdmin]

    @extend_schema(
        tags=['Restaurant'], summary='Get the restaurant profile',
        description='Role: **Admin** only; always the Admin\'s own restaurant. Returns the '
                    'branding fields, the active subscription plan and live counters '
                    '(`users`, `staff`, `kitchen_terminals`, `tables`) counted from the '
                    'database.' + _AUTH_NOTE,
        responses={200: _envelope('RestaurantProfileEnvelope', RestaurantProfileSerializer),
                   401: _E401, 403: _E403},
    )
    def get(self, request):
        return Response(RestaurantProfileSerializer(request.user.restaurant).data)

    @extend_schema(
        tags=['Restaurant'], summary='Update the restaurant profile',
        description='Role: **Admin** only. Send any of `name`, `address`, `logo_url`. The '
                    'plan (`subscription_plan`), counters, id and creation date are read only '
                    'and ignored if sent.' + _AUTH_NOTE,
        request=RestaurantProfileSerializer,
        responses={200: _envelope('RestaurantProfileUpdatedEnvelope',
                                  RestaurantProfileSerializer),
                   400: _E400, 401: _E401, 403: _E403},
    )
    def patch(self, request):
        serializer = RestaurantProfileSerializer(
            request.user.restaurant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
