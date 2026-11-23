from rest_framework import serializers

from orders.models import OrderItem


class ItemStatusSerializer(serializers.Serializer):
    kitchen_status = serializers.ChoiceField(choices=OrderItem.KitchenStatus.choices)
