from django.contrib import admin

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    raw_id_fields = ('menu_item',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'restaurant', 'order_type', 'table', 'waiter', 'total_price', 'created_at')
    list_filter = ('order_type', 'restaurant')
    search_fields = ('id',)
    raw_id_fields = ('table', 'waiter')
    inlines = [OrderItemInline]
