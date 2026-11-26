from django.core.validators import URLValidator
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Restaurant
from .services import RestaurantService


class _CountersSerializer(serializers.Serializer):
    users = serializers.IntegerField(help_text='Active accounts of every role.')
    staff = serializers.IntegerField(help_text='Active Staff (waiter) accounts.')
    kitchen_terminals = serializers.IntegerField(help_text='Active Kitchen accounts.')
    tables = serializers.IntegerField(help_text='Tables of the restaurant.')


class RestaurantProfileSerializer(serializers.ModelSerializer):
    """The restaurant's own profile. `subscription_plan` and the counters are read only:
    the plan is managed by the Platform and the counters are counted from real records."""

    logo_url = serializers.CharField(
        max_length=200, required=False, allow_blank=True,
        validators=[URLValidator(schemes=['http', 'https'])],
        help_text='http(s) link to the logo image (uploading files is not supported).',
    )
    counters = serializers.SerializerMethodField()

    class Meta:
        model = Restaurant
        fields = ['id', 'name', 'address', 'logo_url', 'subscription_plan',
                  'created_at', 'counters']
        read_only_fields = ['id', 'subscription_plan', 'created_at', 'counters']
        extra_kwargs = {'name': {'required': False}}

    @extend_schema_field(_CountersSerializer())
    def get_counters(self, obj):
        return RestaurantService.counters(obj)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('The name cannot be blank.')
        return value
