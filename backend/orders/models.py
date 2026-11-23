from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class Order(models.Model):
    """A Dine-In or Parcel order (PDF section 34, `orders` table).

    `restaurant` is NOT listed in section 34. It was added on purpose so that every
    order, including Parcel orders that have no table, belongs to exactly one tenant.

    There is no overall status column: the preparation state of an order is derived
    from its items' `kitchen_status` (see `OrderQuerySet.with_activity`). An order is
    *active* while at least one of its items is not `Complete`.
    """

    class Type(models.TextChoices):
        DINE_IN = 'Dine-In', 'Dine-In'
        PARCEL = 'Parcel', 'Parcel'

    # Public identifier, e.g. "ORD-7K3F9Q2X" (see orders.services.generate_order_id).
    id = models.CharField(max_length=12, primary_key=True, editable=False)
    restaurant = models.ForeignKey(
        'restaurants.Restaurant', on_delete=models.PROTECT, related_name='orders'
    )
    # Required for Dine-In, must be empty for Parcel (enforced by a constraint below).
    table = models.ForeignKey(
        'tables.Table', on_delete=models.PROTECT, null=True, blank=True,
        related_name='orders',
    )
    waiter = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, related_name='orders',
        help_text='The Admin or Staff user who created the order.',
    )
    order_type = models.CharField(max_length=10, choices=Type.choices)
    total_price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(order_type='Dine-In', table__isnull=False)
                    | Q(order_type='Parcel', table__isnull=True)
                ),
                name='order_table_matches_type',
            ),
            models.CheckConstraint(
                condition=Q(total_price__gte=0), name='order_total_non_negative'
            ),
        ]
        indexes = [
            models.Index(fields=['restaurant', 'created_at'], name='order_rest_created_idx'),
            models.Index(fields=['waiter', 'created_at'], name='order_waiter_created_idx'),
        ]

    def __str__(self):
        return self.id


class OrderItem(models.Model):
    """One dish line of an order (PDF section 34, `order_items` table).

    `price` is the UNIT price at the time the order was created, so later menu price
    changes never alter past orders. The restaurant is reached through `order`."""

    class KitchenStatus(models.TextChoices):
        PICKUP = 'Pickup', 'Pickup'
        PROCESSED = 'Processed', 'Processed'
        COMPLETE = 'Complete', 'Complete'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(
        'menus.MenuItem', on_delete=models.PROTECT, related_name='order_items'
    )
    quantity = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)],
        help_text='Unit price when the order was placed.',
    )
    kitchen_status = models.CharField(
        max_length=10, choices=KitchenStatus.choices, default=KitchenStatus.PICKUP,
        db_index=True,
    )

    class Meta:
        ordering = ['id']
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name='order_item_quantity_gte_1'),
            models.CheckConstraint(condition=Q(price__gte=0), name='order_item_price_non_negative'),
            models.UniqueConstraint(
                fields=['order', 'menu_item'], name='unique_menu_item_per_order'
            ),
        ]

    def __str__(self):
        return f'{self.quantity} x {self.menu_item_id} ({self.order_id})'
