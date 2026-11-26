from django.db import models
from django.db.models import Q


class StaffCall(models.Model):
    """A waiter call alert (PDF section 16, `staff calls` in section 33).

    Two sources: a **Customer** scanning the table QR code and pressing "call waiter"
    (open to any online waiter of the restaurant), or the **Kitchen** calling one chosen
    online waiter, e.g. for a finished dish.

    Lifecycle: PENDING -> PICKED -> RESOLVED, always forwards. `triggered_at`,
    `picked_at` and `resolved_at` are the timestamps the performance score is based on.

    Exceptions, all driven by the restaurant Admin: an open call can be CANCELLED, a
    PICKED call can be closed on the waiter's behalf (RESOLVED with `closed_by` set, which
    earns no score), and when a waiter is deactivated their PICKED customer calls go back
    to PENDING (their kitchen calls are CANCELLED, since only that waiter could take them).
    """

    class Source(models.TextChoices):
        CUSTOMER = 'Customer', 'Customer'
        KITCHEN = 'Kitchen', 'Kitchen'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'PENDING'
        PICKED = 'PICKED', 'PICKED'
        RESOLVED = 'RESOLVED', 'RESOLVED'
        CANCELLED = 'CANCELLED', 'CANCELLED'

    restaurant = models.ForeignKey(
        'restaurants.Restaurant', on_delete=models.PROTECT, related_name='staff_calls')
    # SET_NULL: deleting a table must not erase call (and therefore score) history.
    table = models.ForeignKey(
        'tables.Table', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_calls')
    order = models.ForeignKey(
        'orders.Order', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='staff_calls', help_text='Kitchen calls only: the order the call is about.')
    source = models.CharField(max_length=10, choices=Source.choices)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, null=True, blank=True,
        related_name='calls_created', help_text='The Kitchen user who made the call.')
    target_waiter = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, null=True, blank=True,
        related_name='calls_targeted',
        help_text='Kitchen calls only: the one waiter who may pick the call.')
    assigned_waiter = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, null=True, blank=True,
        related_name='calls_assigned',
        help_text='The waiter who picked the call (set by the server, never by the client).')
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    triggered_at = models.DateTimeField(auto_now_add=True)
    picked_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, null=True, blank=True,
        related_name='calls_closed',
        help_text='The Admin who cancelled the call, or resolved it for the waiter.')

    class Meta:
        ordering = ['triggered_at', 'id']
        constraints = [
            # Status, assignee and timestamps always agree with each other.
            models.CheckConstraint(
                condition=(
                    Q(status='PENDING', assigned_waiter__isnull=True, picked_at__isnull=True,
                      resolved_at__isnull=True, cancelled_at__isnull=True)
                    | Q(status='PICKED', assigned_waiter__isnull=False,
                        picked_at__isnull=False, resolved_at__isnull=True,
                        cancelled_at__isnull=True)
                    | Q(status='RESOLVED', assigned_waiter__isnull=False,
                        picked_at__isnull=False, resolved_at__isnull=False,
                        cancelled_at__isnull=True)
                    | Q(status='CANCELLED', resolved_at__isnull=True,
                        cancelled_at__isnull=False)
                ),
                name='staffcall_status_matches_fields',
            ),
            # Customer calls have no sender or target; Kitchen calls have both.
            models.CheckConstraint(
                condition=(
                    Q(source='Customer', created_by__isnull=True, target_waiter__isnull=True)
                    | Q(source='Kitchen', created_by__isnull=False, target_waiter__isnull=False)
                ),
                name='staffcall_source_matches_fields',
            ),
            # A table has at most one open (PENDING or PICKED) customer call, so pressing
            # the button repeatedly, or two guests pressing at once, cannot spam waiters.
            models.UniqueConstraint(
                fields=['table'],
                condition=Q(source='Customer', status__in=['PENDING', 'PICKED']),
                name='staffcall_one_open_customer_call_per_table',
            ),
        ]
        indexes = [
            models.Index(fields=['restaurant', 'status', 'triggered_at'],
                         name='staffcall_rest_status_idx'),
        ]

    def __str__(self):
        return f'Call {self.pk} ({self.source}, {self.status})'
