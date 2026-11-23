"""Kitchen Display System business logic (PDF section 29: KdsService)."""
from django.db import transaction
from django.shortcuts import get_object_or_404

from orders.models import Order, OrderItem


class KdsService:
    @staticmethod
    def update_item_status(user, order_id, item_id, kitchen_status):
        """Set the preparation status of one item and return its order.

        The item is looked up through its order AND the user's restaurant, so an item
        of another restaurant, or one that belongs to a different order, is a 404.
        Any of the three documented values may be chosen (the KDS shows a dropdown)."""
        with transaction.atomic():
            item = get_object_or_404(
                OrderItem.objects.select_for_update(of=('self',)),
                pk=item_id, order_id=order_id, order__restaurant_id=user.restaurant_id,
            )
            if item.kitchen_status != kitchen_status:
                item.kitchen_status = kitchen_status
                item.save(update_fields=['kitchen_status'])
        return Order.objects.select_related('table').prefetch_related(
            'items__menu_item').get(pk=order_id)
