from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from menus.views import _E400, _E401, _E403, _E404, EnvelopeMixin, _envelope
from orders.serializers import OrderSerializer

from .permissions import IsKitchenStaff
from .serializers import ItemStatusSerializer
from .services import KdsService


class OrderItemStatusView(EnvelopeMixin, APIView):
    permission_classes = [IsKitchenStaff]

    @extend_schema(
        tags=['Kitchen'], summary='Update the preparation status of one food item',
        description=(
            'Role: **Kitchen** only (Admin has view-only KDS access, Staff and Platform have '
            'none). Sets `kitchen_status` of one item to `Pickup`, `Processed` or `Complete`; '
            'any of the three may be chosen. Returns the whole order so the KDS can refresh '
            'it. The item must belong to the order and the order to the kitchen user\'s '
            'restaurant, otherwise 404. Requires a JWT bearer token.'),
        request=ItemStatusSerializer,
        responses={200: _envelope('OrderItemStatusEnvelope', OrderSerializer),
                   400: _E400, 401: _E401, 403: _E403, 404: _E404},
    )
    def patch(self, request, order_id, item_id):
        serializer = ItemStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = KdsService.update_item_status(
            request.user, order_id, item_id, serializer.validated_data['kitchen_status'])
        return Response(OrderSerializer(order, context={'request': request}).data)
