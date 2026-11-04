from rest_framework import serializers

from .models import User


class LoginSerializer(serializers.Serializer):
    """Validates the *shape* of the login request body. It does not check the
    password; the view does that."""

    id = serializers.CharField(max_length=50)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    role = serializers.ChoiceField(choices=User.Role.values)
