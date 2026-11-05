from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Category, MenuItem


def _user_restaurant_id(serializer):
    request = serializer.context.get('request')
    user = getattr(request, 'user', None)
    return getattr(user, 'restaurant_id', None)


class CategorySerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.none(),
        allow_null=True,
        required=False,
        help_text='ID of the parent category; set it to create a sub-category.',
    )

    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'parent', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only the caller's own categories can be chosen as parent.
        restaurant_id = _user_restaurant_id(self)
        if restaurant_id is not None:
            self.fields['parent'].queryset = Category.objects.filter(
                restaurant_id=restaurant_id, parent__isnull=True
            )

    def validate(self, attrs):
        instance = self.instance
        restaurant_id = instance.restaurant_id if instance else _user_restaurant_id(self)
        name = attrs.get('name', instance.name if instance else None)
        parent = attrs['parent'] if 'parent' in attrs else (instance.parent if instance else None)

        if instance is not None:
            if parent is not None and parent.pk == instance.pk:
                raise serializers.ValidationError({'parent': 'A category cannot be its own parent.'})
            if parent is not None and instance.sub_categories.exists():
                raise serializers.ValidationError(
                    {'parent': 'A category that has sub-categories cannot become a sub-category.'}
                )
            parent_changed = parent != instance.parent
            if parent_changed and instance.sub_category_items.exists():
                raise serializers.ValidationError(
                    {'parent': 'Cannot move a sub-category that is used by menu items.'}
                )

        duplicates = Category.objects.filter(
            restaurant_id=restaurant_id, parent=parent, name__iexact=name
        )
        if instance is not None:
            duplicates = duplicates.exclude(pk=instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError(
                {'name': 'A category with this name already exists here.'}
            )
        return attrs


class MenuItemSerializer(serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.none())
    sub_category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.none(), allow_null=True, required=False
    )
    category_name = serializers.CharField(source='category.name', read_only=True)
    sub_category_name = serializers.CharField(
        source='sub_category.name', read_only=True, default=None
    )
    is_orderable = serializers.BooleanField(read_only=True)

    class Meta:
        model = MenuItem
        fields = [
            'id', 'name', 'description', 'price', 'category', 'category_name',
            'sub_category', 'sub_category_name', 'dish_type', 'ingredients',
            'calories', 'portion_size', 'image_url', 'is_available',
            'is_orderable', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        restaurant_id = _user_restaurant_id(self)
        if restaurant_id is not None:
            own = Category.objects.filter(restaurant_id=restaurant_id)
            self.fields['category'].queryset = own.filter(parent__isnull=True)
            self.fields['sub_category'].queryset = own.filter(parent__isnull=False)

    def validate(self, attrs):
        instance = self.instance
        category = attrs.get('category', instance.category if instance else None)
        sub_category = (
            attrs['sub_category'] if 'sub_category' in attrs
            else (instance.sub_category if instance else None)
        )
        if sub_category is not None and sub_category.parent_id != category.pk:
            raise serializers.ValidationError(
                {'sub_category': 'Sub-category must belong to the selected category.'}
            )
        name = attrs.get('name', instance.name if instance else None)
        duplicates = MenuItem.objects.filter(category=category, name__iexact=name)
        if instance is not None:
            duplicates = duplicates.exclude(pk=instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError(
                {'name': 'A dish with this name already exists in this category.'}
            )
        return attrs


class StrictBooleanField(serializers.BooleanField):
    """Accepts only JSON true/false (DRF's default also takes "yes", "1", ...)."""

    def to_internal_value(self, data):
        if not isinstance(data, bool):
            self.fail('invalid', input=data)
        return data


class AvailabilitySerializer(serializers.Serializer):
    is_available = StrictBooleanField()


class CatalogSubCategorySerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'items']

    @extend_schema_field(MenuItemSerializer(many=True))
    def get_items(self, obj):
        return MenuItemSerializer(obj.sub_category_items.all(), many=True).data


class CatalogCategorySerializer(serializers.ModelSerializer):
    sub_categories = CatalogSubCategorySerializer(many=True, read_only=True)
    items = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'items', 'sub_categories']

    @extend_schema_field(MenuItemSerializer(many=True))
    def get_items(self, obj):
        # Items placed directly in the category (not in one of its sub-categories).
        direct = [i for i in obj.items.all() if i.sub_category_id is None]
        return MenuItemSerializer(direct, many=True).data
