from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import StaffCall


class _CallTableRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    table_number = serializers.CharField()


class CallSerializer(serializers.ModelSerializer):
    """A call alert as the staff panel, KDS and Admin see it."""

    table = serializers.SerializerMethodField()
    order_id = serializers.CharField(read_only=True, allow_null=True)
    created_by_id = serializers.CharField(read_only=True, allow_null=True)
    target_waiter_id = serializers.CharField(read_only=True, allow_null=True)
    assigned_waiter_id = serializers.CharField(read_only=True, allow_null=True)
    closed_by_id = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = StaffCall
        fields = [
            'id', 'source', 'status', 'table', 'order_id', 'created_by_id', 'target_waiter_id',
            'assigned_waiter_id', 'closed_by_id', 'triggered_at', 'picked_at', 'resolved_at',
            'cancelled_at',
        ]
        read_only_fields = fields

    @extend_schema_field(_CallTableRefSerializer(allow_null=True))
    def get_table(self, obj):
        if obj.table_id is None:
            return None
        return {'id': obj.table_id, 'table_number': obj.table.table_number}


class CustomerCallResultSerializer(serializers.Serializer):
    """What a guest gets back: no staff ids or internal data are ever exposed publicly."""

    id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=StaffCall.Status.choices)
    table_number = serializers.CharField()
    triggered_at = serializers.DateTimeField()


class CustomerCallSerializer(serializers.Serializer):
    token = serializers.CharField(
        max_length=64, help_text='The `token` of the table QR link.')
    restaurant_id = serializers.IntegerField(
        required=False, help_text='Optional: `restaurant_id` of the QR link; must match the token.')
    table = serializers.IntegerField(
        required=False, help_text='Optional: `table` of the QR link; must match the token.')


class KitchenCallSerializer(serializers.Serializer):
    waiter = serializers.CharField(
        max_length=50, help_text='Id of an online waiter (see `GET /api/v1/staff/online/`).')
    order = serializers.CharField(
        required=False, max_length=12, help_text='Order id the call is about, e.g. ORD-7K3F9Q2X.')
    table = serializers.IntegerField(required=False, min_value=1, help_text='Table id.')

    def validate(self, attrs):
        if 'order' not in attrs and 'table' not in attrs:
            raise serializers.ValidationError(
                'Send the `order` or the `table` the call is about.')
        return attrs


CALL_FILTER_STATUSES = ['PENDING', 'PICKED', 'RESOLVED', 'CANCELLED', 'all']


class CallFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=CALL_FILTER_STATUSES, required=False, default='PENDING',
        help_text='`PENDING` (default): alerts waiting for a waiter. `PICKED` / `RESOLVED` / '
                  '`CANCELLED`: calls already taken or closed (Staff see only their own). '
                  '`all`: every status.',
    )
