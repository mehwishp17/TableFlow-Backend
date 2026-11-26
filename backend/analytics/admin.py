from django.contrib import admin

from .models import ScoreEvent


@admin.register(ScoreEvent)
class ScoreEventAdmin(admin.ModelAdmin):
    """The score ledger is append-only: it can be inspected here but never edited."""

    list_display = ('id', 'restaurant', 'waiter', 'kind', 'points', 'order', 'call', 'created_at')
    list_filter = ('kind',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
