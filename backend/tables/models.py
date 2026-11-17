import uuid

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower

# Flexible alphanumeric labels such as "1", "T1", "A-2", "VIP_3", "2.1".
table_number_validator = RegexValidator(
    regex=r'^[A-Za-z0-9][A-Za-z0-9._-]*$',
    message='Use letters, digits, ".", "_" or "-" only (e.g. "T1", "A-2", "12"); no spaces.',
)


class Table(models.Model):
    """A physical table of a restaurant (PDF section 34, `tables` table)."""

    class Type(models.TextChoices):
        INDOOR = 'Indoor', 'Indoor'
        OUTDOOR = 'Outdoor', 'Outdoor'
        VIP = 'VIP', 'VIP'
        BAR = 'Bar', 'Bar'

    class Status(models.TextChoices):
        AVAILABLE = 'Available', 'Available'
        OCCUPIED = 'Occupied', 'Occupied'
        RESERVED = 'Reserved', 'Reserved'
        CLEANING = 'Cleaning', 'Cleaning'

    restaurant = models.ForeignKey(
        'restaurants.Restaurant', on_delete=models.PROTECT, related_name='tables'
    )
    table_number = models.CharField(max_length=20, validators=[table_number_validator])
    table_type = models.CharField(max_length=20, choices=Type.choices, default=Type.INDOOR)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    capacity = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)], help_text='Number of seats.'
    )
    # Unguessable public identifier, used only by the QR resolver endpoint.
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['restaurant_id', 'table_number']
        constraints = [
            # Case-insensitive, so "t1" and "T1" cannot both exist in one restaurant.
            models.UniqueConstraint(
                'restaurant', Lower('table_number'),
                name='unique_table_number_per_restaurant',
            ),
            models.CheckConstraint(condition=models.Q(capacity__gte=1), name='table_capacity_gte_1'),
        ]
        indexes = [
            models.Index(fields=['restaurant', 'status'], name='table_restaurant_status_idx'),
        ]

    def __str__(self):
        return f'Table {self.table_number} ({self.restaurant_id})'
