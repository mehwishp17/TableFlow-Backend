import re

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import User
from .services import PresenceService, StaffService

# These ids would collide with fixed sub-routes of /staff/ (/staff/online/, ...).
RESERVED_STAFF_IDS = {'online', 'me', 'scorecards'}
_ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
MANAGED_ROLE_CHOICES = [(role.value, role.label) for role in StaffService.MANAGED_ROLES]


class StaffSerializer(serializers.ModelSerializer):
    """A Staff or Kitchen account as the Admin sees it. No password, hash or token is
    ever part of the output."""

    is_online = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'role', 'is_active', 'is_online', 'last_seen']
        read_only_fields = fields

    @extend_schema_field(serializers.BooleanField())
    def get_is_online(self, obj):
        return PresenceService.is_online(obj)


class StaffCreateSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=50, help_text='Login id, e.g. "waiter01".')
    password = serializers.CharField(
        write_only=True, trim_whitespace=False, max_length=128,
        style={'input_type': 'password'},
    )
    role = serializers.ChoiceField(choices=MANAGED_ROLE_CHOICES, default=User.Role.STAFF)

    def validate_id(self, value):
        if not _ID_PATTERN.match(value):
            raise serializers.ValidationError(
                'Use letters, digits, ".", "_" or "-" only; no spaces.')
        if value.lower() in RESERVED_STAFF_IDS:
            raise serializers.ValidationError('This id is reserved.')
        return value


class StaffUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=MANAGED_ROLE_CHOICES, required=False)
    is_active = serializers.BooleanField(required=False)
    password = serializers.CharField(
        write_only=True, required=False, trim_whitespace=False, max_length=128,
        style={'input_type': 'password'}, help_text='Sets a new password (Admin reset).',
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                'Send at least one of: role, is_active, password.')
        return attrs
