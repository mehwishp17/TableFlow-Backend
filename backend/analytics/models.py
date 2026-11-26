from django.db import models
from django.db.models import Q


class ScoreEvent(models.Model):
    """One credit in the waiter performance ledger (PDF section 33: performance score logs).

    The ledger is append-only and idempotent: the unique constraints below allow each
    kind of credit to exist at most once per order or per call, so retries, double
    clicks and race conditions can never score the same event twice. A waiter's score
    is always the sum of their ledger rows; there is no editable "score" column.
    """

    class Kind(models.TextChoices):
        DINE_IN_ORDER = 'DINE_IN_ORDER', 'Dine-In order'
        CALL_RESOLVED = 'CALL_RESOLVED', 'Call resolved'
        QUICK_PICK_BONUS = 'QUICK_PICK_BONUS', 'Call picked within the bonus window'

    restaurant = models.ForeignKey(
        'restaurants.Restaurant', on_delete=models.PROTECT, related_name='score_events')
    waiter = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, related_name='score_events')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    points = models.PositiveSmallIntegerField()
    order = models.ForeignKey(
        'orders.Order', on_delete=models.PROTECT, null=True, blank=True,
        related_name='score_events')
    call = models.ForeignKey(
        'kitchen.StaffCall', on_delete=models.PROTECT, null=True, blank=True,
        related_name='score_events')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        constraints = [
            # At most one credit of each kind per order / per call.
            models.UniqueConstraint(
                fields=['kind', 'order'], condition=Q(order__isnull=False),
                name='scoreevent_one_per_kind_and_order'),
            models.UniqueConstraint(
                fields=['kind', 'call'], condition=Q(call__isnull=False),
                name='scoreevent_one_per_kind_and_call'),
            # Order credits point at an order, call credits at a call.
            models.CheckConstraint(
                condition=(
                    Q(kind='DINE_IN_ORDER', order__isnull=False, call__isnull=True)
                    | Q(kind__in=['CALL_RESOLVED', 'QUICK_PICK_BONUS'],
                        order__isnull=True, call__isnull=False)
                ),
                name='scoreevent_kind_matches_source'),
        ]
        indexes = [
            models.Index(fields=['waiter', 'kind'], name='scoreevent_waiter_kind_idx'),
        ]

    def __str__(self):
        return f'{self.waiter_id} +{self.points} ({self.kind})'
