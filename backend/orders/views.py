from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

# Reuse the Menu module's envelope so every API answers in { data, error, status }.
from menus.views import _E400, _E401, _E403, EnvelopeMixin, _envelope

from .permissions import CanAccessOrders
from .serializers import OrderCreateSerializer, OrderFilterSerializer, OrderSerializer
from .services import OrderService

_AUTH_NOTE = ' Requires a JWT bearer token.'


class OrderListCreateView(EnvelopeMixin, APIView):
    permission_classes = [CanAccessOrders]

    @extend_schema(
        tags=['Orders'], summary='List live orders',
        description=(
            'Roles and what they see: **Admin** all orders of the restaurant; **Staff** all '
            'active orders of their own restaurant, whoever created them (never finished ones); **Kitchen** the active '
            'queue of the restaurant, oldest first; **Platform** orders of all restaurants '
            '(read only).\n\n'
            '**Status:** there is no overall order status. Every order has `is_active` (true '
            'while at least one item is not `Complete`) and `status_summary` (number of items '
            'in `Pickup`, `Processed` and `Complete`). `status=` returns orders with at least '
            'one item in that state. By default only active orders are listed; use '
            '`active=false` for finished orders or `active=all` for both. Dates are inclusive '
            'and use the server time zone (UTC).' + _AUTH_NOTE),
        parameters=[OrderFilterSerializer],
        responses={200: _envelope('OrderListEnvelope', OrderSerializer, many=True),
                   400: _E400, 401: _E401, 403: _E403},
    )
    def get(self, request):
        filters = OrderFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        orders = OrderService.list_orders(request.user, **filters.validated_data)
        return Response(OrderSerializer(orders, many=True, context={'request': request}).data)

    @extend_schema(
        tags=['Orders'], summary='Create a Dine-In or Parcel order',
        description=(
            'Roles: Admin, Staff. The restaurant and the waiter are taken from the logged-in '
            'user. A **Dine-In** order needs a `table` of the same restaurant; a **Parcel** '
            'order must not have one. Dishes must exist in the restaurant and be available. '
            'The unit price is copied from the menu at this moment and `total_price` is the '
            'sum of price x quantity. The table status is not changed.' + _AUTH_NOTE),
        request=OrderCreateSerializer,
        responses={201: _envelope('OrderCreatedEnvelope', OrderSerializer),
                   400: _E400, 401: _E401, 403: _E403},
    )
    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        order = OrderService.create_order(
            request.user, data['order_type'], data.get('table'), data['items'])
        return Response(
            OrderSerializer(order, context={'request': request}).data,
            status=status.HTTP_201_CREATED)
