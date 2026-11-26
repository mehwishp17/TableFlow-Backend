from django.contrib import admin

from .models import StaffCall


@admin.register(StaffCall)
class StaffCallAdmin(admin.ModelAdmin):
    """Read-only overview: calls are created and moved through their lifecycle by the API
    only, so an administrator cannot accidentally break the status/score rules here."""

    list_display = ('id', 'restaurant', 'table', 'source', 'status', 'assigned_waiter',
                    'triggered_at', 'picked_at', 'resolved_at')
    list_filter = ('status', 'source')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
