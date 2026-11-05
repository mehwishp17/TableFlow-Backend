from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class Category(models.Model):
    """Menu category. A category with a `parent` is a sub-category (one level
    deep only). Scoped to a single restaurant."""

    restaurant = models.ForeignKey(
        'restaurants.Restaurant',
        on_delete=models.PROTECT,
        related_name='menu_categories',
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sub_categories',
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'categories'
        constraints = [
            # Two constraints because NULL parents never compare equal in SQL.
            models.UniqueConstraint(
                fields=['restaurant', 'parent', 'name'],
                name='unique_subcategory_name_per_parent',
            ),
            models.UniqueConstraint(
                fields=['restaurant', 'name'],
                condition=Q(parent__isnull=True),
                name='unique_top_category_name_per_restaurant',
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.parent_id is not None:
            if self.parent_id == self.pk:
                raise ValidationError({'parent': 'A category cannot be its own parent.'})
            if self.parent.restaurant_id != self.restaurant_id:
                raise ValidationError({'parent': 'Parent belongs to another restaurant.'})
            if self.parent.parent_id is not None:
                raise ValidationError({'parent': 'Sub-categories cannot be nested further.'})


class MenuItem(models.Model):
    """A dish (PDF section 34, `menu_items`). The restaurant is reached through
    `category.restaurant`, so an item cannot disagree with its category."""

    class DishType(models.TextChoices):
        VEG = 'Veg', 'Veg'
        NON_VEG = 'Non-Veg', 'Non-Veg'

    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name='items'
    )
    sub_category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sub_category_items',
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    dish_type = models.CharField(max_length=10, choices=DishType.choices)
    ingredients = models.TextField(blank=True)
    calories = models.PositiveIntegerField(null=True, blank=True)
    portion_size = models.CharField(max_length=50, blank=True)
    image_url = models.URLField(blank=True)
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=0), name='menu_item_price_non_negative'
            ),
            models.UniqueConstraint(
                fields=['category', 'name'], name='unique_item_name_per_category'
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def restaurant_id(self):
        return self.category.restaurant_id

    @property
    def is_orderable(self):
        """Orders must only accept items for which this is True."""
        return self.is_available

    def clean(self):
        if self.sub_category_id is not None:
            if self.sub_category.parent_id != self.category_id:
                raise ValidationError(
                    {'sub_category': 'Sub-category must belong to the selected category.'}
                )
