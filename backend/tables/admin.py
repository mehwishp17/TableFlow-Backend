from django.contrib import admin

from .models import Table


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ('table_number', 'restaurant', 'table_type', 'status', 'capacity')
    list_filter = ('restaurant', 'status', 'table_type')
    search_fields = ('table_number',)
    readonly_fields = ('qr_token', 'created_at', 'updated_at')
