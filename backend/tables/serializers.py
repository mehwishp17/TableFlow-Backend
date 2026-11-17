from django.db import IntegrityError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Table
from .services import build_menu_url

_DUPLICATE = 'A table with this number already exists in your restaurant.'


class TableSerializer(serializers.ModelSerializer):
    qr_url = serializers.SerializerMethodField(
        help_text='Frontend URL encoded in the QR code of this table.'
    )

    class Meta:
        model = Table
        fields = [
            'id', 'table_number', 'table_type', 'status', 'capacity',
            'qr_url', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'qr_url', 'created_at', 'updated_at']
        # `restaurant` is intentionally absent: it is taken from the logged-in user.

    @extend_schema_field(serializers.URLField())
    def get_qr_url(self, obj):
        return build_menu_url(obj.restaurant_id, obj.pk)

    def validate_table_number(self, value):
        return value.strip()

    def validate(self, attrs):
        instance = self.instance
        request = self.context.get('request')
        restaurant_id = (
            instance.restaurant_id if instance else getattr(request.user, 'restaurant_id', None)
        )
        number = attrs.get('table_number')
        if number is not None:
            duplicates = Table.objects.filter(
                restaurant_id=restaurant_id, table_number__iexact=number
            )
            if instance is not None:
                duplicates = duplicates.exclude(pk=instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError({'table_number': _DUPLICATE})
        return attrs

    def save(self, **kwargs):
        try:
            return super().save(**kwargs)
        except IntegrityError:  # lost a race with a concurrent request
            raise serializers.ValidationError({'table_number': _DUPLICATE})


class TableStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Table.Status.choices)
