from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Order, OrderItem

MAX_QUANTITY = 99
MAX_LINES = 50


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(source='menu_item.name', read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'menu_item_id', 'name', 'quantity', 'price', 'kitchen_status']
        read_only_fields = fields


class _TableRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    table_number = serializers.CharField()


class _StatusSummarySerializer(serializers.Serializer):
    Pickup = serializers.IntegerField()
    Processed = serializers.IntegerField()
    Complete = serializers.IntegerField()


class OrderSerializer(serializers.ModelSerializer):
    """An order as the order panel and the KDS need it.

    There is no overall order status. `status_summary` counts the items in each
    documented state, and `is_active` is true while any item is not `Complete`."""

    restaurant_id = serializers.IntegerField(read_only=True)
    waiter_id = serializers.CharField(read_only=True)
    table = serializers.SerializerMethodField()
    is_active = serializers.SerializerMethodField()
    status_summary = serializers.SerializerMethodField()
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_type', 'restaurant_id', 'table', 'waiter_id', 'total_price',
            'created_at', 'is_active', 'status_summary', 'items',
        ]
        read_only_fields = fields

    @extend_schema_field(_TableRefSerializer(allow_null=True))
    def get_table(self, obj):
        if obj.table_id is None:
            return None
        return {'id': obj.table_id, 'table_number': obj.table.table_number}

    def _statuses(self, obj):
        return [item.kitchen_status for item in obj.items.all()]

    @extend_schema_field(_StatusSummarySerializer())
    def get_status_summary(self, obj):
        statuses = self._statuses(obj)
        return {s.value: statuses.count(s.value) for s in OrderItem.KitchenStatus}

    @extend_schema_field(serializers.BooleanField())
    def get_is_active(self, obj):
        return any(s != OrderItem.KitchenStatus.COMPLETE for s in self._statuses(obj))


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

class OrderLineInputSerializer(serializers.Serializer):
    menu_item = serializers.IntegerField(min_value=1, help_text='Menu item id.')
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY)


class OrderCreateSerializer(serializers.Serializer):
    order_type = serializers.ChoiceField(choices=Order.Type.choices)
    table = serializers.IntegerField(
        required=False, allow_null=True, min_value=1,
        help_text='Table id. Required for Dine-In, not allowed for Parcel.',
    )
    items = OrderLineInputSerializer(many=True, allow_empty=False, max_length=MAX_LINES)

    def validate(self, attrs):
        order_type, table = attrs['order_type'], attrs.get('table')
        if order_type == Order.Type.DINE_IN and table is None:
            raise serializers.ValidationError({'table': 'A table is required for Dine-In orders.'})
        if order_type == Order.Type.PARCEL and table is not None:
            raise serializers.ValidationError({'table': 'Parcel orders must not have a table.'})
        ids = [line['menu_item'] for line in attrs['items']]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError(
                {'items': 'Each menu item may appear only once; increase its quantity instead.'})
        return attrs


class OrderFilterSerializer(serializers.Serializer):
    """Query parameters of `GET /orders`."""

    status = serializers.ChoiceField(
        choices=OrderItem.KitchenStatus.choices, required=False,
        help_text='Only orders with at least one item in this state.',
    )
    active = serializers.ChoiceField(
        choices=['true', 'false', 'all'], required=False, default='true',
        help_text='`true` (default): orders with an item not yet Complete. `false`: finished '
                  'orders only. `all`: both. Kitchen and Staff users always get `true`.',
    )
    table = serializers.IntegerField(required=False, min_value=1, help_text='Table id.')
    date_from = serializers.DateField(required=False, help_text='YYYY-MM-DD, inclusive.')
    date_to = serializers.DateField(required=False, help_text='YYYY-MM-DD, inclusive.')
    search = serializers.CharField(
        required=False, max_length=50, help_text='Matches the order id or the table number.')

    def validate(self, attrs):
        start, end = attrs.get('date_from'), attrs.get('date_to')
        if start and end and start > end:
            raise serializers.ValidationError({'date_from': '`date_from` must not be after `date_to`.'})
        return attrs
